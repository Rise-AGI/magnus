import logging
from typing import Dict, List, Set, Any
from fastapi import WebSocket
from sqlalchemy.orm import Session

from . import models

logger = logging.getLogger(__name__)


class ChatManager:
    def __init__(self) -> None:
        self._connections: Dict[str, List[WebSocket]] = {}
        self._user_conversations: Dict[str, Set[str]] = {}

    async def connect(
        self,
        user_id: str,
        websocket: WebSocket,
        db: Session,
    ) -> None:
        await websocket.accept()
        if user_id not in self._connections:
            self._connections[user_id] = []
        self._connections[user_id].append(websocket)
        memberships = (
            db.query(models.ConversationMember.conversation_id)
            .filter(models.ConversationMember.user_id == user_id)
            .all()
        )
        self._user_conversations[user_id] = {m.conversation_id for m in memberships}
        logger.info(
            f"WebSocket connected: user={user_id}, "
            f"connections={len(self._connections[user_id])}, "
            f"conversations={len(self._user_conversations[user_id])}"
        )

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(user_id, [])
        try:
            conns.remove(websocket)
        except ValueError:
            pass
        if not conns:
            self._connections.pop(user_id, None)
            self._user_conversations.pop(user_id, None)
        logger.info(
            f"WebSocket disconnected: user={user_id}, remaining={len(conns)}"
        )

    async def broadcast_to_conversation(
        self,
        conversation_id: str,
        event: Dict[str, Any],
        exclude_user_id: str | None = None,
    ) -> None:
        for user_id, conv_ids in list(self._user_conversations.items()):
            if conversation_id not in conv_ids:
                continue
            if user_id == exclude_user_id:
                continue
            for ws in list(self._connections.get(user_id, [])):
                try:
                    await ws.send_json(event)
                except Exception:
                    logger.warning(f"Failed to send to user={user_id}, removing connection")
                    self.disconnect(user_id, ws)

    def add_user_to_conversation(self, user_id: str, conversation_id: str) -> None:
        if user_id in self._user_conversations:
            self._user_conversations[user_id].add(conversation_id)

    def remove_user_from_conversation(self, user_id: str, conversation_id: str) -> None:
        if user_id in self._user_conversations:
            self._user_conversations[user_id].discard(conversation_id)

    def is_connected(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))


chat_manager = ChatManager()
