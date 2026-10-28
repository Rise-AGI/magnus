# back_end/server/_scheduler/_job_lifecycle.py
import os
import shutil
import tempfile
import traceback
from typing import TYPE_CHECKING, Optional
from .._protocol_files import open_protocol_file
from .._workspace_cleanup import remove_job_path
from ..database import SessionLocal
from ..models import Job, JobStatus
from .._magnus_config import is_local_mode
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
_WORKING_TABLE_KEEP = frozenset({"slurm", "metrics", ".magnus_result", ".magnus_action", ".magnus_custody_receipts.json"})


class _JobLifecycleMixin(_JobLifecycleMixinBase):
    """Job 收尾相关：success/result 标记、OOM 检测、working table 清理。"""

    def _write_success_marker(self, job_id: str) -> None:
        """Write success marker from the host side (symmetric with HPC wrapper.py behavior)."""
        with open_protocol_file(f"{magnus_workspace_path}/jobs", f"{job_id}/.magnus_success", "wb") as stream:
            stream.write(b"success")

    def _resume_paused_job(
        self,
        job_id: str,
    ) -> bool:
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.status == JobStatus.PAUSED,
                Job.slurm_job_id.is_(None),
                Job.finalization_status.is_(None),
                Job.submission_attempt.is_(None),
            ).update({
                Job.status: JobStatus.PREPARING,
                Job.result: None,
                Job.action: None,
            }, synchronize_session=False)
            if not changed:
                return False
            table = f"{magnus_workspace_path}/jobs/{job_id}"
            os.makedirs(table, exist_ok=True)
            prefix = ".magnus-output-reset-"
            if any(name.startswith(prefix) for name in os.listdir(table)):
                raise RuntimeError("Previous output reset needs recovery before resumption")
            backup = tempfile.mkdtemp(prefix=prefix, dir=table)
            moved = []
            try:
                # Renames stay on the same filesystem and retain the previous
                # output until both marker reset and the DB transition succeed.
                for name in (".magnus_success", ".magnus_result", ".magnus_action", ".magnus_oom"):
                    try:
                        os.replace(os.path.join(table, name), os.path.join(backup, name))
                    except FileNotFoundError:
                        continue
                    moved.append(name)
                self._init_job_working_dir(table)
                db.commit()
            except BaseException:
                for name in reversed(moved):
                    try:
                        os.replace(os.path.join(backup, name), os.path.join(table, name))
                    except OSError:
                        logger.error("Job %s prior output retained for recovery in %s", job_id, backup)
                if not os.listdir(backup):
                    os.rmdir(backup)
                raise
        try:
            shutil.rmtree(backup)
        except OSError:
            logger.warning("Job %s prior output reset backup awaits workspace cleanup", job_id)
        return True

    def _finalize_completed_job(
        self,
        job: Job,
        *,
        cleanup: bool = True,
    ) -> None:
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
        if cleanup:
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

    def _finish_unsubmitted_job(
        self,
        job_id: str,
    ) -> None:
        with SessionLocal() as db:
            changed = self._job_query(db, Job).filter(
                Job.id == job_id,
                Job.status.in_([JobStatus.FAILED, JobStatus.TERMINATED]),
                Job.slurm_job_id.is_(None),
                Job.submission_attempt.is_(None),
                Job.finalization_status.is_(None) | (Job.finalization_status == "CLEANUP"),
            ).update({Job.finalization_status: "CLEANUP"}, synchronize_session=False)
            db.commit()
        if changed:
            self._finish_job_cleanup(job_id, None)

    def _clean_up_working_table(
        self,
        job_id: str,
    ) -> bool:
        with self._preparing_jobs_lock:
            if job_id in self._submitting_jobs:
                return False
        job_working_table = f"{magnus_workspace_path}/jobs/{job_id}"
        job_ephemeral_table = f"{magnus_ephemeral_workspace_path}/jobs/{job_id}"
        try:
            with SessionLocal() as db:
                pending = db.query(
                    Job.finalization_status,
                    Job.slurm_job_id,
                    Job.submission_attempt,
                ).filter(Job.id == job_id).first()
            if pending and (pending[2] is not None or pending[0] not in (None, "CLEANUP")):
                return False
            if pending and is_local_mode and pending[1] is not None and pending[0] != "CLEANUP":
                return False
            if pending and pending[1] is not None and not is_local_mode:
                assert self.slurm_manager is not None
                if not self.slurm_manager.observe_job(pending[1]).allocation_released:
                    return False
            # Keep-whitelist: remove everything under the working table except the
            # protocol artifacts. This also reclaims anything the user wrote into the
            # workspace bind mount (e.g. checkpoints / outputs dumped straight into
            # $MAGNUS_HOME/workspace instead of going through file_custody), which the
            # old fixed delete-list silently let accumulate forever.
            if os.path.isdir(job_working_table):
                for entry in os.listdir(job_working_table):
                    if entry not in _WORKING_TABLE_KEEP and not entry.startswith(".magnus-output-reset-"):
                        path = os.path.join(job_working_table, entry)
                        if os.path.islink(path):
                            os.unlink(path)
                        else:
                            remove_job_path(path)
                        if os.path.lexists(path):
                            raise OSError(f"Could not remove {path}")
            # When ephemeral_root is split onto a separate disk, the overlay +
            # apptainer tmp/cache live outside the working table; drop that dir
            # wholesale (it holds only transient artifacts). When it coincides with
            # the working table, the keep-whitelist sweep above already removed them.
            if job_ephemeral_table != job_working_table:
                if os.path.islink(job_ephemeral_table):
                    os.unlink(job_ephemeral_table)
                else:
                    remove_job_path(job_ephemeral_table)
                if os.path.lexists(job_ephemeral_table):
                    raise OSError(f"Could not remove {job_ephemeral_table}")
            # 远端执行：删掉远端 job 工作区（持久产物已在终态 _stage_out_final 拉回本机，
            # 上面的 keep-whitelist 在本机侧保留）。本机执行下 no-op；远端不存在时 rm -rf
            # 亦无害（覆盖 terminate / preparing 失败等尚未在远端建目录的清理路径）。
            self._cleanup_remote_job(job_id)
            self._staged_job_ids.discard(job_id)
            return True
        except Exception as error:
            logger.warning(f"Clean up working table of job {job_id} failed:\n{error}\nTraceback:\n{traceback.format_exc()}")
            return False
