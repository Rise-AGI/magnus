# back_end/server/models/_blueprint.py
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, defer, joinedload, mapped_column, relationship

from ..database import Base


class Blueprint(Base):
    __tablename__ = "blueprints"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    code: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    user: Mapped["User"] = relationship(back_populates="blueprints")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BlueprintUserPreference(Base):
    __tablename__ = "blueprint_user_preferences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    blueprint_id: Mapped[str] = mapped_column(String, ForeignKey("blueprints.id"), index=True)
    blueprint_hash: Mapped[str] = mapped_column(String)
    cached_params: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


def blueprint_list_load_options():
    """Loader options for the blueprint LIST, mirroring job_list_load_options: defer
    the heavy ``code`` column with raiseload=True so it is neither read from disk nor
    serialized (a single blueprint can inline tens of MB) and accidental access on a
    list-loaded row fails loudly, and eager-load the author to avoid an N+1 lookup.
    The single-blueprint detail endpoint omits these and returns the full row.
    """
    return (
        defer(Blueprint.code, raiseload=True),
        joinedload(Blueprint.user),
    )
