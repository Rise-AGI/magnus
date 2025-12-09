# back_end/server/models.py
import secrets
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey
from datetime import datetime
from .database import Base


def generate_hex_id(
)-> str:
    
    return secrets.token_hex(8)


class User(Base):
    
    __tablename__ = "users"

    # 我们自己的 User ID (16位 Hex)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    
    # 飞书的唯一标识 (Open ID)，这是我们识别用户的核心依据
    feishu_open_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    
    # 基本信息
    name: Mapped[str] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 关联关系：一个用户可以有多个 Job
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")


class Job(Base):
    
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    
    task_name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # 👇 关键变更：
    # 1. 变成了外键 ForeignKey("users.id")
    # 2. 暂时允许 nullable=True，是为了防止你还没有做登录逻辑时，提交任务报错
    # 3. 移除了 default="Researcher"
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    
    # 反向关系：通过 job.user 可以直接拿到 User 对象（获取头像、名字）
    user: Mapped["User"] = relationship(back_populates="jobs")
    
    namespace: Mapped[str] = mapped_column(String)
    repo_name: Mapped[str] = mapped_column(String)
    branch: Mapped[str] = mapped_column(String)
    commit_sha: Mapped[str] = mapped_column(String)
    
    gpu_count: Mapped[int] = mapped_column(Integer)
    gpu_type: Mapped[str] = mapped_column(String)
    
    entry_command: Mapped[str] = mapped_column(Text)
    
    status: Mapped[str] = mapped_column(String, default="Pending")
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)