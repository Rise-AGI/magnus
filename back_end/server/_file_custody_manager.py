# back_end/server/_file_custody_manager.py
import os
import uuid
import time
import shutil
import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Tuple, BinaryIO
from dataclasses import dataclass

from ._magnus_config import magnus_config
from ._resource_manager import _parse_size_string

logger = logging.getLogger(__name__)

FILE_SECRET_PREFIX = "magnus-secret:"


@dataclass
class CustodyEntry:
    entry_id: str
    file_dir: Path
    original_filename: str
    is_directory: bool
    expires_at: float


class FileCustodyManager:

    def __init__(self):
        config = magnus_config["server"]["file_custody"]
        self._max_size: int = _parse_size_string(config["max_size"])
        self._max_processes: int = config["max_processes"]
        self._default_ttl_minutes: int = config["default_ttl_minutes"]
        self._max_ttl_minutes: int = config["max_ttl_minutes"]

        self._storage_root = Path(magnus_config["server"]["root"]) / "file_custody"
        self._storage_root.mkdir(parents=True, exist_ok=True)

        for child in self._storage_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                logger.info(f"Cleaned up stale custody dir: {child.name}")

        self._entries: Dict[str, CustodyEntry] = {}
        self._lock = threading.Lock()

    def _get_storage_size(self) -> int:
        total = 0
        for dirpath, _, filenames in os.walk(self._storage_root):
            for f in filenames:
                total += os.path.getsize(os.path.join(dirpath, f))
        return total

    def store_file(
        self,
        filename: str,
        file_obj: BinaryIO,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
    ) -> str:
        if expire_minutes is None:
            expire_minutes = self._default_ttl_minutes
        expire_minutes = min(expire_minutes, self._max_ttl_minutes)

        entry_id = uuid.uuid4().hex

        # 先占位再写文件，避免并发请求绕过 _max_processes 限制
        placeholder = CustodyEntry(
            entry_id=entry_id,
            file_dir=self._storage_root / entry_id,
            original_filename=filename,
            is_directory=is_directory,
            expires_at=0.0,
        )
        with self._lock:
            if len(self._entries) >= self._max_processes:
                raise RuntimeError(
                    f"File custody limit reached ({self._max_processes}). "
                    "Try again later or increase max_processes."
                )
            self._entries[entry_id] = placeholder

        if self._get_storage_size() >= self._max_size:
            with self._lock:
                self._entries.pop(entry_id, None)
            raise RuntimeError(
                "File custody storage full. "
                "Wait for entries to expire or increase max_size."
            )

        file_dir = placeholder.file_dir
        file_dir.mkdir(parents=True, exist_ok=True)

        file_path = file_dir / filename
        try:
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file_obj, f)

            if self._get_storage_size() > self._max_size:
                raise RuntimeError(
                    "File custody storage exceeded after write. File removed."
                )
        except Exception:
            with self._lock:
                self._entries.pop(entry_id, None)
            shutil.rmtree(file_dir, ignore_errors=True)
            raise

        # 写入成功，更新过期时间使 entry 生效
        placeholder.expires_at = time.time() + expire_minutes * 60

        logger.info(f"File custody stored: {entry_id}, filename={filename}, expire_minutes={expire_minutes}")
        return entry_id

    def get_entry(self, token: str) -> Optional[CustodyEntry]:
        with self._lock:
            entry = self._entries.get(token)
        if entry is None:
            return None
        if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
            return None
        return entry

    def get_file_path(self, token: str) -> Optional[Tuple[Path, str, bool]]:
        entry = self.get_entry(token)
        if entry is None:
            return None
        file_path = entry.file_dir / entry.original_filename
        if not file_path.exists():
            return None
        return (file_path, entry.original_filename, entry.is_directory)

    async def cleanup_loop(self) -> None:
        logger.info("File custody cleanup loop started.")
        while True:
            await asyncio.sleep(30)
            now = time.time()
            with self._lock:
                snapshot = list(self._entries.items())
            expired_ids = [
                eid for eid, entry in snapshot
                if entry.expires_at > 0.0 and now >= entry.expires_at
            ]
            for eid in expired_ids:
                with self._lock:
                    entry = self._entries.pop(eid, None)
                if entry is None:
                    continue
                if entry.file_dir.exists():
                    await asyncio.to_thread(shutil.rmtree, entry.file_dir, True)
                logger.info(f"File custody expired: {eid}")

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        logger.info(f"Shutting down file custody manager ({len(entries)} entries)...")
        for entry in entries:
            if entry.file_dir.exists():
                shutil.rmtree(entry.file_dir, ignore_errors=True)


file_custody_manager = FileCustodyManager()
