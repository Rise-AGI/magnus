import asyncio
import logging
import os
from typing import Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from .. import database
from .. import models
from ..schemas import (
    UserInfo,
    ConversationCreate,
    ConversationResponse,
    ConversationListItem,
    PagedConversationResponse,
    ConversationMemberResponse,
    MessageCreate,
    MessageResponse,
    PagedMessageResponse,
    AddMemberRequest,
    ConversationUpdate,
)
from .auth import get_current_user
from .._chat_manager import chat_manager
from .._file_custody_manager import file_custody_manager

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_member_response(m: models.ConversationMember) -> ConversationMemberResponse:
    return ConversationMemberResponse(
        user_id=m.user_id,
        role=m.role,
        last_read_at=m.last_read_at,
        joined_at=m.joined_at,
        user=UserInfo(
            id=m.user.id,
            name=m.user.name,
            avatar_url=m.user.avatar_url,
            email=m.user.email,
        ) if m.user else None,
    )


def _build_message_response(msg: models.Message) -> MessageResponse:
    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        content=msg.content,
        message_type=msg.message_type,
        created_at=msg.created_at,
        sender=UserInfo(
            id=msg.sender.id,
            name=msg.sender.name,
            avatar_url=msg.sender.avatar_url,
            email=msg.sender.email,
        ) if msg.sender else None,
    )


def _ensure_member(db: Session, conversation_id: str, user_id: str) -> models.ConversationMember:
    member = (
        db.query(models.ConversationMember)
        .filter(
            models.ConversationMember.conversation_id == conversation_id,
            models.ConversationMember.user_id == user_id,
        )
        .first()
    )
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")
    return member


# ─── 会话 CRUD ───────────────────────────────────────────────────────────

@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    body: ConversationCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> ConversationResponse:
    if body.type == models.ConversationType.P2P:
        if len(body.member_ids) == 0:
            raise HTTPException(status_code=400, detail="P2P conversation requires exactly 1 other member")
        if len(body.member_ids) != 1:
            raise HTTPException(status_code=400, detail="P2P conversation requires exactly 1 other member")
        other_id = body.member_ids[0]
        if other_id == current_user.id:
            raise HTTPException(status_code=400, detail="Cannot create P2P conversation with yourself")
        # Return existing P2P conversation if one already exists between these two users
        existing = (
            db.query(models.Conversation)
            .join(models.ConversationMember)
            .filter(
                models.Conversation.type == models.ConversationType.P2P,
                models.ConversationMember.user_id.in_([current_user.id, other_id]),
            )
            .group_by(models.Conversation.id)
            .having(func.count(models.ConversationMember.id) == 2)
            .first()
        )
        if existing:
            db.refresh(existing)
            members = (
                db.query(models.ConversationMember)
                .options(joinedload(models.ConversationMember.user))
                .filter(models.ConversationMember.conversation_id == existing.id)
                .all()
            )
            return ConversationResponse(
                id=existing.id,
                type=existing.type,
                name=existing.name,
                created_by=existing.created_by,
                created_at=existing.created_at,
                updated_at=existing.updated_at,
                members=[_build_member_response(m) for m in members],
            )
    elif body.type == models.ConversationType.GROUP:
        if len(body.member_ids) == 0:
            raise HTTPException(status_code=400, detail="Group conversation requires at least 1 other member")

    # 验证 member_ids 存在
    member_users = db.query(models.User).filter(models.User.id.in_(body.member_ids)).all()
    if len(member_users) != len(body.member_ids):
        raise HTTPException(status_code=400, detail="Some member IDs are invalid")

    conv = models.Conversation(
        type=body.type,
        name=body.name,
        created_by=current_user.id,
    )
    db.add(conv)
    db.flush()

    # 创建者作为 owner
    owner_member = models.ConversationMember(
        conversation_id=conv.id,
        user_id=current_user.id,
        role="owner",
    )
    db.add(owner_member)

    for uid in body.member_ids:
        if uid == current_user.id:
            continue
        m = models.ConversationMember(
            conversation_id=conv.id,
            user_id=uid,
            role="member",
        )
        db.add(m)

    db.commit()
    db.refresh(conv)

    members = (
        db.query(models.ConversationMember)
        .options(joinedload(models.ConversationMember.user))
        .filter(models.ConversationMember.conversation_id == conv.id)
        .all()
    )

    logger.info(f"Conversation created: id={conv.id}, type={conv.type}, by={current_user.id}")

    return ConversationResponse(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        created_by=conv.created_by,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        members=[_build_member_response(m) for m in members],
    )


