import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import models
from ..schemas import UserInfo, MessageResponse
from ..database import SessionLocal
from .._chat_manager import chat_manager
from .._magnus_config import magnus_config

logger = logging.getLogger(__name__)
ws_router = APIRouter()

# 速率限制：每用户每分钟最多 60 条消息
_RATE_LIMIT = 60
_rate_buckets: Dict[str, List[datetime]] = defaultdict(list)


def _check_rate_limit(user_id: str) -> bool:
    now = datetime.now(timezone.utc)
    window = now - timedelta(minutes=1)
    bucket = _rate_buckets[user_id]
    # 清理过期记录
    _rate_buckets[user_id] = [t for t in bucket if t > window]
    if len(_rate_buckets[user_id]) >= _RATE_LIMIT:
        return False
    _rate_buckets[user_id].append(now)
    return True


def _authenticate_bot(app_secret: str) -> models.User | None:
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.app_secret == app_secret).first()
        if not user:
            return None
        db.expunge(user)
        return user


def _authenticate_token(token: str) -> models.User | None:
    try:
        payload = jwt.decode(
            token,
            magnus_config["server"]["auth"]["jwt_signer"]["secret_key"],
            algorithms=[magnus_config["server"]["auth"]["jwt_signer"]["algorithm"]],
        )
        user_id = payload.get("sub")
        if not user_id:
            return None
    except jwt.PyJWTError:
        return None
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return None
        db.expunge(user)
        return user


@ws_router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    app_secret = websocket.query_params.get("app_secret", "")
    token = websocket.query_params.get("token", "")

    user: models.User | None = None

    if app_secret:
        user = _authenticate_bot(app_secret)
    elif token:
        user = _authenticate_token(token)

    if not user:
        logger.warning(
            f"WebSocket auth failed: app_secret={'*' if app_secret else ''}, token={'*' if token else ''}, "
            f"ip={websocket.client.host if websocket.client else 'unknown'}"
        )
        await websocket.accept()
        await websocket.close(code=4001, reason="Invalid credentials")
        return

    user_id = user.id

    with SessionLocal() as db:
        await chat_manager.connect(user_id, websocket, db)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            msg_type = data.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "send_message":
                if not _check_rate_limit(user_id):
                    await websocket.send_json({"type": "error", "detail": "Rate limit exceeded"})
                    continue

                conversation_id = data.get("conversation_id", "")
                content = data.get("content", "")
                message_type_str = data.get("message_type", "text")

                if not conversation_id or not content.strip():
                    await websocket.send_json({"type": "error", "detail": "Missing conversation_id or content"})
                    continue

                if len(content) > 10000:
                    await websocket.send_json({"type": "error", "detail": "Message content too long (max 10000 chars)"})
                    continue

                try:
                    message_type = models.MessageType(message_type_str)
                except ValueError:
                    await websocket.send_json({"type": "error", "detail": f"Invalid message_type: {message_type_str}"})
                    continue

                # 图片消息内容校验
                if message_type == models.MessageType.IMAGE:
                    if not content.startswith("/api/files/download/"):
                        await websocket.send_json({"type": "error", "detail": "Image content must be an internal upload URL"})
                        continue

                with SessionLocal() as db:
                    member = (
                        db.query(models.ConversationMember)
                        .filter(
                            models.ConversationMember.conversation_id == conversation_id,
                            models.ConversationMember.user_id == user_id,
                        )
                        .first()
                    )
                    if not member:
                        await websocket.send_json({"type": "error", "detail": "Not a member of this conversation"})
                        continue

                    msg = models.Message(
                        conversation_id=conversation_id,
                        sender_id=user_id,
                        content=content,
                        message_type=message_type,
                    )
                    db.add(msg)

                    conv = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
                    if conv:
                        conv.updated_at = datetime.now(timezone.utc)

                    db.commit()
                    db.refresh(msg)

                    sender = db.query(models.User).filter(models.User.id == user_id).first()
                    resp = MessageResponse(
                        id=msg.id,
                        conversation_id=msg.conversation_id,
                        sender_id=msg.sender_id,
                        content=msg.content,
                        message_type=msg.message_type,
                        created_at=msg.created_at,
                        sender=UserInfo(
                            id=sender.id,
                            name=sender.name,
                            avatar_url=sender.avatar_url,
                            email=sender.email,
                        ) if sender else None,
                    )

                event = {
                    "type": "new_message",
                    "conversation_id": conversation_id,
                    "message": resp.model_dump(mode="json"),
                }
                await chat_manager.broadcast_to_conversation(conversation_id, event)

            elif msg_type == "mark_read":
                conversation_id = data.get("conversation_id", "")
                if not conversation_id:
                    await websocket.send_json({"type": "error", "detail": "Missing conversation_id"})
                    continue

                with SessionLocal() as db:
                    member = (
                        db.query(models.ConversationMember)
                        .filter(
                            models.ConversationMember.conversation_id == conversation_id,
                            models.ConversationMember.user_id == user_id,
                        )
                        .first()
                    )
                    if member:
                        member.last_read_at = datetime.now(timezone.utc)
                        db.commit()

                await websocket.send_json({"type": "read_ack", "conversation_id": conversation_id})

            else:
                await websocket.send_json({"type": "error", "detail": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for user={user_id}: {e}")
    finally:
        chat_manager.disconnect(user_id, websocket)
