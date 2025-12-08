# back_end/server/models.py
import secrets
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, DateTime, Text
from datetime import datetime
from .database import Base


def generate_hex_id(
)-> str:
    
    return secrets.token_hex(8)


class Job(Base):
    
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    
    task_name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str] = mapped_column(String, default="Researcher")
    
    namespace: Mapped[str] = mapped_column(String)
    repo_name: Mapped[str] = mapped_column(String)
    branch: Mapped[str] = mapped_column(String)
    commit_sha: Mapped[str] = mapped_column(String)
    
    gpu_count: Mapped[int] = mapped_column(Integer)
    gpu_type: Mapped[str] = mapped_column(String)
    
    entry_command: Mapped[str] = mapped_column(Text)
    
    status: Mapped[str] = mapped_column(String, default="Pending")
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)