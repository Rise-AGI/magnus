# back_end/server/_scheduler/_core.py
import asyncio
import threading
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Dict, Optional
from sqlalchemy.orm import Session
from ..models import Job, JobStatus
from ..database import SessionLocal
from .._resource_manager import resource_manager
from .._resource_manager._helpers import _run_blocking_io
from .._lifecycle import cancel_background_tasks
from .._slurm_manager import SlurmManager
from .._docker_manager import DockerManager
from .._magnus_config import is_local_mode, configured_execution_targets
from .._execution_targets import ExecutionTarget, select_execution_target
from . import logger
from ._sync import _SyncMixin
from ._submit import _SubmitMixin
from ._decisions import _DecisionsMixin
from ._resources import _ResourcesMixin
from ._job_lifecycle import _JobLifecycleMixin
from ._staging import _StagingMixin, _validate_custody_metadata
from ._custody_relay import CustodyRelay
from .._file_custody_manager import file_custody_manager
from ._artifact_recovery import _ArtifactRecoveryMixin
from ._submission_recovery import _SubmissionRecoveryMixin


class MagnusScheduler(
    _SyncMixin,
    _SubmitMixin,
    _DecisionsMixin,
    _ResourcesMixin,
    _JobLifecycleMixin,
    _StagingMixin,
    _ArtifactRecoveryMixin,
    _SubmissionRecoveryMixin,
):
    """Job 调度器主类。

    职责按 mixin 拆分：
    - _SyncMixin: 把 SLURM/Docker 真实状态拉回数据库 + 集群快照
    - _SubmitMixin: 把 PENDING job 提交到后端
    - _DecisionsMixin: 保守资源准入与抢占
    - _ResourcesMixin: 环境（拉取或构建）与仓库并行准备，全部就绪后进入 Pending
    - _JobLifecycleMixin: success/OOM marker、working table 清理
    - _StagingMixin: 远端执行（transport=ssh）下 job 工作区的跨界搬运（本机执行 no-op）
    - _WorkspaceGCMixin: 持久 job 工作区总量上限的低频滚动回收
    """

    def __init__(
        self,
        target: ExecutionTarget | None = None,
    ):
        self.target = target or select_execution_target(configured_execution_targets, None)
        self.config = self.target.config
        remote = self.config["transport"]["mode"] == "ssh"
        self.remote_workspace_path = f"{self.target.remote_root}/workspace"
        ephemeral_root = self.target.remote_root if remote else self.config["server"]["ephemeral_root"]
        self.remote_ephemeral_workspace_path = f"{ephemeral_root}/workspace"
        self.remote_container_cache_path = f"{self.target.remote_root}/container_cache"
        if is_local_mode:
            self.docker_manager = DockerManager()
            self.slurm_manager = None
            self.enabled = True
            logger.info("🐳 Scheduler initialized in LOCAL mode (Docker backend)")
        else:
            try:
                self.slurm_manager = SlurmManager(config=self.config)
                self.enabled = True
            except RuntimeError as error:
                logger.critical(f"Scheduler disabled due to missing SLURM: {error}")
                self.slurm_manager = None
                self.enabled = False
            self.docker_manager = None
        self.last_snapshot_time = datetime.min.replace(tzinfo=timezone.utc)
        self.last_workspace_gc_time = datetime.min.replace(tzinfo=timezone.utc)
        self.preparing_jobs: Dict[str, asyncio.Task] = {}  # job_id -> Task
        # preparing_jobs 被两类执行体并发动：调度循环（event loop，建/清 task）与
        # terminate_job（sync 端点，跑在 FastAPI 线程池）。用一把锁串起所有 dict 访问，
        # 杜绝 del 撞 pop / 迭代期被改的 KeyError/RuntimeError。_loop 在 tick 里捕获，
        # 供线程池侧把 asyncio.Task.cancel() 经 call_soon_threadsafe 调度回 loop 线程
        # （Task.cancel 跨线程调不安全）。
        self._preparing_jobs_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._image_pull_tasks: Dict[str, asyncio.Task] = {}  # image_uri -> shared pull Task
        self._build_pinned_jobs: set[str] = set()
        self._docker_log_cursors: Dict[str, Optional[str]] = {}  # job_id -> last log timestamp
        self._finalization_retry_after: Dict[str, float] = {}
        self._artifact_recovery_tasks: Dict[str, asyncio.Task[int]] = {}
        self._artifact_recovery_slots = asyncio.Semaphore(2)
        self._custody_bulk_slots = asyncio.Semaphore(1)
        self._custody_bulk_tasks: Dict[str, asyncio.Task] = {}
        self._custody_relay = CustodyRelay(
            transport = self.slurm_manager.transport if self.slurm_manager is not None else None,
            custody = file_custody_manager,
            local_table = self._local_job_working_table,
            remote_table = self._remote_job_working_table,
            validate_metadata = _validate_custody_metadata,
            logger = logger,
            transfer_timeout = self.config.get("transport", {}).get("ssh", {}).get("transfer_timeout_seconds", 900),
            transfer_logs = self._stage_out_logs,
        )
        self._artifact_retry_after: Dict[str, float] = {}
        self._submission_task: Optional[asyncio.Task] = None
        self._submitting_jobs: set[str] = set()
        self._submission_cancel_events: dict[str, threading.Event] = {}
        self._submission_stopping = False
        self._submission_reconcile_after = 0.0
        self._submission_reconcile_cursor = 0
        self._staged_job_ids: set[str] = set()

    async def tick(self):
        if not self.enabled:
            return
        # tick 跑在调度 event loop 上：捕获它，供 terminate_job（线程池）跨线程调度
        # task.cancel()。idempotent。
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        try:
            # subprocess 调用（docker logs / slurm queries）放线程池，避免阻塞 event loop
            artifact_candidates = await _run_blocking_io(self._sync_reality)
            self._schedule_artifact_recovery(artifact_candidates)
            await self._make_decisions()
            await _run_blocking_io(self._release_finished_builds)
            # _record_snapshot 也会跑 slurm 查询（远端 transport 下是骑 socket 的 squeue/
            # scontrol，秒级，且 auto_connect 时可能触发 socket 重建）——同样丢线程池，
            # 否则会周期性阻塞 event loop。tick 串行执行，线程内独占无并发。
            await _run_blocking_io(self._record_snapshot)
        except Exception as error:
            logger.error(f"Scheduler tick failed: {error}", exc_info=True)

    def _job_query(
        self,
        db: Session,
        *entities,
    ):
        return db.query(*entities).filter(self.target.job_filter(Job))

    def inflight_job_ids(
        self,
    ) -> set[str]:
        with self._preparing_jobs_lock:
            return set(self.preparing_jobs) | self._submitting_jobs

    async def shutdown(
        self,
    ) -> None:
        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(self._shutdown_preparing_jobs)
            cleanup.push_async_callback(self.shutdown_artifact_recovery)
            cleanup.push_async_callback(self.shutdown_submission)

    async def _shutdown_preparing_jobs(
        self,
    ) -> None:
        with self._preparing_jobs_lock:
            tasks = list(self.preparing_jobs.values())
        await cancel_background_tasks(tasks)

    def terminate_job(self, db: Session, job: Job) -> None:
        """API endpoint for user-initiated job termination"""
        if not self.enabled:
            logger.warning("Scheduler disabled, skipping termination logic.")
            return

        db.refresh(job)
        previous_allocation = job.slurm_job_id
        if job.slurm_job_id and not job.finalization_status:
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
        db.refresh(job)
        if previous_allocation is None and job.slurm_job_id is not None:
            # A submitter may publish its ID while this cancellation commits.
            # Re-enter with that association instead of cleaning a live workspace.
            self.terminate_job(db, job)
            return
        # Persist TERMINATED first: late build/repository completions use a
        # conditional update and cannot resurrect this job as Pending. Retain
        # its task until filesystem workers drain; workspace GC also checks it.
        with self._preparing_jobs_lock:
            preparing_task = self.preparing_jobs.get(job.id)
            submitting = job.id in self._submitting_jobs
            if submitting:
                self._submission_cancel_events[job.id].set()
        if submitting:
            return
        if preparing_task is not None and not preparing_task.done() and self._loop is not None:
            job_id = job.id
            def cancel_preparation():
                # The task may finish between the thread's observation and this
                # callback. In that case cancellation is a no-op; still clean up.
                if preparing_task.done():
                    cleanup = asyncio.create_task(_run_blocking_io(
                        self._clean_up_working_table,
                        job_id,
                    ))
                    with self._preparing_jobs_lock:
                        self.preparing_jobs[job_id] = cleanup
                else:
                    preparing_task.cancel()
            self._loop.call_soon_threadsafe(cancel_preparation)
        elif not job.finalization_status and job.submission_attempt is None and (is_local_mode or job.slurm_job_id is None):
            self._clean_up_working_table(job.id)

    def _release_finished_builds(self) -> None:
        """Release artifacts only after submit/staging and external resources drain."""
        with self._preparing_jobs_lock:
            candidates = self._build_pinned_jobs - set(self.preparing_jobs) - self._submitting_jobs
        if not candidates:
            return
        with SessionLocal() as db:
            finished = db.query(Job.id).filter(
                Job.id.in_(candidates),
                Job.status.in_([JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TERMINATED]),
                Job.slurm_job_id.is_(None),
                Job.finalization_status.is_(None),
                Job.submission_attempt.is_(None),
            ).all()
        released = set()
        for (job_id,) in finished:
            with self._preparing_jobs_lock:
                if job_id in self.preparing_jobs or job_id in self._submitting_jobs:
                    continue
            resource_manager.builds.release(job_id)
            released.add(job_id)
        with self._preparing_jobs_lock:
            self._build_pinned_jobs.difference_update(released)

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
