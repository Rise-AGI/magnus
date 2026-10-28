"""Bounded artifact workers, independent of scheduler observations and admission."""
import asyncio
from time import monotonic
from typing import TYPE_CHECKING

from .._resource_manager._helpers import _run_blocking_io
from . import logger

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _ArtifactRecoveryMixinBase = _SchedulerProtocol
else:
    _ArtifactRecoveryMixinBase = object


class _ArtifactRecoveryMixin(_ArtifactRecoveryMixinBase):
    def _schedule_artifact_recovery(
        self,
        candidates: dict[str, str | None],
    ) -> None:
        for job_id, task in list(self._artifact_recovery_tasks.items()):
            if not task.done():
                continue
            del self._artifact_recovery_tasks[job_id]
            try:
                delay = task.result()
            except asyncio.CancelledError:
                delay = 60
            except Exception as error:
                logger.warning("Job %s artifact recovery failed (%s)", job_id, type(error).__name__)
                delay = 60
            self._artifact_retry_after[job_id] = monotonic() + delay

        for job_id, task in list(self._custody_bulk_tasks.items()):
            if task.done():
                del self._custody_bulk_tasks[job_id]
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("Job %s custody bulk worker failed", job_id)
                elif not task.cancelled() and task.result():
                    self._artifact_retry_after.pop(job_id, None)

        retained = candidates.keys() | self._artifact_recovery_tasks.keys()
        self._artifact_retry_after = {
            job_id: deadline
            for job_id, deadline in self._artifact_retry_after.items()
            if job_id in retained
        }
        for job_id, slurm_job_id in candidates.items():
            if job_id in self._artifact_recovery_tasks:
                continue
            if monotonic() < self._artifact_retry_after.get(job_id, 0):
                continue
            self._artifact_recovery_tasks[job_id] = asyncio.create_task(
                self._recover_artifacts_in_slot(job_id, slurm_job_id),
            )

        for job_id in sorted(self._custody_relay.bulk_job_ids()):
            if job_id in candidates and job_id not in self._custody_bulk_tasks:
                self._custody_bulk_tasks[job_id] = asyncio.create_task(self._transfer_custody_bulk(job_id))

    async def _transfer_custody_bulk(
        self,
        job_id: str,
    ) -> bool:
        async with self._custody_bulk_slots:
            return await _run_blocking_io(self._custody_relay.transfer_bulk, job_id)

    async def _recover_artifacts_in_slot(
        self,
        job_id: str,
        slurm_job_id: str | None,
    ) -> int:
        async with self._artifact_recovery_slots:
            return await _run_blocking_io(
                self._recover_job_artifacts,
                job_id,
                slurm_job_id,
            )

    async def shutdown_artifact_recovery(
        self,
    ) -> None:
        self._custody_relay.cancel_transfers()
        tasks = [*self._artifact_recovery_tasks.values(), *self._custody_bulk_tasks.values()]
        for task in tasks:
            task.cancel()
        # Cancellation joins an active filesystem worker before custody closes.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._artifact_recovery_tasks.clear()
        self._custody_bulk_tasks.clear()
