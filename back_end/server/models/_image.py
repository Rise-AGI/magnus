# back_end/server/models/_image.py
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class CachedImage(Base):
    __tablename__ = "cached_images"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uri: Mapped[str] = mapped_column(String, unique=True)
    filename: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    user: Mapped["User"] = relationship("User")
    status: Mapped[str] = mapped_column(String, default="cached")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
