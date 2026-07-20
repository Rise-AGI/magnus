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
                # magnus_usage 包含 RUNNING 与 inflight release (TERMINATED/PAUSED
                # 且 slurm_job_id 非空)：后者 SLURM 仍在 CG 持有 GPU，从历史趋势
                # 视角属 magnus 而非 external，否则 epilog 期间会出现 30-60s 的
                # "magnus 用量假凹"，且让 slurm_used_gpus - magnus_used_gpus 误
                # 暗示 external 占用激增。跟 cluster endpoint used_gpus 派生口径
                # 对齐（详见 routers/cluster.py 与 JobListItem.is_releasing）。
                holding_jobs = db.query(Job).filter(
                    Job.slurm_job_id.isnot(None),
                    Job.status.in_([
                        JobStatus.RUNNING,
                        JobStatus.TERMINATED,
                        JobStatus.PAUSED,
                    ]),
                ).all()
                magnus_usage = sum(job.gpu_count for job in holding_jobs)

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
                        elif real_status == "RUNNING" and self._docker_job_timed_out(job):
                            # 对偶 SLURM 的 --time：容器仍在跑但超出 time_limit → kill + FAILED。
                            logger.warning(f"Job {job.id} exceeded time_limit ({job.time_limit} min), terminating")
                            job.status = JobStatus.FAILED
                            job.result = f"Job exceeded its time limit ({job.time_limit} min)"
                            job.slurm_job_id = None
                            self.docker_manager.stop_container(container_name)
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
            # ``TERMINATED`` / ``PAUSED`` 且 slurm_job_id 仍在的 job 处于 inflight
            # release 阶段：terminate_job / _kill_and_pause 已经发了 scancel 但
            # SLURM 还在 CG (COMPLETING) 跑 epilog。这里 poll 直到 SLURM 报终态
            # 才清 slurm_job_id，让 cluster endpoint 在 CG 期间仍能匹配 magnus
            # 关联（不显示成 external），并让 PAUSED 走到 Phase 2.5 resubmit 时
            # 旧 SLURM 资源已确实释放。
            inflight_release_info = [
                (job.id, job.slurm_job_id, job.status)
                for job in db.query(Job).filter(
                    Job.status.in_([JobStatus.TERMINATED, JobStatus.PAUSED]),
                    Job.slurm_job_id.isnot(None),
                ).all()
            ]

        # Phase 2 — SLURM 状态检查 + 远端产物回读（无 session）
        slurm_statuses = {}
        for job_id, slurm_job_id in queued_info + running_info:
            if slurm_job_id:
                assert self.slurm_manager is not None
                status = self.slurm_manager.check_job_status(slurm_job_id)
                slurm_statuses[job_id] = status
                # 远端执行：终态把 marker + 日志 + metrics 拉齐，供 Phase 3 的
                # finalize / OOM 判定本机读取；运行中拉日志 / metrics 做 live 镜像。
                # 本机执行下两者均 no-op（远端路径已收敛回本地，无需搬运）。
                if status in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
                    self._stage_out_final(job_id)
                elif status == "RUNNING":
                    self._stage_out_logs(job_id)
        for job_id, slurm_job_id, _ in inflight_release_info:
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

            for job_id, slurm_job_id, original_status in inflight_release_info:
                try:
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if job is None:
                        continue
                    # 中途 status 已被改写（如 PAUSED → PREPARING by Phase 2.5）
                    # 或 slurm_job_id 已被覆盖（如 _submit_to_slurm 写新 id），
                    # 跳过——本批 inflight 快照的前提条件不再成立。
                    if job.status != original_status or job.slurm_job_id != slurm_job_id:
                        continue

                    real_status = slurm_statuses.get(job_id)
                    if real_status in ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"):
                        # SLURM 已真正释放资源，可以清 slurm_job_id；status 保持
                        # 不变（TERMINATED 是终态、PAUSED 等待 Phase 2.5 resubmit）。
                        job.slurm_job_id = None
                    # 其他情况（CG 仍在跑，check_job_status 把 CG 映射到 RUNNING）
                    # 保留 slurm_job_id 等下个 tick 再检查。
                except Exception as error:
                    logger.error(f"Failed to sync inflight-release job {job_id}: {error}")

            db.commit()
