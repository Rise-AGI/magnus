# back_end/server/models/_user.py
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ._helpers import generate_hex_id


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    feishu_open_id: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    token: Mapped[str | None] = mapped_column(String, nullable=True)
    user_type: Mapped[str] = mapped_column(String, default="human")
    parent_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")
    services: Mapped[list["Service"]] = relationship(back_populates="owner")
    blueprints: Mapped[list["Blueprint"]] = relationship(back_populates="user")
    parent: Mapped["User | None"] = relationship("User", remote_side=[id], foreign_keys=[parent_id])
    children: Mapped[list["User"]] = relationship("User", foreign_keys=[parent_id])
