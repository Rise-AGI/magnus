# back_end/server/schemas/_chat.py
"""Conversation / message schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from ..models import ConversationType, MessageType
from ._user import UserInfo


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
