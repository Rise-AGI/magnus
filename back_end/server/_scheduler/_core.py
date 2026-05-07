# back_end/server/_scheduler/_core.py
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional
from sqlalchemy.orm import Session
from ..models import Job, JobStatus
from .._slurm_manager import SlurmManager
from .._docker_manager import DockerManager
from .._magnus_config import is_local_mode
from . import logger
from ._sync import _SyncMixin
from ._submit import _SubmitMixin
from ._decisions import _DecisionsMixin
from ._resources import _ResourcesMixin
from ._job_lifecycle import _JobLifecycleMixin


class MagnusScheduler(
    _SyncMixin,
    _SubmitMixin,
    _DecisionsMixin,
    _ResourcesMixin,
    _JobLifecycleMixin,
):
    """Job 调度器主类。

    职责按 mixin 拆分：
    - _SyncMixin: 把 SLURM/Docker 真实状态拉回数据库 + 集群快照
    - _SubmitMixin: 把 PENDING job 提交到后端
    - _DecisionsMixin: 队头挂号 + 抢占
    - _ResourcesMixin: 镜像拉取 + 仓库 clone (Preparing → Pending)
    - _JobLifecycleMixin: success/OOM marker、working table 清理
    """

    def __init__(self):
        if is_local_mode:
            self.docker_manager = DockerManager()
            self.slurm_manager = None
            self.enabled = True
            logger.info("🐳 Scheduler initialized in LOCAL mode (Docker backend)")
        else:
            try:
                self.slurm_manager = SlurmManager()
                self.enabled = True
            except RuntimeError as error:
                logger.critical(f"Scheduler disabled due to missing SLURM: {error}")
                self.slurm_manager = None
                self.enabled = False
            self.docker_manager = None
        self.last_snapshot_time = datetime.min.replace(tzinfo=timezone.utc)
        self.preparing_jobs: Dict[str, asyncio.Task] = {}  # job_id -> Task
        self._image_pull_tasks: Dict[str, asyncio.Task] = {}  # image_uri -> shared pull Task
        self._docker_log_cursors: Dict[str, Optional[str]] = {}  # job_id -> last log timestamp

    async def tick(self):
        if not self.enabled:
            return
        try:
            # subprocess 调用（docker logs / slurm queries）放线程池，避免阻塞 event loop
            await asyncio.to_thread(self._sync_reality)
            await self._make_decisions()
            self._record_snapshot()
        except Exception as error:
            logger.error(f"Scheduler tick failed: {error}", exc_info=True)

    def terminate_job(self, db: Session, job: Job) -> None:
        """API endpoint for user-initiated job termination"""
        if not self.enabled:
            logger.warning("Scheduler disabled, skipping termination logic.")
            return

        # 取消 Preparing 状态的异步任务
        if job.id in self.preparing_jobs:
            self.preparing_jobs[job.id].cancel()
            del self.preparing_jobs[job.id]

        if job.slurm_job_id:
            if is_local_mode:
                assert self.docker_manager is not None
                container_name = f"magnus-job-{job.id}"
                logger.info(f"Terminating job {job.id} (Docker: {container_name}) by user request.")
                self.docker_manager.stop_container(container_name)
                self.docker_manager.remove_container(container_name)
            else:
                assert self.slurm_manager is not None
                logger.info(f"Terminating job {job.id} (SLURM: {job.slurm_job_id}) by user request.")
                self.slurm_manager.kill_job(
                    job.slurm_job_id,
                    runner = job.runner if job.runner is not None else "magnus",
                    token = job.user.token if job.user.token is not None else "",
                )

        self._clean_up_working_table(job.id)
        job.status = JobStatus.TERMINATED
        job.slurm_job_id = None
        job.start_time = None
        db.commit()
