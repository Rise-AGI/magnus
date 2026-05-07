# back_end/server/schemas/_explorer.py
"""Explorer session / message schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


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
