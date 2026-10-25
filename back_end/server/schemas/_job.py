# back_end/server/schemas/_job.py
"""Job submission / response schemas."""
from datetime import datetime
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from ..models import JobType, JobStatus
from .._job_resources import RESOURCE_MODES, ResourceMode, requested_job_resources, resource_mode_for_execution
from ._user import UserInfo


class JobSubmission(BaseModel):
    execution_target: Optional[str] = None
    task_name: str
    entry_command: str
    repo_name: Optional[str] = None
    branch: Optional[str] = None            # None = repository default branch
    commit_sha: Optional[str] = None        # None = HEAD
    gpu_type: str = "cpu"
    description: Optional[str] = None
    namespace: str = "Rise-AGI"
    gpu_count: int = 0
    job_type: JobType = JobType.A2
    container_image: Optional[str] = None
    dockerfile: Optional[str] = None
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    node_count: Optional[int] = None        # 申请节点数（SLURM 多节点）。None = 单节点（历史行为）
    tasks_per_node: Optional[int] = None    # 每节点 MPI rank 数。None = 1
    time_limit: Optional[int] = None        # 期望最大墙钟（分钟）。None = 站点分区默认墙钟
    ephemeral_storage: Optional[str] = None
    runner: Optional[str] = None
    system_entry_command: Optional[str] = None

    @field_validator("description", "entry_command", "system_entry_command", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _validate_sources(self):
        if self.dockerfile is not None:
            if not self.dockerfile.strip():
                raise ValueError("dockerfile must not be empty")
            if self.container_image is not None:
                raise ValueError("Provide either container_image or dockerfile, not both")
        if not self.repo_name or not self.repo_name.strip():
            if self.namespace != "Rise-AGI":
                raise ValueError("repo_name is required with a custom namespace")
            self.repo_name = "magnus"
        return self


class JobResourceSummary(BaseModel):
    """Whole-job display values; raw submission fields keep their original units."""
    basis: Literal["request", "allocation"]
    mode: ResourceMode
    mode_inferred: bool
    cpu_count: Optional[int]
    memory_bytes: Optional[int]
    gpu_count: int
    node_count: Optional[int]
    tasks_per_node: Optional[int]
    allocation_inferred: bool = False
    cpu_source: Optional[str] = None
    memory_source: Optional[str] = None
    gpu_source: Optional[str] = None
    slurm_state: Optional[str] = None


class JobListItem(BaseModel):
    """列表 / 看板 / 内嵌视图里的轻量 job 投影。

    刻意不含重 Text 列（命令、Dockerfile、内部构建结果、result / action）——
    它们单行可达几十 MB（例如内联大 prompt 的批量 job），一页 100 行
    就会序列化上 GB，并发轮询下拖垮整站。列表 UI 从不渲染这些列；需要它们的详情
    视图走 GET /jobs/{id}（返回完整 ``JobResponse``）按需取。查询侧配合
    ``models.job_list_load_options`` defer 掉这些列，做到既不读盘也不序列化。
    """
    id: str
    task_name: str
    description: Optional[str] = None
    user_id: str
    namespace: str
    repo_name: str
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    container_image: Optional[str] = None
    execution_target: Optional[str] = None
    has_dockerfile: bool = False
    gpu_count: int
    gpu_type: str
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    node_count: Optional[int] = None
    tasks_per_node: Optional[int] = None
    resource_mode: Optional[str] = Field(default=None, exclude=True, repr=False)
    resource_summary: Optional[JobResourceSummary] = None
    time_limit: Optional[int] = None
    ephemeral_storage: Optional[str] = None
    runner: Optional[str] = None
    job_type: JobType
    status: JobStatus
    slurm_job_id: Optional[str] = None
    start_time: Optional[datetime] = None
    created_at: datetime
    user: Optional[UserInfo] = None
    class Config: from_attributes = True

    @model_validator(mode="after")
    def _summarize_resources(self):
        if self.resource_summary is None:
            mode = self.resource_mode
            inferred = mode not in RESOURCE_MODES
            if inferred:
                # A legacy row has no historical snapshot. Expose this inference
                # explicitly instead of silently rewriting its stored request.
                from .._magnus_config import magnus_config
                mode = resource_mode_for_execution(magnus_config["execution"])
            self.resource_summary = JobResourceSummary(**requested_job_resources(
                cpu_count = self.cpu_count,
                memory_demand = self.memory_demand,
                gpu_count = self.gpu_count,
                node_count = self.node_count,
                tasks_per_node = self.tasks_per_node,
                mode = mode,
                mode_inferred = inferred,
            ))
        return self

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
        """Cancellation/preemption is awaiting release or final artifact transfer.

        A transfer retry may outlast Slurm allocation; this lifecycle flag must
        not be used as evidence of resource occupancy.

        在后端这里集中派生避免前端 / SDK 各处重复 ``status × slurm_job_id`` 推断
        而漏掉某个 surface（review 里已经踩过 4 处遗漏）。前端直接读这个字段做
        UX 决策（badge 显示 "Releasing"、隐藏重复终结按钮等）。Docker (local)
        模式下 terminate_job 立即清 slurm_job_id，所以这里恒为 ``False``。
        """
        return self.slurm_job_id is not None and self.status in (JobStatus.TERMINATED, JobStatus.PAUSED)


class JobResponse(JobListItem):
    """完整 job 视图（详情 / 提交返回）。补齐公开的重 Text 字段，
    仅用于单条 job 的 endpoint，绝不进列表；build_result 是内部状态。"""
    entry_command: str
    dockerfile: Optional[str] = None
    system_entry_command: Optional[str] = None
    result: Optional[str] = None
    action: Optional[str] = None


class PagedJobResponse(BaseModel):
    total: int
    items: List[JobListItem]
