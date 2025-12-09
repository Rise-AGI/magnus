# back_end/server/schemas.py
from library import *


__all__ = [
    "JobSubmission",
    "JobResponse",
    "FeishuLoginRequest",
    "UserInfo",
    "LoginResponse",
]


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
    
    
class JobResponse(JobSubmission):
    id: str
    user_id: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True # 兼容 ORM 对象读取


class FeishuLoginRequest(BaseModel):
    code: str


class UserInfo(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None
    email: Optional[str] = None

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo
