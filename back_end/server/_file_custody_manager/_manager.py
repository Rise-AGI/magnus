# back_end/server/_file_custody_manager/_manager.py
"""FileCustodyManager 主类。详细线程安全说明见类 docstring。"""
import os
import json
import time
import shutil
import random
import asyncio
import threading
from pathlib import Path
from typing import Any, Optional, Dict, Tuple, BinaryIO

from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from . import logger
from ._types import (
    CustodyEntry,
    CustodyLimitError,
    CustodyStorageFullError,
    FileTooLargeError,
)
from ._word_list import _PRIMES, _WORDS


_COPY_CHUNK_SIZE = 64 * 1024


class FileCustodyManager:
    """文件托管管理器（进程内单例）。

    ``threading.Lock``：临界区做 dict / 计数 / 极短 syscall，以及永久条目
    增删时的 manifest dump（需要遍历 ``self._entries``，必须在锁内）；耗时
    I/O（chunked write、rmtree）一律在 lock 外。``shutdown()`` 在进程退出
    阶段同步调用。

    async endpoint 调用 ``store_file`` / ``delete_entry`` 应包
    ``asyncio.to_thread``；``get_entry`` / ``get_file_path`` 锁内只做 dict
    查找加一次 ``Path.exists`` 的 stat，可直接调。
    """

    def __init__(self):
        config = magnus_config["server"]["file_custody"]
        self._max_size: int = _parse_size_string(config["max_size"])
        raw_file_size = config["max_file_size"]
        self._max_file_size: Optional[int] = _parse_size_string(raw_file_size) if raw_file_size is not None else None
        self._max_processes: int = config["max_processes"]
        self._default_ttl_minutes: int = config["default_ttl_minutes"]
        self._max_ttl_minutes: int = config["max_ttl_minutes"]

        self._storage_root = Path(magnus_config["server"]["root"]) / "file_custody"
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._storage_root / "_manifest.json"

        self._entries: Dict[str, CustodyEntry] = {}
        self._lock = threading.Lock()
        self._rng = random.SystemRandom()

        # 恢复永久条目，清理非永久残留
        manifest = self._load_manifest()
        restored = set()
        for token, meta in manifest.items():
            entry_dir = self._storage_root / token
            file_path = entry_dir / meta["filename"]
            if file_path.exists():
                self._entries[token] = CustodyEntry(
                    entry_id=token,
                    file_dir=entry_dir,
                    original_filename=meta["filename"],
                    is_directory=meta.get("is_directory", False),
                    expires_at=float("inf"),
                    permanent=True,
                    file_size=file_path.stat().st_size,
                )
                restored.add(token)
                logger.info(f"Restored permanent custody entry: {token}")

        # 清理不在 manifest 中的旧目录
        for child in self._storage_root.iterdir():
            if child.is_dir() and child.name not in restored:
                shutil.rmtree(child, ignore_errors=True)
                logger.info(f"Cleaned up stale custody dir: {child.name}")

        # 同步 manifest（移除磁盘已丢失的条目）
        if set(manifest.keys()) != restored:
            self._save_manifest()

        self._current_size = sum(e.file_size for e in self._entries.values())

    def _generate_token(self) -> str:
        for _ in range(64):
            prime = self._rng.choice(_PRIMES)
            words = self._rng.sample(_WORDS, 3)
            token = f"{prime}-{words[0]}-{words[1]}-{words[2]}"
            if token not in self._entries:
                return token
        raise RuntimeError("Failed to generate unique token after 64 attempts")

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        if not self._manifest_path.exists():
            return {}
        try:
            with open(self._manifest_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt manifest file, starting fresh.")
            return {}

    def _save_manifest(self) -> None:
        data: Dict[str, Dict[str, Any]] = {}
        for eid, entry in self._entries.items():
            if entry.permanent:
                data[eid] = {
                    "filename": entry.original_filename,
                    "is_directory": entry.is_directory,
                }
        tmp = self._manifest_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._manifest_path)

    def store_file(
        self,
        filename: str,
        file_obj: BinaryIO,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
        max_downloads: Optional[int] = None,
        permanent: bool = False,
    ) -> str:
        # permanent 条目由服务端内部代码控制（如头像），不受 max_ttl 限制
        if not permanent:
            if expire_minutes is None:
                expire_minutes = self._default_ttl_minutes
            expire_minutes = min(expire_minutes, self._max_ttl_minutes)

        # 先占位再写文件，避免并发请求绕过 _max_processes 限制
        with self._lock:
            if len(self._entries) >= self._max_processes:
                raise CustodyLimitError(self._max_processes)
            if self._current_size >= self._max_size:
                raise CustodyStorageFullError()
            entry_id = self._generate_token()
            placeholder = CustodyEntry(
                entry_id = entry_id,
                file_dir = self._storage_root / entry_id,
                original_filename = filename,
                is_directory = is_directory,
                expires_at = 0.0,
                permanent = permanent,
                max_downloads = max_downloads,
            )
            self._entries[entry_id] = placeholder

        file_dir = placeholder.file_dir
        file_dir.mkdir(parents=True, exist_ok=True)

        file_path = file_dir / filename
        try:
            with open(file_path, "wb") as f:
                written = 0
                while True:
                    chunk = file_obj.read(_COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if self._max_file_size is not None and written > self._max_file_size:
                        raise FileTooLargeError(filename, self._max_file_size)
                    f.write(chunk)

            with self._lock:
                self._current_size += written
                placeholder.file_size = written
                if self._current_size > self._max_size:
                    self._current_size -= written
                    placeholder.file_size = 0
                    raise CustodyStorageFullError()
        except Exception:
            with self._lock:
                self._entries.pop(entry_id, None)
            shutil.rmtree(file_dir, ignore_errors=True)
            raise

        # 写入成功，更新过期时间使 entry 生效
        if permanent:
            placeholder.expires_at = float("inf")
            with self._lock:
                self._save_manifest()
        else:
            assert expire_minutes is not None
            placeholder.expires_at = time.time() + expire_minutes * 60

        logger.info(f"File custody stored: {entry_id}, filename={filename}, permanent={permanent}")
        return entry_id

    def get_entry(self, token: str) -> Optional[CustodyEntry]:
        with self._lock:
            entry = self._entries.get(token)
        if entry is None:
            return None
        if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
            return None
        return entry

    def get_file_path(self, token: str) -> Optional[Tuple[Path, str, bool, bool]]:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
                return None
            if entry.max_downloads is not None and entry.download_count >= entry.max_downloads:
                return None
            file_path = entry.file_dir / entry.original_filename
            if not file_path.exists():
                return None
            entry.download_count += 1
            exhausted = entry.max_downloads is not None and entry.download_count >= entry.max_downloads
        return (file_path, entry.original_filename, entry.is_directory, exhausted)

    def delete_entry(self, token: str) -> None:
        with self._lock:
            entry = self._entries.pop(token, None)
            if entry is not None:
                self._current_size -= entry.file_size
                if entry.permanent:
                    self._save_manifest()
        if entry is not None and entry.file_dir.exists():
            shutil.rmtree(entry.file_dir, ignore_errors=True)
            logger.info(f"File custody purged: {token}")

    async def cleanup_loop(self) -> None:
        logger.info("File custody cleanup loop started.")
        while True:
            await asyncio.sleep(30)
            now = time.time()
            with self._lock:
                snapshot = list(self._entries.items())
            expired_ids = [
                eid for eid, entry in snapshot
                if (entry.expires_at > 0.0 and now >= entry.expires_at)
                or (entry.max_downloads is not None and entry.download_count >= entry.max_downloads)
            ]
            for eid in expired_ids:
                with self._lock:
                    entry = self._entries.pop(eid, None)
                    if entry is not None:
                        self._current_size -= entry.file_size
                if entry is None:
                    continue
                if entry.file_dir.exists():
                    await asyncio.to_thread(shutil.rmtree, entry.file_dir, True)
                logger.info(f"File custody expired: {eid}")

    def shutdown(self) -> None:
        with self._lock:
            ephemeral = [e for e in self._entries.values() if not e.permanent]
            for e in ephemeral:
                self._entries.pop(e.entry_id, None)
                self._current_size -= e.file_size
        logger.info(f"Shutting down file custody manager ({len(ephemeral)} ephemeral entries cleaned, "
                     f"{len(self._entries)} permanent entries preserved)...")
        for entry in ephemeral:
            if entry.file_dir.exists():
                shutil.rmtree(entry.file_dir, ignore_errors=True)
