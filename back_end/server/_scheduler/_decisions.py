# back_end/server/_scheduler/_decisions.py
"""Conservative resource admission and preemption for owned SLURM clusters.

Tenant mode delegates scheduling to SLURM. Owned single-node clusters reserve
the priority head's current free resources; more complex placement is left to
SLURM one head at a time, without aggregate-only preemption or backfill.
"""
import asyncio
from typing import TYPE_CHECKING, List, Tuple

from sqlalchemy.orm import Session

from library.fundamental.scheduling import (
    BackfillCandidate,
    ResourceVector,
    select_conservative_backfill,
)
from ..database import SessionLocal
from ..models import Job, JobStatus, JobType, job_metadata_load_options
from .._magnus_config import is_local_mode, magnus_config
from .._resource_manager._helpers import _run_blocking_io
from .._slurm_manager._resource_query import validate_allocation_visibility
from . import logger
from ._resource_accounting import (
    allocation_resource_vector,
    gpu_dimensions,
    job_resource_target,
    job_resource_vector,
    node_resource_vector,
    select_preemption_victims,
    subtract_allocations,
    target_nodes,
)

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _DecisionsMixinBase = _SchedulerProtocol
else:
    _DecisionsMixinBase = object


class _DecisionsMixin(_DecisionsMixinBase):

    async def _make_decisions(self):
        """Launch async preparation, then decide/submit in a worker-owned session.

        State flow: Preparing / Building -> Pending -> Queued -> Running.
        Tenant mode eagerly submits by priority. Authoritative mode only acts on
        complete live allocation accounting and never treats a queued promise or
        an unknown resource amount as currently free capacity.
        """
        with SessionLocal(expire_on_commit=False) as db:
            # Preparing/Building both recover through the same preparation task.
            # Keep cancelled tasks registered until their filesystem I/O drains.
            # These rows only identify tasks to start. Commands and Dockerfiles
            # are loaded once by each preparation task, not on every tick.
            preparing_jobs = db.query(Job.id).filter(Job.status.in_([JobStatus.PREPARING, JobStatus.BUILDING])).all()
            for job in preparing_jobs:
                with self._preparing_jobs_lock:
                    if job.id in self.preparing_jobs:
                        continue
                    self.preparing_jobs[job.id] = asyncio.create_task(self._prepare_job_resources(job.id))
                logger.info(f"Job {job.id} started resource preparation")

            # Phase 2: 清理已完成的 preparing tasks。锁内 snapshot + 摘出已完成 task，
            # 锁外再处理其结果 —— task.exception() / db.commit 不能在锁内（会把锁占给慢
            # I/O，阻塞 terminate_job 的线程池访问）。取消的 task 在收尾后同样由这里
            # 摘出；跳过 task.exception()，否则它会抛 CancelledError。
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
                    db.query(Job).filter(
                        Job.id == jid,
                        Job.status.in_([JobStatus.PREPARING, JobStatus.BUILDING]),
                    ).update({
                        Job.status: JobStatus.FAILED,
                        Job.result: f"Resource preparation crashed: {exc}",
                    }, synchronize_session=False)
                    db.commit()

            # Phase 2.5: 抢占恢复 — PAUSED 任务重新准备资源（镜像/仓库在抢占时已被清理）。
            # `slurm_job_id IS NULL` 过滤把 SLURM 仍在 CG (COMPLETING) 阶段释放
            # 旧 step 的 paused job 排除掉——那种 job slurm_id 还在，资源未真正
            # 释放，立即 resubmit 会让旧+新两个 SLURM step 同时占资源 + 让 cluster
            # endpoint 在新 slurm_id 写入后丢失旧 slurm_id 的 magnus 关联。等
            # _sync_reality_slurm 在 SLURM 真正释放后清空 slurm_job_id 才进这里。
            paused_jobs = db.query(Job.id).filter(
                Job.status == JobStatus.PAUSED,
                Job.slurm_job_id.is_(None),
                Job.finalization_status.is_(None),
            ).all()
            for job in paused_jobs:
                changed = db.query(Job).filter(
                    Job.id == job.id,
                    Job.status == JobStatus.PAUSED,
                    Job.slurm_job_id.is_(None),
                    Job.finalization_status.is_(None),
                ).update({Job.status: JobStatus.PREPARING}, synchronize_session=False)
                if changed:
                    logger.info(f"Job {job.id} re-entering preparation after preemption")
            if paused_jobs:
                db.commit()

        # Resource queries, preemption and submission are blocking operations.
        # This worker owns its session; ORM objects never cross thread boundaries.
        await _run_blocking_io(self._schedule_pending_jobs)

    def _schedule_pending_jobs(self):
        with SessionLocal(expire_on_commit=False) as db:
            jobs = db.query(Job).options(*job_metadata_load_options()).filter(
                Job.status == JobStatus.PENDING,
            ).all()
            if not jobs:
                return
            priority = {JobType.A1: 4, JobType.A2: 3, JobType.B1: 2, JobType.B2: 1}
            jobs.sort(
                key = lambda job: (priority.get(job.job_type, 0), -job.created_at.timestamp()),
                reverse = True,
            )
            if is_local_mode:
                submit_ids = [jobs[0].id]
            elif magnus_config["cluster"]["scheduling"]["mode"] == "tenant":
                # A shared cluster owns placement, fairshare and preemption.
                submit_ids = [job.id for job in jobs]
            else:
                submit_ids = self._select_authoritative_jobs(db, jobs)
        if submit_ids:
            self._submit_jobs(submit_ids)

    def _select_authoritative_jobs(
        self,
        db: Session,
        jobs: List[Job],
    ) -> List[str]:
        assert self.slurm_manager is not None
        try:
            snapshot = self.slurm_manager.get_node_snapshot(_raise_on_error=True)
            tasks = self.slurm_manager.get_all_running_tasks(_raise_on_error=True)
            validate_allocation_visibility(snapshot, tasks)
            observed_ids = {task["id"] for task in tasks}
            queued = db.query(Job.slurm_job_id).filter(Job.status == JobStatus.QUEUED).all()
            if any(row[0] not in observed_ids for row in queued):
                # A successful sbatch has not appeared in allocated resources yet.
                # Do not promise the same free capacity again on the next tick.
                return []
            gpu_types = gpu_dimensions(snapshot, tasks, jobs, magnus_config)
            total, free = self._compute_cluster_resources(snapshot, tasks, gpu_types)
            head = jobs[0]
            target = job_resource_target(head, magnus_config)
            eligible_nodes = target_nodes(
                snapshot.nodes,
                target,
                for_queue = True,
            )
            node_count = head.node_count or 1
            if len(eligible_nodes) < node_count:
                return []
            demand = job_resource_vector(head, magnus_config, gpu_types)
            if demand is None:
                # Defaults and --mem=0 need SLURM's interpretation. Send only the
                # priority head; the QUEUED barrier above reserves the uncertainty.
                return [head.id]
            scoped_total = node_resource_vector(
                eligible_nodes,
                gpu_types,
                for_queue = True,
            )
            if not demand.fits_within(scoped_total):
                return []
            # Healthy planned/reserved nodes can accept queued work, but their
            # capacity is not currently free and cannot justify speculative kills.
            if any(not node.schedulable for node in eligible_nodes):
                return [head.id]
            if free is None:
                return [head.id]
            simple_topology = (
                snapshot.node_count == 1
                and len(snapshot.nodes) == 1
                and node_count == 1
                and all(task.get("node_count") == 1 for task in tasks)
                and not any(task.get("allocation_inferred", False) for task in tasks)
                and all(task.get("job_state", "RUNNING") == "RUNNING" for task in tasks)
            )
            if not simple_topology:
                # Aggregate capacity proves neither per-node placement nor GPU
                # affinity. Do not kill victims or backfill on that assumption.
                # SLURM performs final placement of this one priority head.
                return [head.id]
            if head.job_type in (JobType.A1, JobType.A2):
                if self._handle_preemption_for_job(
                    db = db,
                    job = head,
                    demand = demand,
                    total = total,
                    free = free,
                    tasks = tasks,
                    gpu_types = gpu_types,
                ):
                    # scancel/CG does not immediately release an allocation.
                    # Await the next real snapshot rather than adding virtual free.
                    return []
            candidates = []
            for job in jobs:
                job_target = job_resource_target(job, magnus_config)
                request = job_resource_vector(job, magnus_config, gpu_types)
                if (
                    request is None
                    or (job.node_count or 1) != 1
                    or not target_nodes(snapshot.nodes, job_target)
                ):
                    break
                candidates.append(BackfillCandidate(
                    payload = job,
                    demand = request,
                ))
            selected = select_conservative_backfill(
                candidates = candidates,
                cluster_total = total,
                cluster_free = free,
            )
            return [job.id for job in selected]
        except Exception as error:
            logger.warning("Skipping authoritative admission: resource accounting unavailable: %s", error)
            return []

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

    def _compute_cluster_resources(
        self,
        snapshot,
        tasks,
        gpu_types,
    ) -> Tuple[ResourceVector, ResourceVector | None]:
        total = node_resource_vector(snapshot.nodes, gpu_types)
        free = subtract_allocations(total, tasks, gpu_types)
        return total, free

    def _handle_preemption_for_job(
        self,
        db: Session,
        job: Job,
        demand: ResourceVector,
        total: ResourceVector,
        free: ResourceVector,
        tasks,
        gpu_types,
    ) -> bool:
        if demand.fits_within(free):
            return False
        allocations = {task["id"]: task for task in tasks}
        running_b_jobs = db.query(Job).options(*job_metadata_load_options()).filter(
            Job.status == JobStatus.RUNNING,
            Job.job_type.in_([JobType.B1, JobType.B2]),
        ).all()
        kill_priority = {JobType.B2: 1, JobType.B1: 0}
        running_b_jobs.sort(
            key = lambda victim: (
                kill_priority.get(victim.job_type, 0),
                victim.start_time.timestamp() if victim.start_time else 0,
            ),
            reverse = True,
        )
        candidates = [
            BackfillCandidate(
                payload = victim,
                demand = allocation_resource_vector(allocations[victim.slurm_job_id], gpu_types),
            )
            for victim in running_b_jobs
            if victim.slurm_job_id in allocations
        ]
        victims = select_preemption_victims(demand, total, free, candidates)
        if not victims:
            return False
        # Validate the complete plan against current DB state before any kill.
        # The original ID must still refer to the same live B allocation.
        db.refresh(job, attribute_names=["status", "job_type"])
        if job.status != JobStatus.PENDING or job.job_type not in (JobType.A1, JobType.A2):
            return False
        for victim in victims:
            allocation_id = victim.slurm_job_id
            planned_type = victim.job_type
            db.refresh(victim, attribute_names=["status", "slurm_job_id", "job_type"])
            if (
                victim.status != JobStatus.RUNNING
                or victim.slurm_job_id != allocation_id
                or victim.job_type != planned_type
                or victim.job_type not in (JobType.B1, JobType.B2)
            ):
                return False
        logger.info("Preemption: job %s reclaiming allocated resources from %s B jobs", job.id, len(victims))
        for victim in victims:
            self._kill_and_pause(db, victim)
        return True

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

        # scancel acknowledges a request, not process/epilog completion. Cleanup
        # is performed by state sync only after the allocation has disappeared.
        # slurm_job_id 不在这里清空：scancel 后 SLURM job 进入 CG (COMPLETING)
        # 阶段跑 epilog（含 GPU reset 等），可能持续数十秒。期间 cluster endpoint
        # 看 squeue 还能见到这个 slurm_job_id，若 magnus 端立即清空，cluster 的
        # magnus_job_map 找不到映射，会把该 inflight job 错显示成 external "(slurm)"
        # 任务。同时若 _make_decisions Phase 2.5 立即 resubmit，旧 SLURM step
        # 仍在 hold 资源，新 step 无法调度且新 slurm_id 写入后旧 slurm_id 永久
        # 丢失 magnus 关联。改由 _sync_reality_slurm 在 SLURM 真正报终态后清空，
        # Phase 2.5 用 `slurm_job_id IS None` 过滤等真正释放再 resubmit。
        # A terminate/completion in another session must not be resurrected by
        # this decision snapshot after the external cancellation has finished.
        db.query(Job).filter(
            Job.id == job.id,
            Job.status == JobStatus.RUNNING,
        ).update({
            Job.status: JobStatus.PAUSED,
            Job.start_time: None,
        }, synchronize_session=False)
        db.commit()
