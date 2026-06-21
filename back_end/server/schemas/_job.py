# back_end/server/schemas/_job.py
"""Job submission / response schemas."""
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, computed_field, field_validator

from ..models import JobType, JobStatus
from ._user import UserInfo


class JobSubmission(BaseModel):
    task_name: str
    entry_command: str
    repo_name: str
    branch: Optional[str] = None            # None = fallback: main → master → default
    commit_sha: Optional[str] = None        # None = HEAD
    gpu_type: str = "cpu"
    description: Optional[str] = None
    namespace: str = "Rise-AGI"
    gpu_count: int = 0
    job_type: JobType = JobType.A2
    container_image: Optional[str] = None
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    time_limit: Optional[int] = None        # 期望最大墙钟（分钟）。None = 站点分区默认墙钟
    ephemeral_storage: Optional[str] = None
    runner: Optional[str] = None
    system_entry_command: Optional[str] = None

    @field_validator("description", "entry_command", "system_entry_command", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v


class JobResponse(JobSubmission):
    id: str
    user_id: str
    status: JobStatus
    slurm_job_id: Optional[str] = None
    start_time: Optional[datetime] = None
    created_at: datetime
    user: Optional[UserInfo] = None
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    runner: Optional[str] = None
    result: Optional[str] = None
    action: Optional[str] = None
    class Config: from_attributes = True

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, v: Any) -> Any:
        """QUEUED 是调度器内部状态，API 层简并为 PENDING"""
        if v == JobStatus.QUEUED or v == "Queued":
            return JobStatus.PENDING
        return v

    @computed_field
    @property
    def is_releasing(self) -> bool:
        """``True`` ⇔ scancel 已发但 SLURM 还在 CG (COMPLETING) 阶段持有资源
        （SLURM 模式 inflight 子态，详见 ``models/_job.py`` JobStatus docstring）。

        在后端这里集中派生避免前端 / SDK 各处重复 ``status × slurm_job_id`` 推断
        而漏掉某个 surface（review 里已经踩过 4 处遗漏）。前端直接读这个字段做
        UX 决策（badge 显示 "Releasing"、隐藏重复终结按钮等）。Docker (local)
        模式下 terminate_job 立即清 slurm_job_id，所以这里恒为 ``False``。
        """
        return self.slurm_job_id is not None and self.status in (JobStatus.TERMINATED, JobStatus.PAUSED)


class PagedJobResponse(BaseModel):
    total: int
    items: List[JobResponse]
