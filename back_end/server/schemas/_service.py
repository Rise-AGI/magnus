# back_end/server/schemas/_service.py
"""Service create / response schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from ..models import JobType
from ._user import UserInfo
from ._job import JobListItem


class _ServiceContent(BaseModel):
    # 命令类大字段：只进详情，不进列表轻量投影
    entry_command: str
    system_entry_command: Optional[str] = None


class ServiceBase(BaseModel):
    id: str = Field(..., description="Slug ID for the service")
    name: str
    description: Optional[str] = None
    is_active: bool = True
    request_timeout: int = 60
    idle_timeout: int = 30
    max_concurrency: int = 64
    job_task_name: str
    job_description: str
    namespace: str
    repo_name: str
    branch: str
    commit_sha: str
    gpu_count: int = 0
    gpu_type: str
    job_type: JobType = JobType.A2
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    ephemeral_storage: Optional[str] = None
    runner: Optional[str] = None
    container_image: Optional[str] = None


class ServiceCreate(ServiceBase, _ServiceContent):
    pass


class ServiceListItem(ServiceBase):
    """列表视图的轻量投影：省掉命令类大字段（entry_command / system_entry_command）。
    详情 GET /services/{id} 才带全。与 job / blueprint / skill 列表同型。"""
    owner_id: str
    last_activity_time: datetime
    current_job_id: Optional[str] = None
    assigned_port: Optional[int] = None
    current_job: Optional[JobListItem] = None
    owner: Optional[UserInfo] = None
    updated_at: datetime
    can_manage: bool = False
    class Config: from_attributes = True


class ServiceResponse(ServiceListItem, _ServiceContent):
    """完整视图（详情 / 创建返回），在轻量投影之上补齐命令类字段。"""
    pass


class PagedServiceResponse(BaseModel):
    total: int
    items: List[ServiceListItem]
