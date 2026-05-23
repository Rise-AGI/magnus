# back_end/server/_scheduler/_job_lifecycle.py
import os
import traceback
from typing import TYPE_CHECKING, Optional
from pywheels.file_tools import delete_file
from ..models import Job, JobStatus
from . import logger, magnus_workspace_path, magnus_ephemeral_workspace_path

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _JobLifecycleMixinBase = _SchedulerProtocol
else:
    _JobLifecycleMixinBase = object


# Entries under a job's working table that the persistence protocol keeps for
# post-completion reads (see routers/jobs.py + routers/metrics.py):
#   slurm/          — SLURM stdout/stderr (slurm/output.txt)
#   metrics/        — Magnus Metrics Protocol v1 JSONL files
#   .magnus_result  — MAGNUS_RESULT, task result content
#   .magnus_action  — MAGNUS_ACTION, client action instruction
# Everything else (repository, wrapper.py, transient markers, ephemeral overlay,
# and anything the user wrote into the workspace bind mount) is removed on cleanup.
_WORKING_TABLE_KEEP = frozenset({"slurm", "metrics", ".magnus_result", ".magnus_action"})


class _JobLifecycleMixin(_JobLifecycleMixinBase):
    """Job 收尾相关：success/result 标记、OOM 检测、working table 清理。"""

    def _write_success_marker(self, job_id: str) -> None:
        """Write success marker from the host side (symmetric with HPC wrapper.py behavior)."""
        marker_path = f"{magnus_workspace_path}/jobs/{job_id}/.magnus_success"
        with open(marker_path, "w") as f:
            f.write("success")

    def _finalize_completed_job(self, job: Job) -> None:
        marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
        if os.path.exists(marker_path):
            logger.info(f"Job {job.id} completed successfully (Marker Verified).")
            job.status = JobStatus.SUCCESS
            result_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_result"
            job.result = ".magnus_result" if os.path.exists(result_path) else None
            action_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_action"
            job.action = ".magnus_action" if os.path.exists(action_path) else None
        else:
            logger.warning(f"Job {job.id} completed but NO success marker found. Marking FAILED.")
            job.status = JobStatus.FAILED
            if self._has_oom_marker(job.id):
                job.result = self._format_oom_message(job.memory_demand)
            else:
                job.result = "Job process exited but did not report success (no success marker found)"
        job.slurm_job_id = None
        self._clean_up_working_table(job.id)

    @staticmethod
    def _format_oom_message(memory_demand: Optional[str]) -> str:
        # New jobs always have memory_demand filled (router defaults); guard for legacy NULL rows.
        shown = memory_demand if memory_demand is not None else "unspecified"
        return f"Out of memory: job exceeded its memory limit (memory_demand={shown})"

    @staticmethod
    def _has_oom_marker(job_id: str) -> bool:
        marker_path = f"{magnus_workspace_path}/jobs/{job_id}/.magnus_oom"
        return os.path.exists(marker_path)

    def _clean_up_working_table(self, job_id: str) -> None:
        job_working_table = f"{magnus_workspace_path}/jobs/{job_id}"
        job_ephemeral_table = f"{magnus_ephemeral_workspace_path}/jobs/{job_id}"
        try:
            # Keep-whitelist: remove everything under the working table except the
            # protocol artifacts. This also reclaims anything the user wrote into the
            # workspace bind mount (e.g. checkpoints / outputs dumped straight into
            # $MAGNUS_HOME/workspace instead of going through file_custody), which the
            # old fixed delete-list silently let accumulate forever.
            if os.path.isdir(job_working_table):
                for entry in os.listdir(job_working_table):
                    if entry not in _WORKING_TABLE_KEEP:
                        delete_file(os.path.join(job_working_table, entry))
            # When ephemeral_root is split onto a separate disk, the overlay +
            # apptainer tmp/cache live outside the working table; drop that dir
            # wholesale (it holds only transient artifacts). When it coincides with
            # the working table, the keep-whitelist sweep above already removed them.
            if job_ephemeral_table != job_working_table:
                delete_file(job_ephemeral_table)
        except Exception as error:
            logger.warning(f"Clean up working table of job {job_id} failed:\n{error}\nTraceback:\n{traceback.format_exc()}")
