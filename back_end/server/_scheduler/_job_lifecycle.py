# back_end/server/_scheduler/_job_lifecycle.py
import os
import traceback
from typing import Optional
from pywheels.file_tools import delete_file
from ..models import Job, JobStatus
from . import logger, magnus_workspace_path


class _JobLifecycleMixin:
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
        try:
            delete_file(os.path.join(job_working_table, "repository"))
            delete_file(os.path.join(job_working_table, "wrapper.py"))
            delete_file(os.path.join(job_working_table, ".magnus_success"))
            delete_file(os.path.join(job_working_table, ".magnus_oom"))
            delete_file(os.path.join(job_working_table, ".magnus_user_script.sh"))
            # apptainer overlay create 在不同 apptainer 版本下落盘文件名不同：
            # 部分版本写 ephemeral_overlay.img，部分版本自动追加 .ext3 后缀。
            # 双删兜住两种情况，避免漏掉孤儿 sparse 文件。
            delete_file(os.path.join(job_working_table, "ephemeral_overlay.img"))
            delete_file(os.path.join(job_working_table, "ephemeral_overlay.img.ext3"))
            delete_file(os.path.join(job_working_table, ".magnus_tmp"))
            delete_file(os.path.join(job_working_table, ".magnus_cache"))
            # metrics/ 不清理，与 slurm/output.txt 同策略，供 job 结束后回看
        except Exception as error:
            logger.warning(f"Clean up working table of job {job_id} failed:\n{error}\nTraceback:\n{traceback.format_exc()}")
