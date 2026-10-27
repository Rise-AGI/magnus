# back_end/server/_scheduler/_sync.py
import os
import subprocess
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING, Any, Dict, Optional
from .._protocol_files import open_protocol_file
from ..database import SessionLocal
from ..models import Job, JobStatus, ClusterSnapshot
from .._magnus_config import is_local_mode
from .._slurm_manager._resource_query import resource_partitions, validate_allocation_visibility
from . import logger, magnus_workspace_path

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _SyncMixinBase = _SchedulerProtocol
else:
    _SyncMixinBase = object


class _SyncMixin(_SyncMixinBase):
    """状态同步：把 SLURM / Docker 真实状态拉回数据库；周期性写集群快照。"""

    def _record_snapshot(self):
        if is_local_mode:
            return  # local 模式不需要集群快照

        now = datetime.now(timezone.utc)
        if (now - self.last_snapshot_time).total_seconds() < \
            self.config["server"]["scheduler"]["snapshot_interval"]:
            return

        # Failed or incomplete samples must not turn a five-minute query into
        # heartbeat-rate traffic against a shared scheduler.
        self.last_snapshot_time = now
        try:
            # Phase 1 — SLURM 调用（无 session）
            assert self.slurm_manager is not None
            partitions = resource_partitions(self.config)
            capacity = self.slurm_manager.get_node_snapshot(
                partitions = partitions or None,
                _raise_on_error = True,
            )
            tasks = self.slurm_manager.get_all_running_tasks(
                node_names = [node.name for node in capacity.nodes] if partitions else None,
                _raise_on_error = True,
            )
            if any(node.gpu_by_type and node.allocated_gpus is None for node in capacity.nodes):
                logger.warning("Skipped cluster history sample: GPU node allocation is unknown")
                return
            validate_allocation_visibility(
                capacity,
                tasks,
                resource_fields=("gpu_count",),
            )
            if any(not task.get("scope_is_subset", True) or task.get("gpu_count_source") == "per_node_fallback" for task in tasks):
                logger.warning("Skipped cluster history sample: GPU allocations are inferred or cross the selected node scope")
                return
            allocations = {task["id"]: task["gpu_count"] for task in tasks}

            # Phase 2 — 写快照（短 session）
            with SessionLocal() as db:
                # Ownership comes from Slurm IDs, usage from actual allocations.
                # Queued/stale DB state and CG remain owned by Magnus. Select
                # only IDs; historical command blobs are never loaded here.
                owned_ids = {
                    row[0]
                    for row in self._job_query(db, Job.slurm_job_id).filter(
                        Job.slurm_job_id.in_(list(allocations)),
                    ).distinct().all()
                } if allocations else set()
                magnus_usage = sum(allocations[job_id] for job_id in owned_ids)

                snapshot = ClusterSnapshot(
                    execution_target = self.target.id,
                    total_gpus = capacity.total_gpus,
                    slurm_used_gpus = sum(allocations.values()),
                    magnus_used_gpus = magnus_usage,
                    timestamp = now,
                )
                db.add(snapshot)
                db.commit()
                logger.debug(f"Recorded Cluster Snapshot: Total={snapshot.total_gpus}, Used={snapshot.slurm_used_gpus}, Magnus={magnus_usage}")
        except Exception as error:
            logger.error(f"Failed to record cluster snapshot: {error}")

    def _dump_docker_logs(self, job_id: str, container_name: str, since: Optional[str] = None) -> Optional[str]:
        # 与 SLURM 模式的 sbatch --output 共用同一文件，让 jobs.py 读端点不必按模式分支。
        # Capture cursor BEFORE fetching logs to avoid missing lines emitted during the call.
        # Microsecond precision (RFC 3339 with %f) drastically narrows the duplicate window
        # vs second precision: docker logs --since is inclusive, so saving a floor-to-second
        # cursor would re-fetch every line emitted within the same wall-clock second on each tick.
        new_cursor = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            command = [
                "docker",
                "logs",
                container_name,
            ]
            if since:
                command.extend(["--since", since])
            result = subprocess.run(
                command,
                capture_output = True,
                text = True,
            )
            if result.returncode != 0:
                # container 已不存在 / docker 出错，本次跳过
                return since
            output = result.stdout
            if result.stderr:
                output += result.stderr
            if output:
                mode = "ab" if since else "wb"
                with open_protocol_file(f"{magnus_workspace_path}/jobs", f"{job_id}/slurm/output.txt", mode) as stream:
                    stream.write(output.encode("utf-8"))
            return new_cursor
        except Exception as error:
            logger.warning(f"Failed to dump Docker logs for {job_id}: {error}")
            return since

    def _sync_reality(self) -> dict[str, str | None]:
        """同步真实状态到数据库（SLURM 或 Docker）"""
        if is_local_mode:
            self._sync_reality_docker()
            with SessionLocal() as db:
                return dict(self._job_query(db, Job.id, Job.slurm_job_id).filter(
                    Job.finalization_status == "CLEANUP",
                ).all())
        return self._sync_reality_slurm()

    def _docker_job_timed_out(self, job: Job) -> bool:
        """job 是否已超出其 time_limit（分钟）。time_limit 或 start_time 为 None 则不限。
        start_time 从 SQLite 读出可能是 naive（按 UTC 存的），统一补成 aware 再比较。"""
        if job.time_limit is None or job.start_time is None:
            return False
        start = job.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        elapsed_minutes = (datetime.now(timezone.utc) - start).total_seconds() / 60
        return elapsed_minutes > job.time_limit

    def _sync_reality_docker(self):
        """同步 Docker 容器状态到数据库（三阶段模式，与 _sync_reality_slurm 对称）"""
        assert self.docker_manager is not None

        # Phase 1 — 收集 job 信息（短 session）
        with SessionLocal() as db:
            active_info = [
                (job.id, job.status)
                for job in self._job_query(db, Job).filter(
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
                    | ((Job.status == JobStatus.TERMINATED) & Job.slurm_job_id.is_not(None))
                ).all()
            ]

        if not active_info:
            return

        # Phase 2 — Docker 状态检查 + 日志抓取（无 session）
        docker_results: Dict[str, Dict[str, Any]] = {}
        for job_id, db_status in active_info:
            container_name = f"magnus-job-{job_id}"
            try:
                real_status = self.docker_manager.check_container_status(container_name)

                # 增量日志：RUNNING 状态每次心跳抓取
                log_since = self._docker_log_cursors.get(job_id)
                if real_status == "RUNNING" or real_status in ["COMPLETED", "FAILED"]:
                    new_cursor = self._dump_docker_logs(job_id, container_name, since=log_since)
                    self._docker_log_cursors[job_id] = new_cursor

                docker_results[job_id] = {
                    "status": real_status,
                    "container_name": container_name,
                    "db_status": db_status,
                }
            except Exception as error:
                logger.error(f"Failed to check Docker job {job_id}: {error}")

        # Phase 3 — 批量更新（短 session）
        with SessionLocal() as db:
            for job_id, info in docker_results.items():
                try:
                    job = self._job_query(db, Job).filter(Job.id == job_id).first()
                    if not job:
                        continue

                    real_status = info["status"]
                    container_name = info["container_name"]

                    if job.status == JobStatus.TERMINATED:
                        self.terminate_job(db, job)
                        continue

                    if job.status == JobStatus.QUEUED:
                        if real_status == "RUNNING":
                            job.status = JobStatus.RUNNING
                            job.start_time = datetime.now(timezone.utc)
                            logger.info(f"Job {job.id} started running in Docker")
                        elif real_status == "COMPLETED":
                            self._write_success_marker(job.id)
                            self.docker_manager.remove_container(container_name)
                            self._finalize_completed_job(job)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status == "FAILED":
                            logger.warning(f"Job {job.id} failed in Docker")
                            job.status = JobStatus.FAILED
                            term_info = self.docker_manager.get_termination_info(container_name)
                            if term_info.get("oom_killed"):
                                job.result = self._format_oom_message(job.memory_demand)
                            else:
                                job.result = "Container exited with non-zero status while starting"
                            self.docker_manager.remove_container(container_name)
                            job.slurm_job_id = None
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status in ["MISSING"]:
                            logger.warning(f"Job {job.id} container not found while QUEUED")
                            job.status = JobStatus.FAILED
                            job.result = "Container disappeared while queued (may have been removed externally)"
                            self.docker_manager.remove_container(container_name)
                            job.slurm_job_id = None
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)

                    elif job.status == JobStatus.RUNNING:
                        if real_status == "COMPLETED":
                            self._write_success_marker(job.id)
                            self.docker_manager.remove_container(container_name)
                            self._finalize_completed_job(job)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status in ["FAILED", "MISSING"]:
                            logger.warning(f"Job {job.id} failed in Docker (Status: {real_status})")
                            job.status = JobStatus.FAILED
                            # MISSING means the container is gone; OOMKilled is only meaningful
                            # for FAILED (exited non-zero), so only consult inspect there.
                            term_info = (
                                self.docker_manager.get_termination_info(container_name)
                                if real_status == "FAILED" else {"oom_killed": False}
                            )
                            if term_info.get("oom_killed"):
                                job.result = self._format_oom_message(job.memory_demand)
                            else:
                                job.result = f"Container {real_status} during execution"
                            self.docker_manager.remove_container(container_name)
                            job.slurm_job_id = None
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status == "RUNNING" and self._docker_job_timed_out(job):
                            # 对偶 SLURM 的 --time：容器仍在跑但超出 time_limit → kill + FAILED。
                            logger.warning(f"Job {job.id} exceeded time_limit ({job.time_limit} min), terminating")
                            self.docker_manager.stop_container(container_name)
                            self.docker_manager.remove_container(container_name)
                            job.status = JobStatus.FAILED
                            job.result = f"Job exceeded its time limit ({job.time_limit} min)"
                            job.slurm_job_id = None
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)

                    db.commit()
                except Exception as error:
                    db.rollback()
                    logger.error(f"Failed to sync Docker job {job_id}: {error}")

    def _sync_reality_slurm(
        self,
    ) -> dict[str, str | None]:
        with SessionLocal() as db:
            observations = self._job_query(db,
                Job.id,
                Job.status,
                Job.slurm_job_id,
                Job.finalization_status,
            ).filter(
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
                | (
                    Job.status.in_([JobStatus.TERMINATED, JobStatus.PAUSED])
                    & Job.slurm_job_id.isnot(None)
                )
                | Job.finalization_status.isnot(None),
            ).all()

        observed_ids = {row[0] for row in observations}
        self._finalization_retry_after = {
            job_id: deadline
            for job_id, deadline in self._finalization_retry_after.items()
            if job_id in observed_ids
        }
        artifact_candidates = {}
        for job_id, original_status, slurm_job_id, finalization_status in observations:
            try:
                if finalization_status and monotonic() < self._finalization_retry_after.get(job_id, 0):
                    continue
                if finalization_status == "CLEANUP":
                    artifact_candidates[job_id] = slurm_job_id
                    continue
                if not slurm_job_id:
                    with SessionLocal() as db:
                        self._job_query(db, Job).filter(
                            Job.id == job_id,
                            Job.status == original_status,
                            Job.slurm_job_id.is_(None),
                            Job.finalization_status.is_(None),
                        ).update({
                            Job.status: JobStatus.FAILED,
                            Job.result: "Internal error: active job has no scheduler job ID",
                        }, synchronize_session=False)
                        db.commit()
                    continue

                if finalization_status and original_status not in (JobStatus.QUEUED, JobStatus.RUNNING):
                    artifact_candidates[job_id] = slurm_job_id
                    continue

                assert self.slurm_manager is not None
                real_status = finalization_status or self.slurm_manager.check_job_status(slurm_job_id)
                if real_status == "RUNNING":
                    with SessionLocal() as db:
                        self._job_query(db, Job).filter(
                            Job.id == job_id,
                            Job.status == JobStatus.QUEUED,
                            Job.slurm_job_id == slurm_job_id,
                        ).update({
                            Job.status: JobStatus.RUNNING,
                            Job.start_time: datetime.now(timezone.utc),
                        }, synchronize_session=False)
                        db.commit()
                    artifact_candidates[job_id] = slurm_job_id
                elif real_status in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
                    if self._classify_slurm_job(job_id, slurm_job_id, real_status):
                        artifact_candidates[job_id] = slurm_job_id
                        self._finalization_retry_after.pop(job_id, None)
                elif original_status == JobStatus.TERMINATED:
                    artifact_candidates[job_id] = slurm_job_id
            except Exception as error:
                self._finalization_retry_after[job_id] = monotonic() + 60
                logger.warning("Job %s synchronization incomplete; retaining workspace (%s)", job_id, type(error).__name__)
        return artifact_candidates

    def _classify_slurm_job(
        self,
        job_id: str,
        slurm_job_id: str,
        real_status: str,
    ) -> bool:
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status.is_(None) | (Job.finalization_status == real_status),
            ).update({Job.finalization_status: real_status}, synchronize_session=False)
            db.commit()
            current = self._job_query(db, Job.status).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status == real_status,
            ).first()
        if not changed or current is None:
            return False
        if current[0] not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return True

        # Classify only after the small marker read. A failed connection is not
        # evidence of a missing success marker; custody transfer is independent.
        self._stage_out_markers(job_id)
        with SessionLocal() as db:
            job = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status == real_status,
            ).first()
            if job is None:
                return False
            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                original_status = job.status
                marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
                if real_status == "COMPLETED" or os.path.exists(marker_path):
                    self._finalize_completed_job(job, cleanup=False)
                else:
                    job.status = JobStatus.FAILED
                    if self._has_oom_marker(job.id):
                        job.result = self._format_oom_message(job.memory_demand)
                    else:
                        job.result = f"Scheduler reported {real_status} during execution"
                values = {Job.status: job.status, Job.result: job.result, Job.action: job.action}
                db.expunge(job)
                self._job_query(db, Job).filter(
                    Job.id == job_id,
                    Job.status == original_status,
                    Job.slurm_job_id == slurm_job_id,
                    Job.finalization_status == real_status,
                ).update(values, synchronize_session=False)
            db.commit()
        return True

    def _recover_job_artifacts(
        self,
        job_id: str,
        slurm_job_id: str | None,
    ) -> int:
        with SessionLocal() as db:
            current = self._job_query(db, Job.status, Job.finalization_status).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
            ).first()
        if current is None:
            return 0
        status, finalization_status = current
        if finalization_status == "CLEANUP":
            self._finish_job_cleanup(job_id, slurm_job_id)
            return 60
        if finalization_status:
            # Classification owns marker reads. Bulk recovery must not race it.
            if status in (JobStatus.QUEUED, JobStatus.RUNNING):
                return 0
            # Cancellation/preemption sets status before Slurm exits, bypassing
            # classification's marker read. Recover these files before cleanup.
            if status in (JobStatus.TERMINATED, JobStatus.PAUSED):
                self._stage_out_markers(job_id)
            self._finalize_slurm_job(job_id, slurm_job_id, finalization_status)
            return 60
        if status == JobStatus.TERMINATED and slurm_job_id is not None:
            with SessionLocal() as db:
                job = self._job_query(db, Job).filter(
                    Job.id == job_id,
                    Job.status == JobStatus.TERMINATED,
                    Job.slurm_job_id == slurm_job_id,
                    Job.finalization_status.is_(None),
                ).first()
                if job is not None:
                    self.terminate_job(db, job)
            return 30
        if status in (JobStatus.QUEUED, JobStatus.RUNNING):
            try:
                self._stage_out_custody(job_id)
            finally:
                self._stage_out_logs(job_id)
        return 30

    def _finalize_slurm_job(
        self,
        job_id: str,
        slurm_job_id: str,
        real_status: str,
    ) -> None:
        if self._stage_out_final(job_id) is not True:
            return
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status == real_status,
            ).update({Job.finalization_status: "CLEANUP"}, synchronize_session=False)
            db.commit()
        if changed:
            self._finish_job_cleanup(job_id, slurm_job_id)

    def _finish_job_cleanup(
        self,
        job_id: str,
        slurm_job_id: Optional[str],
    ) -> None:
        # Commit the cleanup phase before removing markers. A restart must not
        # classify the same completed execution again using its deleted marker.
        with SessionLocal() as db:
            current = self._job_query(db, Job.id).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status == "CLEANUP",
            ).first()
        if current is None:
            return
        if self._clean_up_working_table(job_id) is not True:
            return
        self._custody_relay.forget(job_id)
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status == "CLEANUP",
            ).update({
                Job.slurm_job_id: None,
                Job.finalization_status: None,
            }, synchronize_session=False)
            db.commit()
