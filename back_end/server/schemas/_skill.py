# back_end/server/schemas/_skill.py
"""Skill create / response schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator

from ._user import UserInfo


class SkillFileCreate(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if not v:
            raise ValueError("path must not be empty")
        if "\x00" in v:
            raise ValueError("path must not contain null bytes")
        if v.startswith("/"):
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
