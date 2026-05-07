# back_end/server/_scheduler/_decisions.py
"""调度决策：按 (job_type 优先级, 创建时间) 排序取队头提交，A 类任务可抢占 B 类。"""
import asyncio
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import Job, JobStatus, JobType
from .._magnus_config import is_local_mode
from . import logger


class _DecisionsMixin:

    async def _make_decisions(self):
        """
        调度决策 - 队头挂号模式

        状态流转：Preparing → Pending → Queued → Running
        - Preparing: 系统正在准备资源（镜像、仓库）
        - Pending: 资源就绪，等待调度决策
        - Queued: 已提交到 SLURM，等待执行
        - Running: SLURM 正在执行

        核心逻辑：
        1. 新任务以 Preparing 状态进入，启动异步资源准备
        2. 资源准备完成后变为 Pending
        3. 调度器从 Pending 任务中选择队头提交到 SLURM
        4. A 类任务可以抢占 RUNNING 的 B 类任务
        5. 被抢占的 B 类任务回到 Preparing 重新准备资源
        """
        with SessionLocal() as db:
            priority_map = {
                JobType.A1: 4, JobType.A2: 3,
                JobType.B1: 2, JobType.B2: 1,
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

            # Phase 3: 调度 Pending 任务（资源已就绪，等待提交到 SLURM）
            schedulable_jobs = db.query(Job).filter(
                Job.status == JobStatus.PENDING
            ).all()

            if not schedulable_jobs:
                return

            # 按优先级排序
            schedulable_jobs.sort(
                key = lambda x: (priority_map.get(x.job_type, 0), -x.created_at.timestamp()),
                reverse = True,
            )

            head_job = schedulable_jobs[0]

            # 如果队头是 A 类任务，检查是否需要抢占 RUNNING 的 B 类任务
            if not is_local_mode and head_job.job_type in [JobType.A1, JobType.A2]:
                self._handle_preemption_for_job(db, head_job)

            if is_local_mode:
                # local 模式：直接提交，不排队
                self._submit_to_docker(db, head_job)
            else:
                # 只有当没有任务在 SLURM 队列中等待时，才提交队头
                slurm_queued_count = db.query(Job).filter(Job.status == JobStatus.QUEUED).count()

                if slurm_queued_count == 0:
                    self._submit_to_slurm(db, head_job)

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
