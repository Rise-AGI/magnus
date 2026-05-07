# back_end/server/_file_custody_manager/_types.py
"""File custody 用到的常量、异常、entry dataclass。"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


FILE_SECRET_PREFIX = "magnus-secret:"


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:g} {unit}"
        value /= 1024
    return f"{value:g} PB"


class FileTooLargeError(Exception):
    def __init__(self, filename: str, limit: int):
        self.filename = filename
        self.limit = limit
        super().__init__(f'File "{filename}" exceeds the {_format_size(limit)} limit.')


class CustodyLimitError(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"File custody slot limit reached ({limit}). Try again later.")


class CustodyStorageFullError(Exception):
    def __init__(self):
        super().__init__("File custody storage is full. Try again later.")


@dataclass
class CustodyEntry:
    entry_id: str
    file_dir: Path
    original_filename: str
    is_directory: bool
    expires_at: float
    permanent: bool = False
    max_downloads: Optional[int] = None
    download_count: int = 0
    file_size: int = 0
