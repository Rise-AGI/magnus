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
        tasks = list(self._artifact_recovery_tasks.values())
        for task in tasks:
            task.cancel()
        # Cancellation joins an active filesystem worker before custody closes.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._artifact_recovery_tasks.clear()
