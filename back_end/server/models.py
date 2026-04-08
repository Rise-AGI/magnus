# back_end/server/models.py
import secrets
import enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, Enum as SQLEnum, Boolean, UniqueConstraint, text
from datetime import datetime, timezone
from .database import Base


__all__ = [
    "User",
    "Job",
    "JobType",
    "JobStatus",
    "ClusterSnapshot",
    "Blueprint",
    "Service",
    "BlueprintUserPreference",
    "ExplorerSession",
    "ExplorerMessage",
    "Skill",
    "SkillFile",
    "CachedImage",
    "ConversationType",
    "MessageType",
    "Conversation",
    "ConversationMember",
    "Message",
]


def generate_hex_id()-> str:
    return secrets.token_hex(8)


class JobType(str, enum.Enum):
    A1 = "A1"  # 高优稳定
    A2 = "A2"  # 次优稳定
    B1 = "B1"  # 高优可抢
    B2 = "B2"  # 次优可抢
    EXTERNAL = "N/A" # 外部任务


class JobStatus(str, enum.Enum):
    PENDING = "Pending"
    PREPARING = "Preparing"
    QUEUED = "Queued"
    RUNNING = "Running"
    PAUSED  = "Paused"
    SUCCESS = "Success"
    FAILED  = "Failed"
    TERMINATED = "Terminated"


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


class BlueprintUserPreference(Base):
    __tablename__ = "blueprint_user_preferences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    blueprint_id: Mapped[str] = mapped_column(String, ForeignKey("blueprints.id"), index=True)
    blueprint_hash: Mapped[str] = mapped_column(String)
    cached_params: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    user: Mapped["User"] = relationship("User")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    files: Mapped[list["SkillFile"]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
    )


class SkillFile(Base):
    __tablename__ = "skill_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String, ForeignKey("skills.id"), index=True)
    skill: Mapped["Skill"] = relationship(back_populates="files")
    path: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


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


class ConversationType(str, enum.Enum):
    P2P = "p2p"
    GROUP = "group"


class MessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    SYSTEM = "system"


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    type: Mapped[ConversationType] = mapped_column(SQLEnum(ConversationType))
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    members: Mapped[list["ConversationMember"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    creator: Mapped["User"] = relationship("User")


class ConversationMember(Base):
    __tablename__ = "conversation_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String, default="member")
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    conversation: Mapped["Conversation"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship("User")
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_member"),
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), index=True)
    sender_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[MessageType] = mapped_column(SQLEnum(MessageType), default=MessageType.TEXT)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship("User")