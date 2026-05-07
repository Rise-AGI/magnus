# back_end/server/schemas/_image.py
"""CachedImage schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from ._user import UserInfo


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
