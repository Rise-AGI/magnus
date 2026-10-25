# back_end/server/_file_custody_manager/_manager.py
"""FileCustodyManager 主类。详细线程安全说明见类 docstring。"""
import os
import re
import json
import time
import shutil
import random
import asyncio
import threading
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any, Literal, Optional, Dict, Tuple, BinaryIO

from library import is_disk_full_oserror

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

# Shape of the SDK's offline-generated relay token (`relay-<uuid4 hex>`). An explicit
# token reaches store_file from a job's drop-dir meta.json — untrusted input on
# no-network relay sites — and is used verbatim as the on-disk entry dir, so it must be
# validated against this exact shape to keep it a single leaf under the storage root
# (no path traversal). filename is sanitized separately by _sanitize_filename.
_RELAY_TOKEN_RE = re.compile(r"^relay-[0-9a-f]{32}\Z")


def _sanitize_filename(name: str) -> str:
    """Strip path components from a user-supplied upload filename.

    ``store_file`` later joins ``file_dir / filename``; without this, a
    hostile filename like ``"../etc/passwd"`` would let the on-disk write
    escape the per-entry directory (and with enough leading ``..`` would
    escape the storage root entirely). ``os.path.basename`` handles the
    common path-separator cases; the remaining ``""`` / ``"."`` / ``".."``
    / NUL cases would still make the join refer to something other than a
    fresh leaf file, so collapse them to a known-safe fallback.
    """
    base = os.path.basename(name)
    if not base or base in (".", "..") or "\0" in base:
        return "upload"
    return base


