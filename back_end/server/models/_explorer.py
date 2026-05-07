# back_end/server/models/_explorer.py
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ._helpers import generate_hex_id


class ExplorerSession(Base):
    __tablename__ = "explorer_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    user: Mapped["User"] = relationship("User")
    title: Mapped[str] = mapped_column(String, default="New Session")
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    messages: Mapped[list["ExplorerMessage"]] = relationship(
        back_populates = "session",
        cascade = "all, delete-orphan",
        order_by = "ExplorerMessage.created_at",
    )


class ExplorerMessage(Base):
    __tablename__ = "explorer_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("explorer_sessions.id"), index=True)
    session: Mapped["ExplorerSession"] = relationship(back_populates="messages")
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
