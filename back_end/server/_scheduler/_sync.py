# back_end/server/_scheduler/_sync.py
import os
import subprocess
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional
from ..database import SessionLocal
from ..models import Job, JobStatus, ClusterSnapshot
from .._magnus_config import magnus_config, is_local_mode
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
            magnus_config["server"]["scheduler"]["snapshot_interval"]:
            return

        try:
            # Phase 1 — SLURM 调用（无 session）
            assert self.slurm_manager is not None
            slurm_stats = self.slurm_manager.get_resource_snapshot()

            # Phase 2 — 写快照（短 session）
            with SessionLocal() as db:
                running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
                magnus_usage = sum(job.gpu_count for job in running_jobs)

                snapshot = ClusterSnapshot(
                    total_gpus = slurm_stats["total_gpus"],
                    slurm_used_gpus = slurm_stats["slurm_used_gpus"],
                    magnus_used_gpus = magnus_usage,
                    timestamp = now,
                )
                db.add(snapshot)
                db.commit()
                logger.debug(f"Recorded Cluster Snapshot: Total={snapshot.total_gpus}, Used={snapshot.slurm_used_gpus}, Magnus={magnus_usage}")
            self.last_snapshot_time = now
        except Exception as error:
            logger.error(f"Failed to record cluster snapshot: {error}")

    def _dump_docker_logs(self, job_id: str, container_name: str, since: Optional[str] = None) -> Optional[str]:
        # 与 SLURM 模式的 sbatch --output 共用同一文件，让 jobs.py 读端点不必按模式分支。
        log_path = f"{magnus_workspace_path}/jobs/{job_id}/slurm/output.txt"
        # Capture cursor BEFORE fetching logs to avoid missing lines emitted during the call
        new_cursor = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                mode = "a" if since else "w"
                with open(log_path, mode, encoding="utf-8") as f:
                    f.write(output)
            return new_cursor
        except Exception as error:
            logger.warning(f"Failed to dump Docker logs for {job_id}: {error}")
            return since

    def _sync_reality(self):
        """同步真实状态到数据库（SLURM 或 Docker）"""
        if is_local_mode:
            self._sync_reality_docker()
        else:
            self._sync_reality_slurm()

    def _sync_reality_docker(self):
        """同步 Docker 容器状态到数据库（三阶段模式，与 _sync_reality_slurm 对称）"""
        assert self.docker_manager is not None

        # Phase 1 — 收集 job 信息（短 session）
        with SessionLocal() as db:
            active_info = [
                (job.id, job.status)
                for job in db.query(Job).filter(
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
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
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if not job:
                        continue

                    real_status = info["status"]
                    container_name = info["container_name"]

                    if job.status == JobStatus.QUEUED:
                        if real_status == "RUNNING":
                            job.status = JobStatus.RUNNING
                            job.start_time = datetime.now(timezone.utc)
                            logger.info(f"Job {job.id} started running in Docker")
                        elif real_status == "COMPLETED":
                            self._write_success_marker(job.id)
                            self._finalize_completed_job(job)
                            self.docker_manager.remove_container(container_name)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status == "FAILED":
                            logger.warning(f"Job {job.id} failed in Docker")
                            job.status = JobStatus.FAILED
                            term_info = self.docker_manager.get_termination_info(container_name)
                            if term_info.get("oom_killed"):
                                job.result = self._format_oom_message(job.memory_demand)
                            else:
                                job.result = "Container exited with non-zero status while starting"
                            job.slurm_job_id = None
                            self.docker_manager.remove_container(container_name)
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status in ["UNKNOWN"]:
                            logger.warning(f"Job {job.id} container not found while QUEUED")
                            job.status = JobStatus.FAILED
                            job.result = "Container disappeared while queued (may have been removed externally)"
                            job.slurm_job_id = None
                            self.docker_manager.remove_container(container_name)
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)

                    elif job.status == JobStatus.RUNNING:
                        if real_status == "COMPLETED":
                            self._write_success_marker(job.id)
                            self._finalize_completed_job(job)
                            self.docker_manager.remove_container(container_name)
                            self._docker_log_cursors.pop(job_id, None)
                        elif real_status in ["FAILED", "UNKNOWN"]:
                            logger.warning(f"Job {job.id} failed in Docker (Status: {real_status})")
                            job.status = JobStatus.FAILED
                            # UNKNOWN means the container is gone; OOMKilled is only meaningful
                            # for FAILED (exited non-zero), so only consult inspect there.
                            term_info = (
                                self.docker_manager.get_termination_info(container_name)
                                if real_status == "FAILED" else {"oom_killed": False}
                            )
                            if term_info.get("oom_killed"):
                                job.result = self._format_oom_message(job.memory_demand)
                            else:
                                job.result = f"Container {real_status} during execution"
                            job.slurm_job_id = None
                            self.docker_manager.remove_container(container_name)
                            self._clean_up_working_table(job.id)
                            self._docker_log_cursors.pop(job_id, None)

                except Exception as error:
                    logger.error(f"Failed to sync Docker job {job_id}: {error}")

            db.commit()

    def _sync_reality_slurm(self):
        """同步 SLURM 真实状态到数据库"""
        # Phase 1 — 收集 job 信息（短 session）
        with SessionLocal() as db:
            queued_info = [
                (job.id, job.slurm_job_id)
                for job in db.query(Job).filter(Job.status == JobStatus.QUEUED).all()
            ]
            running_info = [
                (job.id, job.slurm_job_id)
                for job in db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
            ]

        # Phase 2 — SLURM 状态检查（无 session）
        slurm_statuses = {}
        for job_id, slurm_job_id in queued_info + running_info:
            if slurm_job_id:
                assert self.slurm_manager is not None
                slurm_statuses[job_id] = self.slurm_manager.check_job_status(slurm_job_id)

        # Phase 3 — 批量更新（短 session）
        with SessionLocal() as db:
            for job_id, slurm_job_id in queued_info:
                try:
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if not job or job.status != JobStatus.QUEUED:
                        continue

                    if not slurm_job_id:
                        logger.warning(f"Job {job.id} is QUEUED but has no slurm_id. Marking FAILED.")
                        job.status = JobStatus.FAILED
                        job.result = "Internal error: job entered QUEUED state without a scheduler job ID"
                        continue

                    real_status = slurm_statuses.get(job_id)
                    if real_status == "RUNNING":
                        job.status = JobStatus.RUNNING
                        job.start_time = datetime.now(timezone.utc)
                        logger.info(f"Job {job.id} started running in SLURM (ID: {slurm_job_id})")
                    elif real_status == "COMPLETED":
                        self._finalize_completed_job(job)
                    elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                        logger.warning(f"Job {job.id} failed in SLURM queue (Status: {real_status}).")
                        job.status = JobStatus.FAILED
                        if self._has_oom_marker(job.id):
                            job.result = self._format_oom_message(job.memory_demand)
                        else:
                            job.result = f"Scheduler reported {real_status} while job was queued"
                        job.slurm_job_id = None
                        self._clean_up_working_table(job.id)
                    # else: SLURM 仍在排队（PD）或状态未知，保持 QUEUED
                except Exception as error:
                    logger.error(f"Failed to sync QUEUED job {job_id}: {error}")

            for job_id, slurm_job_id in running_info:
                try:
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if not job or job.status != JobStatus.RUNNING:
                        continue

                    if not slurm_job_id:
                        logger.warning(f"Job {job.id} is RUNNING but has no slurm_id. Marking FAILED.")
                        job.status = JobStatus.FAILED
                        job.result = "Internal error: job entered RUNNING state without a scheduler job ID"
                        continue

                    real_status = slurm_statuses.get(job_id)
                    if real_status == "COMPLETED":
                        self._finalize_completed_job(job)
                    elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                        # Marker 优先于 SLURM state：signal_job 路径下用户 handler
                        # 处理后写了 .magnus_result，wrapper 写 success marker 并
                        # `sys.exit(0)` 让 SLURM 优先报 COMPLETED；但实测某些 SLURM
                        # 版本 / 配置下 batch step 收过 SIGTERM 仍会被标 FAILED /
                        # CANCELLED。这里先看 marker 兜底：marker 存在 → finalize
                        # 走 SUCCESS；不存在 → SLURM state 是真 failure（含 OOM
                        # 检测，保留 SLURM state info 进 result）。这一层与
                        # _wrapper_template.py Phase 3 的 sys.exit(0) 形成 defense
                        # -in-depth。
                        marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
                        if os.path.exists(marker_path):
                            self._finalize_completed_job(job)
                        else:
                            logger.warning(f"Job {job.id} failed in SLURM (Status: {real_status}).")
                            job.status = JobStatus.FAILED
                            if self._has_oom_marker(job.id):
                                job.result = self._format_oom_message(job.memory_demand)
                            else:
                                job.result = f"Scheduler reported {real_status} during execution"
                            job.slurm_job_id = None
                            self._clean_up_working_table(job.id)
                except Exception as error:
                    logger.error(f"Failed to sync RUNNING job {job_id}: {error}")

            db.commit()