async def _settle_task(
    task: asyncio.Task,
) -> Any:
    """Join an already-cancelled caller's I/O before it releases file ownership."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    return task.result()


class FileCustodyManager:
    """文件托管管理器（进程内单例）。

    ``threading.Lock``：临界区做 dict / 计数 / 极短 syscall，以及永久条目
    增删时的 manifest dump（需要遍历 ``self._entries``，必须在锁内）；耗时
    I/O（chunked write、rmtree）一律在 lock 外。``shutdown()`` 在进程退出
    阶段同步调用。

    上传通过 ``store_file_async`` 等待复制线程收尾后才允许请求关闭文件。
    下载通过 ``acquire_file`` / ``release_file`` 保护在途读取，终次下载、
    TTL 和显式删除只标记删除，最后一个读取者退出后才释放磁盘预算。
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
        self._changed = threading.Condition(self._lock)
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
            if entry.permanent and not entry.writing and not entry.delete_pending:
                data[eid] = {
                    "filename": entry.original_filename,
                    "is_directory": entry.is_directory,
                }
        tmp = self._manifest_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._manifest_path)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                logger.warning("Cannot remove failed custody manifest temporary file", exc_info=True)
            raise

    def store_file(
        self,
        filename: str,
        file_obj: BinaryIO,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
        max_downloads: Optional[int] = None,
        permanent: bool = False,
        explicit_token: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        # Sanitize at the entry point so all callers are protected centrally
        # rather than each one needing to remember `os.path.basename`.
        filename = _sanitize_filename(filename)

        # explicit_token is untrusted on relay sites (it comes from a job-written
        # meta.json) and is used verbatim as the on-disk entry directory. Reject any
        # token that isn't the SDK's exact relay shape so it can't traverse out of the
        # storage root. None (the HTTP path) skips this and uses _generate_token.
        if explicit_token is not None and not _RELAY_TOKEN_RE.match(explicit_token):
            raise ValueError(f"invalid explicit custody token: {explicit_token!r}")

        # permanent 条目由服务端内部代码控制（如头像），不受 max_ttl 限制
        if not permanent:
            if expire_minutes is None:
                expire_minutes = self._default_ttl_minutes
            expire_minutes = min(expire_minutes, self._max_ttl_minutes)

        # 先占位再写文件，避免并发请求绕过 _max_processes 限制
        with self._changed:
            # explicit_token：远端无网执行时 SDK 离线生成的 relay token，由 host 侧
            # staging 把暂存产物按它注册进 custody。重复注册（同一 drop 被多次拉回）
            # 幂等返回，不重写、不重复计数。explicit_token=None 时走原 _generate_token
            # 路径，HTTP 上传行为完全不变。
            while explicit_token is not None and explicit_token in self._entries:
                existing = self._entries[explicit_token]
                if existing.writing or existing.deleting:
                    self._changed.wait(timeout=0.1)
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError()
                    continue
                if existing.delete_pending:
                    raise CustodyStorageFullError()
                return explicit_token
            if len(self._entries) >= self._max_processes:
                raise CustodyLimitError(self._max_processes)
            if self._current_size >= self._max_size:
                raise CustodyStorageFullError()
            entry_id = explicit_token if explicit_token is not None else self._generate_token()
            placeholder = CustodyEntry(
                entry_id = entry_id,
                file_dir = self._storage_root / entry_id,
                original_filename = filename,
                is_directory = is_directory,
                expires_at = 0.0,
                permanent = permanent,
                max_downloads = max_downloads,
                writing = True,
            )
            self._entries[entry_id] = placeholder

        file_dir = placeholder.file_dir
        file_path = file_dir / filename
        try:
            file_dir.mkdir(parents=True, exist_ok=True)
            with open(file_path, "wb") as f:
                written = 0
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError()
                    chunk = file_obj.read(_COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if self._max_file_size is not None and written > self._max_file_size:
                        raise FileTooLargeError(filename, self._max_file_size)
                    # Charge bytes before writing, including all concurrent uploads.
                    # Failed writes retain the reservation until their files are gone.
                    with self._lock:
                        if placeholder.delete_pending:
                            raise CancelledError()
                        if self._current_size + len(chunk) > self._max_size:
                            raise CustodyStorageFullError()
                        self._current_size += len(chunk)
                        placeholder.file_size += len(chunk)
                    f.write(chunk)

            with self._changed:
                if placeholder.delete_pending or (cancel_event is not None and cancel_event.is_set()):
                    raise CancelledError()
                placeholder.writing = False
                if permanent:
                    placeholder.expires_at = float("inf")
                    self._save_manifest()
                else:
                    assert expire_minutes is not None
                    placeholder.expires_at = time.time() + expire_minutes * 60
                self._changed.notify_all()
        except BaseException as error:
            with self._changed:
                placeholder.writing = False
                placeholder.expires_at = 0.0
                placeholder.delete_pending = True
                self._changed.notify_all()
            self.delete_entry(
                entry_id,
                expected_entry = placeholder,
            )
            # 真实磁盘满（ENOSPC/EDQUOT）和配额满对调用方是同一回事："存不下了"。
            # 翻译成域错误，让 API 回 503 清晰提示，而不是裸 OSError 冒成 500。
            if isinstance(error, OSError) and is_disk_full_oserror(error):
                raise CustodyStorageFullError() from error
            raise

        logger.info(f"File custody stored: {entry_id}, filename={filename}, permanent={permanent}")
        return entry_id

    async def store_file_async(
        self,
        filename: str,
        file_obj: BinaryIO,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
        max_downloads: Optional[int] = None,
        permanent: bool = False,
    ) -> str:
        cancel_event = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(
            self.store_file,
            filename = filename,
            file_obj = file_obj,
            expire_minutes = expire_minutes,
            is_directory = is_directory,
            max_downloads = max_downloads,
            permanent = permanent,
            cancel_event = cancel_event,
        ))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel_event.set()
            try:
                token = await _settle_task(task)
            except (Exception, asyncio.CancelledError):
                pass
            else:
                # Publication may have won the race with cancellation. Its caller
                # never received the token, so it still owns rollback.
                await _settle_task(asyncio.create_task(asyncio.to_thread(
                    self.delete_entry,
                    token,
                )))
            raise

    def get_file_status(
        self,
        token: str,
    ) -> Literal["ready", "pending", "unavailable", "unknown"]:
        """Observe availability without a lease, download count or TTL renewal."""
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return "unknown"
            if entry.delete_pending or entry.deleting:
                return "unavailable"
            if entry.writing:
                return "pending"
            if (
                entry.expires_at == 0.0
                or time.time() >= entry.expires_at
                or (entry.max_downloads is not None and entry.download_count >= entry.max_downloads)
                or not (entry.file_dir / entry.original_filename).is_file()
            ):
                return "unavailable"
            return "ready"

    def get_entry(
        self,
        token: str,
    ) -> Optional[CustodyEntry]:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry.writing or entry.delete_pending:
                return None
            if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
                return None
            return entry

    def acquire_file(
        self,
        token: str,
        count_download: bool = True,
    ) -> Optional[Tuple[Path, str, bool, bool]]:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry.writing or entry.delete_pending:
                return None
            if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
                return None
            if count_download and entry.max_downloads is not None and entry.download_count >= entry.max_downloads:
                return None
            file_path = entry.file_dir / entry.original_filename
            if not file_path.exists():
                return None
            entry.active_readers += 1
            if count_download:
                entry.download_count += 1
            exhausted = entry.max_downloads is not None and entry.download_count >= entry.max_downloads
        return (file_path, entry.original_filename, entry.is_directory, exhausted)

    def get_file_path(
        self,
        token: str,
    ) -> Optional[Tuple[Path, str, bool, bool]]:
        """Acquire a counted download; the caller must pair it with release_file."""
        return self.acquire_file(token)

    def release_file(
        self,
        token: str,
    ) -> None:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry.active_readers == 0:
                return
            entry.active_readers -= 1
            remove = entry.delete_pending or time.time() >= entry.expires_at or (
                entry.max_downloads is not None and entry.download_count >= entry.max_downloads
            )
        if remove:
            self.delete_entry(
                token,
                expected_entry = entry,
            )

    async def release_file_async(
        self,
        token: str,
    ) -> None:
        task = asyncio.create_task(asyncio.to_thread(
            self.release_file,
            token,
        ))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await _settle_task(task)
            raise

    def delete_entry(
        self,
        token: str,
        expected_entry: Optional[CustodyEntry] = None,
    ) -> None:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or (expected_entry is not None and entry is not expected_entry):
                return
            entry.delete_pending = True
            # Retire the durable identity before deferring physical deletion for
            # a reader. A restart must not restore an already replaced avatar.
            if entry.permanent and not entry.writing and entry.expires_at > 0.0:
                self._save_manifest()
            if entry.writing or entry.active_readers or entry.deleting:
                return
            entry.deleting = True
        try:
            shutil.rmtree(entry.file_dir)
        except FileNotFoundError:
            pass
        except OSError:
            # Keep the slot and byte reservation until cleanup really succeeds.
            # This also prevents a relay retry reusing the directory while an old
            # removal is in flight.
            with self._changed:
                entry.deleting = False
                self._changed.notify_all()
            logger.warning("Cannot remove custody entry %s; cleanup will retry", token, exc_info=True)
            return
        with self._changed:
            self._entries.pop(token)
            self._current_size -= entry.file_size
            self._changed.notify_all()
        logger.info(f"File custody purged: {token}")

    def cleanup_expired(
        self,
    ) -> None:
        now = time.time()
        with self._lock:
            expired_entries = [
                (token, entry) for token, entry in self._entries.items()
                if entry.delete_pending
                or (entry.expires_at > 0.0 and now >= entry.expires_at)
                or (entry.max_downloads is not None and entry.download_count >= entry.max_downloads)
            ]
        for token, entry in expired_entries:
            try:
                self.delete_entry(
                    token,
                    expected_entry = entry,
                )
            except OSError:
                logger.warning("Custody cleanup failed for %s; will retry", token, exc_info=True)

    async def cleanup_loop(self) -> None:
        logger.info("File custody cleanup loop started.")
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(self.cleanup_expired)

    def shutdown(self) -> None:
        with self._lock:
            ephemeral = [e for e in self._entries.values() if not e.permanent]
        for entry in ephemeral:
            self.delete_entry(
                entry.entry_id,
                expected_entry = entry,
            )
        logger.info(f"Shutting down file custody manager ({len(ephemeral)} ephemeral entries retired, "
                     f"{len(self._entries)} permanent entries preserved)...")
