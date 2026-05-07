# back_end/server/_file_custody_manager/__init__.py
"""File custody: short-lived / permanent file storage with human-friendly tokens.

- _types.py:      FILE_SECRET_PREFIX 常量 + 3 个 exception + CustodyEntry dataclass
- _word_list.py:  Token 生成用的素数表 + 单词表（数据，与逻辑分离）
- _manager.py:    FileCustodyManager（slot 限额、LRU、permanent manifest 持久化）

公共面：单例 `file_custody_manager`、`FILE_SECRET_PREFIX`、3 个 exception 类。
"""
import logging

logger = logging.getLogger(__name__)

from ._manager import FileCustodyManager
from ._types import (
    FILE_SECRET_PREFIX,
    CustodyEntry,
    CustodyLimitError,
    CustodyStorageFullError,
    FileTooLargeError,
)

__all__ = [
    "file_custody_manager",
    "FILE_SECRET_PREFIX",
    "CustodyEntry",
    "CustodyLimitError",
    "CustodyStorageFullError",
    "FileTooLargeError",
    "FileCustodyManager",
]

file_custody_manager = FileCustodyManager()
