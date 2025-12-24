# back_end/server/schemas.py
from typing import Any, List, Optional
from datetime import datetime
from pydantic import BaseModel
from .models import JobType, JobStatus


__all__ = [
    "JobSubmission",
    "JobResponse",
    "JobMetricResponse",
    "PagedJobResponse",
    "FeishuLoginRequest",
    "UserInfo",
    "LoginResponse",
    "ClusterStatsResponse",
    "DashboardJobsResponse",
    "BlueprintCreate",
    "BlueprintResponse",
    "PagedBlueprintResponse",
    "BlueprintParamSchema",
]


class UserInfo(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None
    email: Optional[str] = None
    class Config: from_attributes = True


class JobSubmission(BaseModel):
    task_name: str
    description: Optional[str] = None
    namespace: str = "PKU-Plasma"
    repo_name: str
    branch: str
    commit_sha: str
    entry_command: str
    gpu_type: str
    gpu_count: int = 1
    job_type: JobType = JobType.A2
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    runner: Optional[str] = None


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
    class Config: from_attributes = True
    
    
class JobMetricResponse(BaseModel):
    timestamp: datetime
    status_json: str
    class Config: from_attributes = True


class PagedJobResponse(BaseModel):
    total: int
    items: List[JobResponse]


class FeishuLoginRequest(BaseModel):
    code: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo
    
    
class ClusterResources(BaseModel):
    node: str
    gpu_model: str
    total: int
    free: int
    used: int
    class Config: from_attributes = True


class ClusterStatsResponse(BaseModel):
    resources: ClusterResources
    running_jobs: List[JobResponse]
    total_running: int
    pending_jobs: List[JobResponse]
    total_pending: int
    class Config: from_attributes = True
    
    
class DashboardJobsResponse(BaseModel):
    items: List[JobResponse]
    total: int
    
    
class BlueprintCreate(BaseModel):
    id: str
    title: str
    description: str
    code: str


class BlueprintResponse(BaseModel):
    id: str
    title: str
    description: str
    code: str
    user_id: str
    updated_at: datetime
    user: Optional[UserInfo] = None 
    class Config: from_attributes = True


class PagedBlueprintResponse(BaseModel):
    total: int
    items: List[BlueprintResponse]


class BlueprintParamSchema(BaseModel):
    key: str
    label: str
    type: str 
    default: Any = None
    options: Optional[List[str]] = None
    min: Optional[int] = None
    max: Optional[int] = None
    description: Optional[str] = None
    step: Optional[float] = None
    placeholder: Optional[str] = None
