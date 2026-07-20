# back_end/server/schemas/_service.py
"""Service create / response schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from ..models import JobType
from ._user import UserInfo
from ._job import JobListItem


class ServiceCreate(BaseModel):
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
    entry_command: str
    gpu_count: int = 0
    gpu_type: str
    job_type: JobType = JobType.A2
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    ephemeral_storage: Optional[str] = None
    runner: Optional[str] = None
    container_image: Optional[str] = None
    system_entry_command: Optional[str] = None


class ServiceResponse(ServiceCreate):
    owner_id: str
    last_activity_time: datetime
    current_job_id: Optional[str] = None
    assigned_port: Optional[int] = None
    current_job: Optional[JobListItem] = None
    owner: Optional[UserInfo] = None
    updated_at: datetime
    can_manage: bool = False
    class Config: from_attributes = True


class PagedServiceResponse(BaseModel):
    total: int
    items: List[ServiceResponse]
