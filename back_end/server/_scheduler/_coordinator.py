"""Route immutable Job execution identities to independent scheduler workers."""
import asyncio
import os
from datetime import datetime, timezone

from .._execution_targets import select_execution_target
from .._magnus_config import configured_execution_targets
from .._resource_manager._helpers import _run_blocking_io
from .._file_custody_manager import file_custody_manager
from .._lifecycle import cancel_background_tasks, join_background_tasks
from ..database import SessionLocal
from ..models import Job, JobStatus
from . import logger
from ._core import MagnusScheduler
from ._workspace_gc import _WorkspaceGCMixin


class SchedulerCoordinator(_WorkspaceGCMixin):
    def __init__(
        self,
    ) -> None:
        self.workers = {
            target_id: MagnusScheduler(target)
            for target_id, target in configured_execution_targets.items()
        }
        channels = {}
        for worker in self.workers.values():
            transport = worker.config.get("transport", {})
            if transport.get("mode") == "ssh":
                socket = os.path.abspath(os.path.expanduser(transport["ssh"]["control_path"]))
                slots, bulk = channels.setdefault(socket, (asyncio.Semaphore(2), asyncio.Semaphore(1)))
                worker._artifact_recovery_slots = slots
                worker._custody_bulk_slots = bulk
        self._ticks: dict[str, asyncio.Task] = {}
        self._custody_task: asyncio.Task | None = None
        self._gc_task: asyncio.Task | None = None
        self.last_workspace_gc_time = datetime.min.replace(tzinfo=timezone.utc)

    def for_target(
        self,
        target_id: str | None,
    ) -> MagnusScheduler:
        target = select_execution_target(configured_execution_targets, target_id)
        return self.workers[target.id]

    def inflight_job_ids(
        self,
    ) -> set[str]:
        return set().union(*(worker.inflight_job_ids() for worker in self.workers.values()))

    async def tick(
        self,
    ) -> None:
        # Each target gets at most one reconciliation pass in flight. A slow
        # filesystem or authentication failure must not stall another cluster.
        for target_id, worker in self.workers.items():
            task = self._ticks.get(target_id)
            if task is None or task.done():
                self._consume_result(task)
                self._ticks[target_id] = asyncio.create_task(worker.tick())
        if self._custody_task is None or self._custody_task.done():
            self._consume_result(self._custody_task)
            self._custody_task = asyncio.create_task(_run_blocking_io(self._finish_custody_jobs))
        if self._gc_task is None or self._gc_task.done():
            self._consume_result(self._gc_task)
            self._gc_task = asyncio.create_task(_run_blocking_io(self._reclaim_workspace_over_cap))

    def _finish_custody_jobs(
        self,
    ) -> None:
        pending = file_custody_manager.pending_job_ids()
        if not pending:
            return
        with SessionLocal() as db:
            rows = db.query(Job.id, Job.status, Job.slurm_job_id, Job.finalization_status).filter(Job.id.in_(pending)).all()
        for job_id, status, allocation, finalization in rows:
            if status in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TERMINATED) and (
                finalization is not None or allocation is None
            ):
                file_custody_manager.finish_job(job_id)

    @staticmethod
    def _consume_result(
        task: asyncio.Task | None,
    ) -> None:
        if task is None or task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Scheduler background pass failed: %s", error)

    def terminate_job(
        self,
        db,
        job,
    ) -> None:
        self.for_target(job.execution_target).terminate_job(db, job)

    def signal_job(
        self,
        job,
    ) -> None:
        self.for_target(job.execution_target).signal_job(job)

    def restore_pins(
        self,
        rows,
    ) -> None:
        for job_id, target_id in rows:
            worker = self.for_target(target_id)
            with worker._preparing_jobs_lock:
                worker._build_pinned_jobs.add(job_id)

    async def shutdown(
        self,
    ) -> None:
        tasks = list(self._ticks.values())
        if self._gc_task is not None:
            tasks.append(self._gc_task)
        if self._custody_task is not None:
            tasks.append(self._custody_task)
        try:
            await cancel_background_tasks(tasks)
        finally:
            await join_background_tasks([
                asyncio.create_task(worker.shutdown()) for worker in self.workers.values()
            ])
