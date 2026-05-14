# back_end/server/_scheduler/_decisions.py
"""调度决策：EASY backfill 模式 — 队头优先级保留，后续不延迟队头者旁路启动。"""
import asyncio
from typing import TYPE_CHECKING, Tuple

from sqlalchemy.orm import Session

from library.fundamental.scheduling import (
    BackfillCandidate,
    ResourceVector,
    select_easy_backfill,
)
from ..database import SessionLocal
from ..models import Job, JobStatus, JobType
from .._magnus_config import is_local_mode
from .._size_utils import _parse_size_string
from . import logger

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _DecisionsMixinBase = _SchedulerProtocol
else:
    _DecisionsMixinBase = object


_BYTES_PER_MEGABYTE = 1024 * 1024


def _job_to_resource_vector(job: Job) -> ResourceVector:
    """把 Job 资源诉求映射成 ``(gpu, cpu_cores, memory_mb)`` 三维向量。

    维度顺序与 ``_DecisionsMixin._compute_cluster_resources`` 严格对应，
    任何一处加维度（如 ephemeral_storage）必须同步另一处。
    """
    if job.memory_demand is None:
        memory_mb = 0
    else:
        memory_mb = _parse_size_string(job.memory_demand) // _BYTES_PER_MEGABYTE
    return ResourceVector(
        components = (
            job.gpu_count,
            job.cpu_count or 0,
            memory_mb,
        ),
    )