@router.get(
    "/conversations",
    response_model=PagedConversationResponse,
)
def list_conversations(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> PagedConversationResponse:
    my_conv_ids_q = (
        db.query(models.ConversationMember.conversation_id)
        .filter(models.ConversationMember.user_id == current_user.id)
        .subquery()
    )

    total = (
        db.query(func.count())
        .select_from(models.Conversation)
        .filter(models.Conversation.id.in_(db.query(my_conv_ids_q.c.conversation_id)))
        .scalar()
    ) or 0

    conversations = (
        db.query(models.Conversation)
        .filter(models.Conversation.id.in_(db.query(my_conv_ids_q.c.conversation_id)))
        .order_by(models.Conversation.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    if not conversations:
        return PagedConversationResponse(total=total, items=[])

    conv_ids = [c.id for c in conversations]

    # Batch: member counts — single GROUP BY query instead of N queries
    member_count_rows = (
        db.query(
            models.ConversationMember.conversation_id,
            func.count(models.ConversationMember.id).label("cnt"),
        )
        .filter(models.ConversationMember.conversation_id.in_(conv_ids))
        .group_by(models.ConversationMember.conversation_id)
        .all()
    )
    member_count_map: Dict[str, int] = {row.conversation_id: row.cnt for row in member_count_rows}

    # Batch: last message per conversation — single query using MAX subquery
    last_msg_subq = (
        db.query(
            models.Message.conversation_id,
            func.max(models.Message.created_at).label("max_ts"),
        )
        .filter(models.Message.conversation_id.in_(conv_ids))
        .group_by(models.Message.conversation_id)
        .subquery()
    )
    last_msgs = (
        db.query(models.Message)
        .options(joinedload(models.Message.sender))
        .join(
            last_msg_subq,
            (models.Message.conversation_id == last_msg_subq.c.conversation_id)
            & (models.Message.created_at == last_msg_subq.c.max_ts),
        )
        .all()
    )
    # 同一毫秒内有多条消息时取 id 最大的（字典序稳定，hex id 单调递增）
    last_msg_map: Dict[str, models.Message] = {}
    for m in last_msgs:
        existing = last_msg_map.get(m.conversation_id)
        if existing is None or m.id > existing.id:
            last_msg_map[m.conversation_id] = m

    # Batch: P2P 对方用户 — 仅查询 P2P 会话
    p2p_conv_ids = [c.id for c in conversations if c.type == models.ConversationType.P2P]
    other_user_map: Dict[str, UserInfo] = {}
    if p2p_conv_ids:
        p2p_members = (
            db.query(models.ConversationMember)
            .options(joinedload(models.ConversationMember.user))
            .filter(
                models.ConversationMember.conversation_id.in_(p2p_conv_ids),
                models.ConversationMember.user_id != current_user.id,
            )
            .all()
        )
        for m in p2p_members:
            if m.user:
                other_user_map[m.conversation_id] = UserInfo(
                    id=m.user.id,
                    name=m.user.name,
                    avatar_url=m.user.avatar_url,
                    email=m.user.email,
                )

    items = []
    for conv in conversations:
        last_msg = last_msg_map.get(conv.id)
        items.append(ConversationListItem(
            id=conv.id,
            type=conv.type,
            name=conv.name,
            created_by=conv.created_by,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            member_count=member_count_map.get(conv.id, 0),
            last_message=_build_message_response(last_msg) if last_msg else None,
            other_user=other_user_map.get(conv.id),
        ))

    return PagedConversationResponse(total=total, items=items)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> ConversationResponse:
    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    _ensure_member(db, conversation_id, current_user.id)

    members = (
        db.query(models.ConversationMember)
        .options(joinedload(models.ConversationMember.user))
        .filter(models.ConversationMember.conversation_id == conversation_id)
        .all()
    )

    return ConversationResponse(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        created_by=conv.created_by,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        members=[_build_member_response(m) for m in members],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> ConversationResponse:
    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    member = _ensure_member(db, conversation_id, current_user.id)
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can update the conversation")
    if conv.type != models.ConversationType.GROUP:
        raise HTTPException(status_code=400, detail="Only group conversations can be renamed")

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Conversation name cannot be empty")
        conv.name = name

    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)

    members = (
        db.query(models.ConversationMember)
        .options(joinedload(models.ConversationMember.user))
        .filter(models.ConversationMember.conversation_id == conv.id)
        .all()
    )
    return ConversationResponse(
        id=conv.id,
        type=conv.type,
        name=conv.name,
        created_by=conv.created_by,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        members=[_build_member_response(m) for m in members],
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    member = _ensure_member(db, conversation_id, current_user.id)
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can delete a conversation")

    db.delete(conv)
    db.commit()
    logger.info(f"Conversation deleted: id={conversation_id}, by={current_user.id}")


# ─── 成员管理 ──────────────────────────────────────────────────────────

@router.post(
    "/conversations/{conversation_id}/members",
    response_model=ConversationMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    conversation_id: str,
    body: AddMemberRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> ConversationMemberResponse:
    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.type == models.ConversationType.P2P:
        raise HTTPException(status_code=400, detail="Cannot add members to P2P conversation")

    _ensure_member(db, conversation_id, current_user.id)

    target_user = db.query(models.User).filter(models.User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = (
        db.query(models.ConversationMember)
        .filter(
            models.ConversationMember.conversation_id == conversation_id,
            models.ConversationMember.user_id == body.user_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member")

    m = models.ConversationMember(
        conversation_id=conversation_id,
        user_id=body.user_id,
        role="member",
    )
    db.add(m)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(m)

    chat_manager.add_user_to_conversation(body.user_id, conversation_id)

    await chat_manager.broadcast_to_conversation(
        conversation_id,
        {
            "type": "member_added",
            "conversation_id": conversation_id,
            "user_id": body.user_id,
            "user_name": target_user.name,
        },
    )

    return ConversationMemberResponse(
        user_id=m.user_id,
        role=m.role,
        last_read_at=m.last_read_at,
        joined_at=m.joined_at,
        user=UserInfo(
            id=target_user.id,
            name=target_user.name,
            avatar_url=target_user.avatar_url,
            email=target_user.email,
        ),
    )


@router.delete(
    "/conversations/{conversation_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    conversation_id: str,
    user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    actor_member = _ensure_member(db, conversation_id, current_user.id)

    # 只有 owner 可以移除别人；任何人可以移除自己
    if user_id != current_user.id and actor_member.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can remove other members")

    target_member = (
        db.query(models.ConversationMember)
        .filter(
            models.ConversationMember.conversation_id == conversation_id,
            models.ConversationMember.user_id == user_id,
        )
        .first()
    )
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found")

    db.delete(target_member)
    db.commit()

    chat_manager.remove_user_from_conversation(user_id, conversation_id)

    await chat_manager.broadcast_to_conversation(
        conversation_id,
        {
            "type": "member_removed",
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )


# ─── 消息 ──────────────────────────────────────────────────────────────

@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=PagedMessageResponse,
)
def list_messages(
    conversation_id: str,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> PagedMessageResponse:
    _ensure_member(db, conversation_id, current_user.id)

    total = (
        db.query(func.count())
        .select_from(models.Message)
        .filter(models.Message.conversation_id == conversation_id)
        .scalar()
    ) or 0

    messages = (
        db.query(models.Message)
        .options(joinedload(models.Message.sender))
        .filter(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PagedMessageResponse(
        total=total,
        items=[_build_message_response(msg) for msg in messages],
    )


@router.get(
    "/conversations/{conversation_id}/messages/backfill",
    response_model=PagedMessageResponse,
)
def backfill_messages(
    conversation_id: str,
    since: str,  # ISO timestamp
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> PagedMessageResponse:
    """Fetch messages newer than 'since' timestamp. Used for backfill on reconnect."""
    _ensure_member(db, conversation_id, current_user.id)

    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid 'since' timestamp format")

    # 使用 >= 避免与 since 完全相同时间戳的消息被遗漏；前端通过 id dedup 去重
    # 限制 200 条防止长时间离线后 OOM
    messages = (
        db.query(models.Message)
        .options(joinedload(models.Message.sender))
        .filter(
            models.Message.conversation_id == conversation_id,
            models.Message.created_at >= since_dt,
        )
        .order_by(models.Message.created_at.asc())
        .limit(200)
        .all()
    )

    return PagedMessageResponse(total=len(messages), items=[_build_message_response(m) for m in messages])


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: str,
    body: MessageCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> MessageResponse:
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")
    if len(body.content) > 10000:
        raise HTTPException(status_code=400, detail="Message content too long (max 10000 chars)")
    # 图片消息只允许本服务器上传的内部 URL
    if body.message_type == models.MessageType.IMAGE:
        if not body.content.startswith("/api/files/download/"):
            raise HTTPException(status_code=400, detail="Image content must be an internal upload URL")

    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    _ensure_member(db, conversation_id, current_user.id)

    msg = models.Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        content=body.content,
        message_type=body.message_type,
    )
    db.add(msg)
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg)

    resp = MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        sender_id=msg.sender_id,
        content=msg.content,
        message_type=msg.message_type,
        created_at=msg.created_at,
        sender=UserInfo(
            id=current_user.id,
            name=current_user.name,
            avatar_url=current_user.avatar_url,
            email=current_user.email,
        ),
    )

    # 广播给所有成员（含发送者的其他设备），前端通过 id dedup 去重
    await chat_manager.broadcast_to_conversation(
        conversation_id,
        {
            "type": "new_message",
            "conversation_id": conversation_id,
            "message": resp.model_dump(mode="json"),
        },
    )

    return resp


# ─── 已读标记 ──────────────────────────────────────────────────────────

@router.post(
    "/conversations/{conversation_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
)
def mark_read(
    conversation_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    member = _ensure_member(db, conversation_id, current_user.id)
    member.last_read_at = datetime.now(timezone.utc)
    db.commit()


# ─── 媒体上传 ──────────────────────────────────────────────────────────

@router.post("/chat/media/upload")
async def upload_chat_media(
    file: UploadFile,
    current_user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    allowed = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    # 防路径穿越：仅取文件名部分
    filename = os.path.basename(file.filename or "image") or "image"
    token = await asyncio.to_thread(
        file_custody_manager.store_file,
        filename,
        file.file,
        None,
        False,
        None,
        True,
    )
    return {"url": f"/api/files/download/{token}", "token": token}
