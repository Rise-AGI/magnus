import secrets
import enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, Enum as SQLEnum, JSON
from datetime import datetime
from .database import Base


__all__ = [
    "User",
    "Job",
    "JobType",
    "JobStatus",
    "JobMetric",
    "ClusterSnapshot",
]


def generate_hex_id() -> str:
    return secrets.token_hex(8)


class JobType(str, enum.Enum):
    A1 = "A1"  # 高优稳定
    A2 = "A2"  # 次优稳定
    B1 = "B1"  # 高优可抢
    B2 = "B2"  # 次优可抢
    EXTERNAL = "N/A" # 外部任务


class JobStatus(str, enum.Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PAUSED  = "Paused"
    SUCCESS = "Success"
    FAILED  = "Failed"
    TERMINATED = "Terminated"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    feishu_open_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    task_name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    user: Mapped["User"] = relationship(back_populates="jobs")
    namespace: Mapped[str] = mapped_column(String)
    repo_name: Mapped[str] = mapped_column(String)
    branch: Mapped[str] = mapped_column(String)
    commit_sha: Mapped[str] = mapped_column(String)
    gpu_count: Mapped[int] = mapped_column(Integer)
    gpu_type: Mapped[str] = mapped_column(String)
    entry_command: Mapped[str] = mapped_column(Text)
    status: Mapped[JobStatus] = mapped_column(SQLEnum(JobStatus), default=JobStatus.PENDING)
    job_type: Mapped[JobType] = mapped_column(SQLEnum(JobType), default=JobType.A2)
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cpu_count: Mapped[int | None] = mapped_column(Integer, default=None)
    memory_demand: Mapped[str | None] = mapped_column(String, default=None)
    runner: Mapped[str | None] = mapped_column(String, nullable=None)
    metrics: Mapped[list["JobMetric"]] = relationship(
        back_populates = "job", 
        cascade = "all, delete-orphan",
    )


class JobMetric(Base):
    __tablename__ = "job_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status_json: Mapped[str] = mapped_column(Text)
    job: Mapped["Job"] = relationship(back_populates="metrics")
 
    
class ClusterSnapshot(Base):
    __tablename__ = "cluster_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    total_gpus: Mapped[int] = mapped_column(Integer)
    slurm_used_gpus: Mapped[int] = mapped_column(Integer)
    magnus_used_gpus: Mapped[int] = mapped_column(Integer)