class _DecisionsMixin(_DecisionsMixinBase):

    async def _make_decisions(self):
        """
        调度决策 — EASY backfill 模式

        状态流转：Preparing → Pending → Queued → Running
        - Preparing: 系统正在准备资源（镜像、仓库）
        - Pending:   资源就绪，等待调度决策
        - Queued:    已提交到 SLURM，等待执行
        - Running:   SLURM 正在执行

        调度核心：
        1. 新任务以 Preparing 状态进入，启动异步资源准备
        2. 资源准备完成后变为 Pending
        3. 按 (priority, time) 排序 Pending 队列，队头是 A 类时抢占 B 类释放 GPU
        4. EASY backfill 选出当下可启动的子集 — 队头自身能跑则按严格优先级贪心，
           队头在等则挑后续不延迟队头的候选旁路 — 全部一次提交到 SLURM。
           调度逻辑全部在 magnus 内决策，SLURM 只接收已被 backfill 验证过资源
           的提交，不再承担排队 / 优先级仲裁工作
        """
        with SessionLocal() as db:
            priority_map = {
                JobType.A1: 4,
                JobType.A2: 3,
                JobType.B1: 2,
                JobType.B2: 1,
            }

            # Phase 1: 启动 Preparing 任务的资源准备
            preparing_jobs = db.query(Job).filter(Job.status == JobStatus.PREPARING).all()
            for job in preparing_jobs:
                if job.id not in self.preparing_jobs:
                    task = asyncio.create_task(self._prepare_job_resources(job.id))
                    self.preparing_jobs[job.id] = task
                    logger.info(f"Job {job.id} started resource preparation")

            # Phase 2: 清理已完成的 preparing tasks
            done_jobs = [jid for jid, task in self.preparing_jobs.items() if task.done()]
            for jid in done_jobs:
                task = self.preparing_jobs.pop(jid)
                exc = task.exception()
                if exc is not None:
                    logger.error(f"Job {jid} preparation crashed: {exc}")
                    self._clean_up_working_table(jid)
                    failed_job = db.query(Job).filter(Job.id == jid).first()
                    if failed_job and failed_job.status == JobStatus.PREPARING:
                        failed_job.status = JobStatus.FAILED
                        failed_job.result = f"Resource preparation crashed: {exc}"
                        db.commit()

            # Phase 2.5: 抢占恢复 — PAUSED 任务重新准备资源（镜像/仓库在抢占时已被清理）
            paused_jobs = db.query(Job).filter(Job.status == JobStatus.PAUSED).all()
            for job in paused_jobs:
                job.status = JobStatus.PREPARING
                logger.info(f"Job {job.id} re-entering preparation after preemption")
            if paused_jobs:
                db.commit()

            # Phase 3: 调度 Pending 任务
            schedulable_jobs = db.query(Job).filter(
                Job.status == JobStatus.PENDING
            ).all()

            if not schedulable_jobs:
                return

            schedulable_jobs.sort(
                key = lambda x: (priority_map.get(x.job_type, 0), -x.created_at.timestamp()),
                reverse = True,
            )

            head_job = schedulable_jobs[0]

            # 队头是 A 类时先尝试抢占 B 类，让 backfill 拿到可能更大的 free
            if not is_local_mode and head_job.job_type in [JobType.A1, JobType.A2]:
                self._handle_preemption_for_job(db, head_job)

            if is_local_mode:
                # local 模式只跑队头，docker run 自身非阻塞，多容器自然并行
                self._submit_to_docker(db, head_job)
            else:
                cluster_total, cluster_free = self._compute_cluster_resources()
                candidates = [
                    BackfillCandidate(
                        payload = job,
                        demand = _job_to_resource_vector(job),
                    )
                    for job in schedulable_jobs
                ]
                selected = select_easy_backfill(
                    candidates = candidates,
                    cluster_total = cluster_total,
                    cluster_free = cluster_free,
                )
                for job in selected:
                    self._submit_to_slurm(db, job)

    def _compute_cluster_resources(self) -> Tuple[ResourceVector, ResourceVector]:
        """从 SLURM 一次性派生 ``(total, free)`` 资源向量。

        维度顺序固定为 ``(gpu, cpu_cores, memory_mb)``，与
        ``_job_to_resource_vector`` 对齐。一次 scontrol (NodeSnapshot) 给三个
        容量维度，一次 squeue (running tasks) 派生三个 alloc 维度——gpu / cpu /
        mem 全部走 job-level squeue 数字 sum，让 free 与 total 来自同一时刻
        SLURM 视图，无 race。alloc 不走 scontrol 的 CPUAlloc / AllocMem 是因为
        后者在 CG 状态会瞬时清零，跟 squeue 视角下 GPU 仍占用矛盾；统一从
        squeue 派生即可消除该内部不一致，避免 scheduler 在 epilog 期间误判
        free 充足而 over-schedule。详见 NodeSnapshot docstring。
        """
        assert self.slurm_manager is not None
        node_snap = self.slurm_manager.get_node_snapshot()
        running_tasks = self.slurm_manager.get_all_running_tasks()
        alloc_gpus = sum(task["gpu_count"] for task in running_tasks)
        alloc_cpus = sum(task["cpu_count"] for task in running_tasks)
        alloc_mem_mb = sum(task["memory_mb"] for task in running_tasks)

        cluster_total = ResourceVector(
            components = (
                node_snap.total_gpus,
                node_snap.cpu_total,
                node_snap.mem_total_mb,
            ),
        )
        cluster_free = ResourceVector(
            components = (
                max(0, node_snap.total_gpus - alloc_gpus),
                max(0, node_snap.cpu_total - alloc_cpus),
                max(0, node_snap.mem_total_mb - alloc_mem_mb),
            ),
        )
        return cluster_total, cluster_free

    def _handle_preemption_for_job(self, db: Session, job: Job):
        """为指定的 A 类任务处理抢占逻辑"""
        assert self.slurm_manager is not None
        free_gpus = self.slurm_manager.get_cluster_free_gpus()

        if free_gpus >= job.gpu_count:
            return  # 资源充足，无需抢占

        needed = job.gpu_count - free_gpus

        running_b_jobs = db.query(Job).filter(
            Job.status == JobStatus.RUNNING,
            Job.job_type.in_([JobType.B1, JobType.B2])
        ).all()

        if not running_b_jobs:
            return

        # 优先杀 B2，同优先级先杀晚启动的 (LIFO)
        kill_priority = {JobType.B2: 1, JobType.B1: 0}
        running_b_jobs.sort(
            key = lambda x: (
                kill_priority.get(x.job_type, 0),
                x.start_time.timestamp() if x.start_time else 0
            ),
            reverse = True,
        )

        victims, recovered = [], 0
        for b_job in running_b_jobs:
            if recovered >= needed:
                break
            victims.append(b_job)
            recovered += b_job.gpu_count

        if recovered >= needed:
            logger.info(f"Preemption: Job {job.id} ({job.job_type}) reclaiming {needed} GPUs from {len(victims)} B-class jobs")
            for v in victims:
                self._kill_and_pause(db, v)

    def _kill_and_pause(self, db: Session, job: Job):
        """Kill SLURM job and mark as PAUSED for preemption"""
        if job.slurm_job_id:
            logger.info(f"Killing victim job {job.id} (SLURM: {job.slurm_job_id})")
            assert self.slurm_manager is not None
            self.slurm_manager.kill_job(
                job.slurm_job_id,
                runner = job.runner if job.runner is not None else "magnus",
                token = job.user.token if job.user.token is not None else "",
            )

        self._clean_up_working_table(job.id)
        job.status = JobStatus.PAUSED
        job.slurm_job_id = None
        job.start_time = None
        db.commit()
