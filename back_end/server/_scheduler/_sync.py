# back_end/server/_scheduler/_sync.py
import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING, Any, Dict, Optional
from .._protocol_files import open_protocol_file, replace_protocol_file
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

    def _dump_docker_logs(
        self,
        job_id: str,
        container_name: str,
        since: Optional[str] = None,
        *,
        final: bool = False,
    ) -> str:
        cursor = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        command = ["docker", "logs", container_name]
        if not final:
            command.extend(["--until", cursor])
            if since:
                command.extend(["--since", since])
        # Final snapshots replace the previous file atomically: a failed fetch
        # or restart cannot truncate retained logs or append the full log twice.
        with tempfile.TemporaryDirectory(prefix=".docker-logs-", dir=magnus_workspace_path) as directory:
            snapshot = Path(directory) / "output"
            with snapshot.open("wb") as stream:
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                )
                if result.returncode != 0:
                    raise RuntimeError("Docker log retrieval failed")
                stream.flush()
                os.fsync(stream.fileno())
            if final or since is None:
                replace_protocol_file(
                    f"{magnus_workspace_path}/jobs",
                    f"{job_id}/slurm/output.txt",
                    str(snapshot),
                )
            else:
                with snapshot.open("rb") as source, open_protocol_file(
                    f"{magnus_workspace_path}/jobs",
                    f"{job_id}/slurm/output.txt",
                    "ab",
                ) as destination:
                    shutil.copyfileobj(source, destination)
        return cursor

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

    def _sync_reality_docker(
        self,
    ) -> None:
        assert self.docker_manager is not None
        terminal = (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TERMINATED)
        with SessionLocal() as db:
            identifiers = [row[0] for row in self._job_query(db, Job.id).filter(
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]) | Job.slurm_job_id.isnot(None),
                Job.finalization_status.is_(None) | (Job.finalization_status != "CLEANUP"),
            ).all()]
        for job_id in identifiers:
            if monotonic() < self._finalization_retry_after.get(job_id, 0):
                continue
            name = f"magnus-job-{job_id}"
            try:
                observed = self.docker_manager.check_container_status(name)
                if observed == "UNKNOWN":
                    raise RuntimeError("Docker state is unavailable")
                with SessionLocal() as db:
                    job = self._job_query(db, Job).filter(Job.id == job_id).first()
                    if job is None or job.finalization_status == "CLEANUP":
                        continue
                    original_status = job.status
                    external_id = job.slurm_job_id
                    if job.status == JobStatus.RUNNING and observed == "RUNNING" and self._docker_job_timed_out(job):
                        self.docker_manager.stop_container(name)
                        job.status = JobStatus.FAILED
                        job.result = f"Job exceeded its time limit ({job.time_limit} min)"
                        job.finalization_status = "TIMEOUT"
                    elif job.status in terminal:
                        if observed in ("RUNNING", "PENDING"):
                            self.docker_manager.stop_container(name, timeout=0)
                        job.finalization_status = job.finalization_status or "STOPPED"
                    elif observed in ("COMPLETED", "FAILED", "MISSING"):
                        if observed == "COMPLETED":
                            self._write_success_marker(job_id)
                            self._finalize_completed_job(job, cleanup=False)
                        else:
                            job.status = JobStatus.FAILED
                            information = self.docker_manager.get_termination_info(name) if observed == "FAILED" else {}
                            job.result = (
                                self._format_oom_message(job.memory_demand)
                                if information.get("oom_killed") else f"Container {observed} during execution"
                            )
                        job.finalization_status = observed
                    elif observed == "RUNNING" and job.status == JobStatus.QUEUED:
                        job.status = JobStatus.RUNNING
                        job.start_time = datetime.now(timezone.utc)
                    values = {
                        Job.status: job.status,
                        Job.result: job.result,
                        Job.action: job.action,
                        Job.start_time: job.start_time,
                        Job.finalization_status: job.finalization_status,
                    }
                    finished = job.status in terminal
                    db.expunge(job)
                    changed = self._job_query(db, Job).filter(
                        Job.id == job_id,
                        Job.status == original_status,
                        Job.slurm_job_id == external_id,
                        Job.submission_attempt.is_(None),
                    ).update(values, synchronize_session=False)
                    db.commit()
                if not changed:
                    continue
                if finished:
                    self._finish_docker_job(job_id, external_id, observed)
                elif observed == "RUNNING":
                    self._docker_log_cursors[job_id] = self._dump_docker_logs(
                        job_id,
                        name,
                        since=self._docker_log_cursors.get(job_id),
                    )
                self._finalization_retry_after.pop(job_id, None)
            except Exception as error:
                self._finalization_retry_after[job_id] = monotonic() + 30
                logger.warning("Job %s Docker synchronization pending (%s)", job_id, type(error).__name__)

    def _finish_docker_job(
        self,
        job_id: str,
        external_id: str | None,
        observed: str,
    ) -> None:
        name = f"magnus-job-{job_id}"
        if observed != "MISSING":
            try:
                self._dump_docker_logs(job_id, name, final=True)
            except Exception:
                if self.docker_manager.check_container_status(name) != "MISSING":
                    raise
        self.docker_manager.remove_container(name)
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.status.in_([JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TERMINATED]),
                Job.slurm_job_id == external_id,
                Job.submission_attempt.is_(None),
            ).update({Job.finalization_status: "CLEANUP"}, synchronize_session=False)
            db.commit()
        if changed:
            self._docker_log_cursors.pop(job_id, None)
            self._finish_job_cleanup(job_id, external_id)

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
            return self._finalize_slurm_job(job_id, slurm_job_id, finalization_status)
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
    ) -> int:
        assert self.slurm_manager is not None
        if not self.slurm_manager.observe_job(slurm_job_id).allocation_released:
            # Epilog/requeue cleanup can still hold the workspace after the
            # execution outcome is known. Do not freeze logs or final receipts.
            try:
                self._stage_out_custody(job_id)
            finally:
                self._stage_out_logs(job_id)
            return 10
        if self._stage_out_final(job_id) is not True:
            return 60
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.slurm_job_id == slurm_job_id,
                Job.finalization_status == real_status,
            ).update({Job.finalization_status: "CLEANUP"}, synchronize_session=False)
            db.commit()
        if changed:
            self._finish_job_cleanup(job_id, slurm_job_id)
        return 60

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
