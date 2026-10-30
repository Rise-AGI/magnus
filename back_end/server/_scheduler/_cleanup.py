"""Durable terminal cleanup with capacity independent of artifact transfers."""

import asyncio
from time import monotonic
from typing import TYPE_CHECKING

from .._resource_manager._helpers import _run_blocking_io
from ..database import SessionLocal
from ..models import Job
from . import logger

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _CleanupMixinBase = _SchedulerProtocol
else:
    _CleanupMixinBase = object


class _CleanupMixin(_CleanupMixinBase):
    async def _cleanup_tick(
        self,
    ) -> None:
        if self.enabled:
            candidates = await _run_blocking_io(self._cleanup_candidates)
            self._schedule_cleanup(candidates)

    def _cleanup_candidates(
        self,
    ) -> dict[str, str | None]:
        with SessionLocal() as db:
            return dict(self._job_query(db, Job.id, Job.slurm_job_id).filter(
                Job.finalization_status == "CLEANUP",
                Job.submission_attempt.is_(None),
            ).all())

    def _schedule_cleanup(
        self,
        candidates: dict[str, str | None],
    ) -> None:
        for job_id, task in list(self._cleanup_tasks.items()):
            if not task.done():
                continue
            del self._cleanup_tasks[job_id]
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as error:
                logger.warning("Job %s cleanup worker failed (%s)", job_id, type(error).__name__)
            self._cleanup_retry_after[job_id] = monotonic() + 30
        self._cleanup_retry_after = {
            job_id: deadline
            for job_id, deadline in self._cleanup_retry_after.items()
            if job_id in candidates or job_id in self._cleanup_tasks
        }
        # One queued/active task per target; the database is the restart queue.
        # Old retry deadlines rotate progressing Jobs behind untouched ones.
        if self._cleanup_tasks:
            return
        for job_id in sorted(candidates, key=lambda item: self._cleanup_retry_after.get(item, 0)):
            if monotonic() < self._cleanup_retry_after.get(job_id, 0):
                continue
            self._cleanup_tasks[job_id] = asyncio.create_task(
                self._cleanup_in_slot(job_id, candidates[job_id]),
            )
            return

    async def _cleanup_in_slot(
        self,
        job_id: str,
        external_id: str | None,
    ) -> None:
        async with self._cleanup_channel_slots:
            async with self._cleanup_slots:
                await _run_blocking_io(
                    self._finish_job_cleanup,
                    job_id,
                    external_id,
                )

    async def shutdown_cleanup(
        self,
    ) -> None:
        tasks = list(self._cleanup_tasks.values())
        for task in tasks:
            task.cancel()
        # _run_blocking_io retains ownership until the filesystem worker exits.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_tasks.clear()
