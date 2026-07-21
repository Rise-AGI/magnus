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


class SkillListItem(BaseModel):
    """列表视图的轻量投影：省掉 files（列表从不渲染文件内容，累计可达数百 KB；详情
    GET /skills/{id} 才带全）。与 job / blueprint 列表同型。"""
    id: str
    title: str
    description: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    # files 被投影掉后，列表仍需要一个轻量标量来概括文件数（CLI / 未来列表卡片用），
    # 由 endpoint 填充：列表填 DB 文件数、详情填 DB + 文件系统 resource 数，各自反映本响应的 file set。
    file_count: int = 0
    user: Optional[UserInfo] = None
    can_manage: bool = False
    class Config: from_attributes = True


class SkillResponse(SkillListItem):
    """完整视图（详情 / 提交返回），在轻量投影之上补齐 files，只由单条 skill 的
    endpoint 返回，绝不进列表。"""
    files: List[SkillFileResponse] = []


class PagedSkillResponse(BaseModel):
    total: int
    items: List[SkillListItem]
