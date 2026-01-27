# back_end/server/schemas.py
from typing import Any, List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel, Field
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
    "BlueprintParamOption",
    "BlueprintParamSchema",
    "ServiceCreate",
    "ServiceResponse",
    "PagedServiceResponse",
    "BlueprintPreferenceUpdate",
    "BlueprintPreferenceResponse",
    "ExplorerMessageCreate",
    "ExplorerMessageResponse",
    "ExplorerSessionCreate",
    "ExplorerSessionResponse",
    "ExplorerSessionWithMessages",
    "PagedExplorerSessionResponse",
]


class UserInfo(BaseModel):
    id: str
    name: str
    token: Optional[str] = None
    avatar_url: Optional[str] = None
    email: Optional[str] = None
    class Config: from_attributes = True


class JobSubmission(BaseModel):
    task_name: str
    description: Optional[str] = None
    namespace: str = "Rise-AGI"
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
    result: Optional[str] = None
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


class BlueprintParamOption(BaseModel):
    label: str
    value: Any
    description: Optional[str] = None


class BlueprintParamSchema(BaseModel):
    key: str
    label: str
    type: str
    default: Any = None
    description: Optional[str] = None
    scope: Optional[str] = None
    allow_empty: bool = True
    is_optional: bool = False
    is_list: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    placeholder: Optional[str] = None
    multi_line: bool = False
    min_lines: Optional[int] = None
    color: Optional[str] = None
    border_color: Optional[str] = None
    options: Optional[List[BlueprintParamOption]] = None
    
    
class BlueprintPreferenceUpdate(BaseModel):
    blueprint_id: str
    blueprint_hash: str
    cached_params: Dict[str, Any]


class BlueprintPreferenceResponse(BaseModel):
    blueprint_id: str
    blueprint_hash: str
    cached_params: Dict[str, Any]
    updated_at: datetime
    
    
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
    gpu_count: int = 1
    gpu_type: str
    job_type: JobType = JobType.A2
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    runner: Optional[str] = None


class ServiceResponse(ServiceCreate):
    owner_id: str
    last_activity_time: datetime
    current_job_id: Optional[str] = None
    assigned_port: Optional[int] = None
    current_job: Optional[JobResponse] = None
    owner: Optional[UserInfo] = None
    updated_at: datetime
    class Config: from_attributes = True
    
    
class PagedServiceResponse(BaseModel):
    total: int
    items: List[ServiceResponse]


class ExplorerMessageCreate(BaseModel):
    content: str
    truncate_before: Optional[int] = None


class ExplorerMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    class Config: from_attributes = True


class ExplorerSessionCreate(BaseModel):
    title: Optional[str] = "New Session"


class ExplorerSessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    is_shared: bool = False
    created_at: datetime
    updated_at: datetime
    class Config: from_attributes = True


class ExplorerSessionWithMessages(ExplorerSessionResponse):
    messages: List[ExplorerMessageResponse] = []


class PagedExplorerSessionResponse(BaseModel):
    total: int
    items: List[ExplorerSessionResponse]