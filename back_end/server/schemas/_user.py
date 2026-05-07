# back_end/server/schemas/_user.py
"""User-related Pydantic schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


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
