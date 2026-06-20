# back_end/server/_scheduler/_core.py
import asyncio
import threading
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
from ._staging import _StagingMixin


class MagnusScheduler(
    _SyncMixin,
    _SubmitMixin,
    _DecisionsMixin,
    _ResourcesMixin,
    _JobLifecycleMixin,
    _StagingMixin,
):
    """Job 调度器主类。

    职责按 mixin 拆分：
    - _SyncMixin: 把 SLURM/Docker 真实状态拉回数据库 + 集群快照
    - _SubmitMixin: 把 PENDING job 提交到后端
    - _DecisionsMixin: EASY backfill + 抢占
    - _ResourcesMixin: 镜像拉取 + 仓库 clone (Preparing → Pending)
    - _JobLifecycleMixin: success/OOM marker、working table 清理
    - _StagingMixin: 远端执行（transport=ssh）下 job 工作区的跨界搬运（本机执行 no-op）
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
        # preparing_jobs 被两类执行体并发动：调度循环（event loop，建/清 task）与
        # terminate_job（sync 端点，跑在 FastAPI 线程池）。用一把锁串起所有 dict 访问，
        # 杜绝 del 撞 pop / 迭代期被改的 KeyError/RuntimeError。_loop 在 tick 里捕获，
        # 供线程池侧把 asyncio.Task.cancel() 经 call_soon_threadsafe 调度回 loop 线程
        # （Task.cancel 跨线程调不安全）。
        self._preparing_jobs_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._image_pull_tasks: Dict[str, asyncio.Task] = {}  # image_uri -> shared pull Task
        self._docker_log_cursors: Dict[str, Optional[str]] = {}  # job_id -> last log timestamp

    async def tick(self):
        if not self.enabled:
            return
        # tick 跑在调度 event loop 上：捕获它，供 terminate_job（线程池）跨线程调度
        # task.cancel()。idempotent。
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        try:
            # subprocess 调用（docker logs / slurm queries）放线程池，避免阻塞 event loop
            await asyncio.to_thread(self._sync_reality)
            await self._make_decisions()
            # _record_snapshot 也会跑 slurm 查询（远端 transport 下是骑 socket 的 squeue/
            # scontrol，秒级，且 auto_connect 时可能触发 socket 重建）——同样丢线程池，
            # 否则会周期性阻塞 event loop。tick 串行执行，线程内独占无并发。
            await asyncio.to_thread(self._record_snapshot)
        except Exception as error:
            logger.error(f"Scheduler tick failed: {error}", exc_info=True)

    def terminate_job(self, db: Session, job: Job) -> None:
        """API endpoint for user-initiated job termination"""
        if not self.enabled:
            logger.warning("Scheduler disabled, skipping termination logic.")
            return

        # 取消 Preparing 状态的异步任务。本函数跑在 FastAPI 线程池：先在锁内把 task 摘出
        # （与调度循环的建/清原子互斥），再经 call_soon_threadsafe 把 cancel 调度回 loop
        # 线程执行 —— asyncio.Task.cancel() 跨线程直调不安全。摘出后调度循环 Phase 2 不会
        # 再见到它，无 double-pop。
        with self._preparing_jobs_lock:
            preparing_task = self.preparing_jobs.pop(job.id, None)
        if preparing_task is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(preparing_task.cancel)

        if job.slurm_job_id:
            if is_local_mode:
                assert self.docker_manager is not None
                container_name = f"magnus-job-{job.id}"
                logger.info(f"Terminating job {job.id} (Docker: {container_name}) by user request.")
                # timeout=0 → docker stop 立即发 SIGKILL，跳过 SIGTERM grace。
                # 对偶 SLURM kill_job 的 `scancel --signal=KILL --full`：默认的
                # 10s SIGTERM grace 期间，外层 bash trap 把 SIGTERM 转发给整个
                # user pgrp，子壳和 handler-less 用户代码都 SIG_IGN 不死、空转
                # 整 grace；handler-aware 代码会借这个窗口跑收尾。terminate 的
                # 语义是"no coordination window"，不能等也不该等 user handler，
                # 因此 SIGKILL 直发立刻清场。

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
        if is_local_mode:
            # Docker 模式没有 SLURM 那种 CG 收尾窗口，stop_container +
            # remove_container 已同步完成，slurm_job_id (实际存的是
            # container_name) 立即清空，跟 _sync_reality_docker 的清理路径一致。
            job.slurm_job_id = None
        # else (SLURM): slurm_job_id 不在这里清空。scancel 后 SLURM job 进入
        # CG (COMPLETING) 阶段跑 epilog（含 GPU reset 等），可能持续数十秒。
        # 期间 cluster endpoint 看 squeue 还能见到这个 slurm_job_id，若 magnus
        # 端立即清空，cluster 的 magnus_job_map 找不到映射，会把该 inflight job
        # 错显示成 external "(slurm)" 任务，让用户怀疑是绕过 magnus 的越权提交。
        # 改由 _sync_reality_slurm 在 SLURM 真正报
        # COMPLETED/FAILED/CANCELLED/TIMEOUT 后清空，跟 RUNNING → 终态的清理
        # 路径一致。
        job.start_time = None
        db.commit()

    def signal_job(self, job: Job) -> None:
        """向运行中的 job 进程发送 SIGTERM。

        信号转发器，不动 DB、不清理工作目录、不改 job 状态。SIGTERM 实际会送到
        用户进程：装了 handler 的代码可以做自定义清理（CUDA / NCCL teardown、
        保存检查点等），写 `$MAGNUS_RESULT` + `sys.exit(0)` 让 _sync_reality 收
        敛到 Success；没装 handler 的用户代码因继承 user-script bash 的 SIG_IGN
        把 SIGTERM 当 no-op，job 继续跑，想强杀走 terminate_job 的 SIGKILL 路径。
        详见 docs/internals/job-runtime.md "Signaling and Termination"。
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
