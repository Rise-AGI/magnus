"""Durable acceptance identity and Blueprint provenance, independent of files."""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ._helpers import UtcDateTime


class JobAcceptance(Base):
    __tablename__ = "job_acceptances"
    __table_args__ = (
        Index(
            "uq_job_acceptance_user_key",
            "user_id",
            "request_key",
            unique=True,
        ),
    )

    job_id: Mapped[str] = mapped_column(
        String,
        ForeignKey(
            "jobs.id",
        ),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey(
            "users.id",
        ),
    )
    request_key: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    request_hash: Mapped[str] = mapped_column(
        String,
    )
    # Source identifiers remain after a Blueprint is edited or deleted.
    blueprint_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    blueprint_revision: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        default=lambda: datetime.now(
            timezone.utc,
        ),
    )
