# back_end/server/models/_job.py
import enum
from datetime import datetime, timezone
from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ._helpers import generate_hex_id


class JobType(str, enum.Enum):
    A1 = "A1"  # 高优稳定
    A2 = "A2"  # 次优稳定
    B1 = "B1"  # 高优可抢
    B2 = "B2"  # 次优可抢
    EXTERNAL = "N/A"  # 外部任务


class JobStatus(str, enum.Enum):
    PENDING = "Pending"
    PREPARING = "Preparing"
    QUEUED = "Queued"
    RUNNING = "Running"
    PAUSED  = "Paused"
    SUCCESS = "Success"
    FAILED  = "Failed"
    TERMINATED = "Terminated"


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    task_name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    user: Mapped["User"] = relationship(back_populates="jobs")
    namespace: Mapped[str] = mapped_column(String)
    repo_name: Mapped[str] = mapped_column(String)
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_command: Mapped[str] = mapped_column(Text)
    container_image: Mapped[str] = mapped_column(String)
    system_entry_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    gpu_count: Mapped[int] = mapped_column(Integer)
    gpu_type: Mapped[str] = mapped_column(String)
    cpu_count: Mapped[int | None] = mapped_column(Integer, default=None)
    memory_demand: Mapped[str | None] = mapped_column(String, default=None)
    ephemeral_storage: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(SQLEnum(JobStatus), default=JobStatus.PREPARING)
    job_type: Mapped[JobType] = mapped_column(SQLEnum(JobType), default=JobType.A2)
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    runner: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClusterSnapshot(Base):
    __tablename__ = "cluster_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    total_gpus: Mapped[int] = mapped_column(Integer)
    slurm_used_gpus: Mapped[int] = mapped_column(Integer)
    magnus_used_gpus: Mapped[int] = mapped_column(Integer)
