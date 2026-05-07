# back_end/server/models/_service.py
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ._job import JobType


class Service(Base):
    __tablename__ = "services"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    owner: Mapped["User"] = relationship(back_populates="services")
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_activity_time: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    current_job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.id"), nullable=True)
    current_job: Mapped["Job"] = relationship()
    assigned_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_timeout: Mapped[int] = mapped_column(Integer, default=60)
    idle_timeout: Mapped[int] = mapped_column(Integer, default=30)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=64)
    namespace: Mapped[str] = mapped_column(String)
    repo_name: Mapped[str] = mapped_column(String)
    branch: Mapped[str] = mapped_column(String)
    commit_sha: Mapped[str] = mapped_column(String)
    entry_command: Mapped[str] = mapped_column(Text)
    job_task_name: Mapped[str] = mapped_column(String, nullable=False)
    job_description: Mapped[str] = mapped_column(String, nullable=False)
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    gpu_type: Mapped[str] = mapped_column(String)
    job_type: Mapped[JobType] = mapped_column(SQLEnum(JobType), default=JobType.B2)
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_demand: Mapped[str | None] = mapped_column(String, nullable=True)
    ephemeral_storage: Mapped[str | None] = mapped_column(String, nullable=True)
    runner: Mapped[str | None] = mapped_column(String, nullable=True)
    container_image: Mapped[str | None] = mapped_column(String, nullable=True)
    system_entry_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
