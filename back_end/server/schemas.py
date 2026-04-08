# back_end/server/schemas.py
from typing import Any, List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from .models import JobType, JobStatus, ConversationType, MessageType


__all__ = [
    "JobSubmission",
    "JobResponse",
    "PagedJobResponse",
    "FeishuLoginRequest",
    "TokenLoginRequest",
    "UserInfo",
    "UserDetail",
    "AgentCreate",
    "PagedUserResponse",
    "LoginResponse",
    "ClusterStatsResponse",
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
    "SkillFileCreate",
    "SkillFileResponse",
    "SkillCreate",
    "SkillResponse",
    "PagedSkillResponse",
    "CachedImageCreate",
    "CachedImageResponse",
    "PagedCachedImageResponse",
    "ConversationCreate",
    "ConversationResponse",
    "ConversationListItem",
    "PagedConversationResponse",
    "ConversationMemberResponse",
    "MessageCreate",
    "MessageResponse",
    "PagedMessageResponse",
    "AddMemberRequest",
    "ConversationUpdate",
]


class UserInfo(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None
    email: Optional[str] = None
    is_admin: bool = False
    class Config: from_attributes = True


class TransferRequest(BaseModel):
    new_owner_id: str


class UserDetail(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None
    is_admin: bool = False
    user_type: str = "human"
    parent_id: Optional[str] = None
    parent_name: Optional[str] = None
    parent_avatar_url: Optional[str] = None
    headcount: Optional[int] = None
    available_headcount: Optional[int] = None
    blueprint_count: int = 0
    service_count: int = 0
    skill_count: int = 0
    created_at: datetime
    class Config: from_attributes = True


class AgentCreate(BaseModel):
    name: str = Field(min_length=1)


class HeadcountUpdate(BaseModel):
    headcount: int = Field(ge=0)


class PagedUserResponse(BaseModel):
    total: int
    items: List[UserDetail]


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
    ephemeral_storage: Optional[str] = None
    runner: Optional[str] = None
    system_entry_command: Optional[str] = None

    @field_validator("description", "entry_command", "system_entry_command", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: Optional[str])-> Optional[str]:
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
    
    
class PagedJobResponse(BaseModel):
    total: int
    items: List[JobResponse]


class FeishuLoginRequest(BaseModel):
    code: str


class TokenLoginRequest(BaseModel):
    token: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class TokenResponse(BaseModel):
    magnus_token: str
    
    
class ClusterResources(BaseModel):
    node: str
    gpu_model: str
    total: int
    free: int
    used: int
    cpu_total: int
    cpu_free: int
    mem_total_mb: int
    mem_free_mb: int
    class Config: from_attributes = True


class ClusterStatsResponse(BaseModel):
    resources: ClusterResources
    running_jobs: List[JobResponse]
    total_running: int
    pending_jobs: List[JobResponse]
    total_pending: int
    class Config: from_attributes = True
    
    
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
    can_manage: bool = False
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
    is_item_optional: bool = False
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
    current_job: Optional[JobResponse] = None
    owner: Optional[UserInfo] = None
    updated_at: datetime
    can_manage: bool = False
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


class ExplorerSessionOwner(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None
    class Config: from_attributes = True


class ExplorerSessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    is_shared: bool = False
    created_at: datetime
    updated_at: datetime
    user: Optional[ExplorerSessionOwner] = None
    class Config: from_attributes = True


class ExplorerSessionWithMessages(ExplorerSessionResponse):
    messages: List[ExplorerMessageResponse] = []


class PagedExplorerSessionResponse(BaseModel):
    total: int
    items: List[ExplorerSessionResponse]


class SkillFileCreate(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("path must not be empty")
        if "\x00" in v:
            raise ValueError("path must not contain null bytes")
        if v.startswith("/") or v.startswith("\\"):
            raise ValueError("path must be relative")
        if ".." in v.split("/"):
            raise ValueError("path must not contain '..'")
        return v


class SkillFileResponse(BaseModel):
    path: str
    content: str
    is_binary: bool = False
    updated_at: Optional[datetime] = None
    class Config: from_attributes = True


class SkillCreate(BaseModel):
    id: str
    title: str
    description: str
    files: List[SkillFileCreate]


class SkillResponse(BaseModel):
    id: str
    title: str
    description: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    user: Optional[UserInfo] = None
    files: List[SkillFileResponse] = []
    can_manage: bool = False
    class Config: from_attributes = True


class PagedSkillResponse(BaseModel):
    total: int
    items: List[SkillResponse]


class CachedImageCreate(BaseModel):
    uri: str


class CachedImageResponse(BaseModel):
    id: Optional[int] = None
    uri: str
    filename: str
    user_id: Optional[str] = None
    user: Optional[UserInfo] = None
    status: str = "cached"
    size_bytes: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    can_manage: bool = False
    class Config: from_attributes = True


class PagedCachedImageResponse(BaseModel):
    total: int
    items: List[CachedImageResponse]


# ─── Chat ──────────────────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    type: ConversationType
    name: Optional[str] = None
    member_ids: List[str]


class ConversationMemberResponse(BaseModel):
    user_id: str
    role: str
    last_read_at: Optional[datetime] = None
    joined_at: datetime
    user: Optional[UserInfo] = None
    class Config: from_attributes = True


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    content: str
    message_type: MessageType
    created_at: datetime
    sender: Optional[UserInfo] = None
    class Config: from_attributes = True


class ConversationResponse(BaseModel):
    id: str
    type: ConversationType
    name: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    members: List[ConversationMemberResponse] = []
    class Config: from_attributes = True


class ConversationListItem(BaseModel):
    id: str
    type: ConversationType
    name: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    last_message: Optional[MessageResponse] = None
    other_user: Optional[UserInfo] = None  # P2P 会话中的对方
    class Config: from_attributes = True


class PagedConversationResponse(BaseModel):
    total: int
    items: List[ConversationListItem]


class MessageCreate(BaseModel):
    content: str
    message_type: MessageType = MessageType.TEXT


class PagedMessageResponse(BaseModel):
    total: int
    items: List[MessageResponse]


class AddMemberRequest(BaseModel):
    user_id: str


class ConversationUpdate(BaseModel):
    name: Optional[str] = None
