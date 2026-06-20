# back_end/server/_scheduler/_decisions.py
"""调度决策：按 cluster.scheduling.mode 切换 —— authoritative 跑 EASY backfill
（队头优先级保留，后续不延迟队头者旁路启动），tenant 按优先级序 eager 提交、把排队
交给外部 SLURM 的 fairshare。"""
import asyncio
from typing import TYPE_CHECKING, List, Tuple

from sqlalchemy.orm import Session

from library.fundamental.scheduling import (
    BackfillCandidate,
    ResourceVector,
    select_easy_backfill,
)
from ..database import SessionLocal
from ..models import Job, JobStatus, JobType
from .._magnus_config import is_local_mode, magnus_config
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
        调度决策

        提交策略由 cluster.scheduling.mode 选择（默认 authoritative）：
        - authoritative：magnus 独占集群，自己算全集群 free + EASY backfill + A 抢 B，
          SLURM 只接收已验证过资源的提交（独占集群，下方详述）。
        - tenant：magnus 是共享集群的租户、只有 QOS 配额，按优先级序 eager 提交所有
          pending，把排队/backfill 交给外部 SLURM 的 fairshare（共享集群租户）。

        以下 backfill / 抢占描述针对 authoritative 模式。

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

            # Phase 1: 启动 Preparing 任务的资源准备。check-then-set 在锁内做，与
            # terminate_job（线程池）的 pop 原子互斥；create_task 是同步的（仅在 loop
            # 上排程），锁内调用无阻塞风险。
            preparing_jobs = db.query(Job).filter(Job.status == JobStatus.PREPARING).all()
            for job in preparing_jobs:
                with self._preparing_jobs_lock:
                    if job.id in self.preparing_jobs:
                        continue
                    self.preparing_jobs[job.id] = asyncio.create_task(self._prepare_job_resources(job.id))
                logger.info(f"Job {job.id} started resource preparation")

            # Phase 2: 清理已完成的 preparing tasks。锁内 snapshot + 摘出已完成 task，
            # 锁外再处理其结果 —— task.exception() / db.commit 不能在锁内（会把锁占给慢
            # I/O，阻塞 terminate_job 的线程池访问）。terminate_job 取消的 task 已被它自己
            # 摘出，这里见不到；对仍在此的 cancelled task 跳过（task.exception() 会抛
            # CancelledError）。pop 用默认值兜底，防与 terminate_job 的竞态。
            with self._preparing_jobs_lock:
                done_tasks = [(jid, task) for jid, task in self.preparing_jobs.items() if task.done()]
                for jid, _ in done_tasks:
                    self.preparing_jobs.pop(jid, None)
            for jid, task in done_tasks:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.error(f"Job {jid} preparation crashed: {exc}")
                    # _clean_up_working_table 在远端执行下含 ssh `rm -rf`（+ 可能触发 socket
                    # 重建），这里跑在事件循环上，丢线程池避免阻塞 loop。
                    await asyncio.to_thread(self._clean_up_working_table, jid)
                    failed_job = db.query(Job).filter(Job.id == jid).first()
                    if failed_job and failed_job.status == JobStatus.PREPARING:
                        failed_job.status = JobStatus.FAILED
                        failed_job.result = f"Resource preparation crashed: {exc}"
                        db.commit()

            # Phase 2.5: 抢占恢复 — PAUSED 任务重新准备资源（镜像/仓库在抢占时已被清理）。
            # `slurm_job_id IS NULL` 过滤把 SLURM 仍在 CG (COMPLETING) 阶段释放
            # 旧 step 的 paused job 排除掉——那种 job slurm_id 还在，资源未真正
            # 释放，立即 resubmit 会让旧+新两个 SLURM step 同时占资源 + 让 cluster
            # endpoint 在新 slurm_id 写入后丢失旧 slurm_id 的 magnus 关联。等
            # _sync_reality_slurm 在 SLURM 真正释放后清空 slurm_job_id 才进这里。
            paused_jobs = db.query(Job).filter(
                Job.status == JobStatus.PAUSED,
                Job.slurm_job_id.is_(None),
            ).all()
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

            scheduling_mode = magnus_config["cluster"]["scheduling"]["mode"]

            # 抢占只在 authoritative（magnus 独占集群）下成立：队头是 A 类时先抢占
            # B 类释放 GPU，让 backfill 拿到可能更大的 free。tenant 模式我们是共享
            # 集群的租户、无权也不该抢占（连自己的 job 也交给外部 SLURM 调度）；
            # local 模式无 SLURM。
            if (
                not is_local_mode
                and scheduling_mode == "authoritative"
                and head_job.job_type in [JobType.A1, JobType.A2]
            ):
                self._handle_preemption_for_job(db, head_job)

            if is_local_mode:
                # local 模式只跑队头，docker run 自身非阻塞，多容器自然并行
                submit_ids = [head_job.id]
            elif scheduling_mode == "tenant":
                # tenant 模式：magnus 只有 QOS 配额、不掌握集群。不算全集群 free、
                # 不 backfill、不抢占 —— 那些只对 magnus 独占集群成立；作为租户，
                # 全集群 squeue 给的是全站所有租户的占用，算 free 既不准也不该用，
                # magnus 侧 backfill/抢占更是僭越。改为按 magnus 优先级序 eager 提交
                # 所有 pending job，交给外部 SLURM 自己的 fairshare + backfill 排队。
                # SLURM 兜底分两种：运行类配额（MaxJobs / GrpTRES）超额时 sbatch 收下
                # 并挂 PENDING 等待；但 QOS 提交条数上限（MaxSubmitJobs，上限通常为数千）
                # 超额时 sbatch 直接拒绝，该 job 经 _submit_to_slurm 的异常分支标 FAILED
                # （正常用量远不及该上限，不为此加重试）。
                submit_ids = [job.id for job in schedulable_jobs]
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
                submit_ids = [job.id for job in selected]

            # 提交含远端 scp staging（SIF/repo 推共享盘）+ sbatch —— 远端站点是秒级阻塞
            # subprocess，必须丢 worker thread，否则卡死事件循环上的所有 API/WS。本机/
            # Docker 也走这条，统一且无害。提交器自带 session，不碰这里的 db。
            if submit_ids:
                await asyncio.to_thread(self._submit_jobs, submit_ids)

    def _submit_jobs(self, job_ids: List[str]) -> None:
        """在 worker thread 里提交一批 job（由 _make_decisions 经 asyncio.to_thread 调用）。

        提交链路含阻塞调用 —— 远端站点下要 scp 把 SIF/repo 推到共享盘、再 sbatch over
        socket，单个可达数秒；必须离开事件循环，否则卡死所有 API/WS。本方法**自有
        session**（绝不碰调度循环的 db），逐个按 id 重新载入再按后端提交，没有跨线程
        共享 ORM 对象。"""
        with SessionLocal() as db:
            for job_id in job_ids:
                job = db.query(Job).filter(Job.id == job_id).first()
                if job is None:
                    continue
                if is_local_mode:
                    self._submit_to_docker(db, job)
                else:
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
        # slurm_job_id 不在这里清空：scancel 后 SLURM job 进入 CG (COMPLETING)
        # 阶段跑 epilog（含 GPU reset 等），可能持续数十秒。期间 cluster endpoint
        # 看 squeue 还能见到这个 slurm_job_id，若 magnus 端立即清空，cluster 的
        # magnus_job_map 找不到映射，会把该 inflight job 错显示成 external "(slurm)"
        # 任务。同时若 _make_decisions Phase 2.5 立即 resubmit，旧 SLURM step
        # 仍在 hold 资源，新 step 无法调度且新 slurm_id 写入后旧 slurm_id 永久
        # 丢失 magnus 关联。改由 _sync_reality_slurm 在 SLURM 真正报终态后清空，
        # Phase 2.5 用 `slurm_job_id IS None` 过滤等真正释放再 resubmit。
        job.start_time = None
        db.commit()
