# back_end/server/models/__init__.py
"""SQLAlchemy ORM models, organized by domain.

每个域文件定义自己的表 + enum；公共 helper（`generate_hex_id`）单独一个文件。
本 ``__init__`` 从各域文件 re-export 全部 ORM 类与 enum。

- _user.py:          User
- _job.py:           Job, JobType, JobStatus, ClusterSnapshot
- _blueprint.py:     Blueprint, BlueprintUserPreference
- _service.py:       Service
- _skill.py:         Skill, SkillFile
- _image.py:         CachedImage
- _explorer.py:      ExplorerSession, ExplorerMessage
- _conversation.py:  ConversationType, MessageType, Conversation, ConversationMember, Message
- _helpers.py:       generate_hex_id
"""
from ..database import Base
from ._helpers import generate_hex_id
from ._user import User
from ._job import Job, JobType, JobStatus, ClusterSnapshot, job_list_load_options
from ._blueprint import Blueprint, BlueprintUserPreference, blueprint_list_load_options
from ._service import Service
from ._skill import Skill, SkillFile
from ._image import CachedImage
from ._explorer import ExplorerSession, ExplorerMessage
from ._conversation import (
    ConversationType,
    MessageType,
    Conversation,
    ConversationMember,
    Message,
)


__all__ = [
    "User",
    "Job",
    "JobType",
    "JobStatus",
    "ClusterSnapshot",
    "job_list_load_options",
    "Blueprint",
    "Service",
    "BlueprintUserPreference",
    "blueprint_list_load_options",
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
