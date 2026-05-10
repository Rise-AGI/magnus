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
    - _DecisionsMixin: EASY backfill + 抢占
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
                # timeout=0 → docker stop 立即发 SIGKILL，跳过 SIGTERM grace。对偶
                # SLURM kill_job 的 `scancel --signal=KILL --full`：user_script 外层
                # bash 装了 trap 不会因 SIGTERM 自死、子壳 SIG_DFL 用户进程被默认
                # disposition 终止 / handler 进程自己处理 —— 默认 10s grace 既不能
                # 让 magnus 即时清场，又会撞上用户 handler 还在持有资源的中间状态。
                # SIGKILL 直发恢复 terminate "no coordination window" 的语义。
                self.docker_manager.stop_container(container_name, timeout = 0)
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

    def signal_job(self, job: Job) -> None:
        """向运行中的 job 进程发送 SIGTERM。

        信号转发器，不动 DB、不清理工作目录、不改 job 状态。SIGTERM 实际会送到
        用户进程：装了 handler 的代码可以做自定义清理（CUDA / NCCL teardown、
        保存检查点等），写 `$MAGNUS_RESULT` + `sys.exit(0)` 让 _sync_reality 收
        敛到 Success；没装 handler 的用户进程被默认 disposition 终止、收敛到
        Failed。详见 docs/internals/job-runtime.md "Signaling and Termination"。
        """
        if not self.enabled:
            logger.warning("Scheduler disabled, skipping signal logic.")
            return

        if not job.slurm_job_id:
            return

        if is_local_mode:
            assert self.docker_manager is not None
            container_name = f"magnus-job-{job.id}"
            logger.info(f"Sending SIGTERM to job {job.id} (Docker: {container_name}).")
            self.docker_manager.send_signal(container_name, "TERM")
        else:
            assert self.slurm_manager is not None
            logger.info(f"Sending SIGTERM to job {job.id} (SLURM: {job.slurm_job_id}).")
            self.slurm_manager.send_signal(
                job.slurm_job_id,
                signal_name = "TERM",
                runner = job.runner if job.runner is not None else "magnus",
                token = job.user.token if job.user.token is not None else "",
            )
