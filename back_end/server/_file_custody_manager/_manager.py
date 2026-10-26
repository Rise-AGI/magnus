# back_end/server/_file_custody_manager/_manager.py
"""FileCustodyManager 主类。详细线程安全说明见类 docstring。"""
import os
import re
import json
import math
import time
import shutil
import random
import asyncio
import threading
from concurrent.futures import CancelledError
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Dict, Tuple, BinaryIO

from library import is_disk_full_oserror

from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from .._custody_receipts import _sync_directory
from . import logger
from ._types import (
    CustodyEntry,
    CustodyLimitError,
    CustodyStorageFullError,
    FileTooLargeError,
)
from ._word_list import _PRIMES, _WORDS
from ._index import CustodyIndex


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

    ``threading.Lock``：临界区做 dict / 计数及元数据事务的原子提交；耗时
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
        self._index = CustodyIndex(self._storage_root / "_custody.sqlite3")

        self._entries: Dict[str, CustodyEntry] = {}
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._rng = random.SystemRandom()
        self._closed = False

        manifest = self._load_manifest()
        temporary = self._index.load()
        self._validate_manifest(temporary, temporary=True)
        restored = set()
        for token, meta in {**manifest, **temporary}.items():
            if token in manifest and token in temporary:
                raise ValueError("Conflicting custody manifests; storage left unchanged")
            entry_dir = self._storage_root / token
            file_path = entry_dir / meta["filename"]
            if entry_dir.is_symlink() or file_path.is_symlink():
                raise ValueError("Symlink in custody manifest; storage left unchanged")
            permanent = token in manifest
            expiry = float("inf") if permanent or meta["expires_at"] is None else meta["expires_at"]
            limit = None if permanent else meta["max_downloads"]
            downloads = 0 if permanent else meta["download_count"]
            if file_path.is_file() and time.time() < expiry and (limit is None or downloads < limit):
                self._entries[token] = CustodyEntry(
                    entry_id = token,
                    file_dir = entry_dir,
                    original_filename = meta["filename"],
                    is_directory = meta.get("is_directory", False),
                    expires_at = expiry,
                    permanent = permanent,
                    max_downloads = limit,
                    download_count = downloads,
                    file_size = file_path.stat().st_size,
                    job_id = meta.get("job_id"),
                    ttl_seconds = meta.get("ttl_seconds", 0),
                )
                restored.add(token)

        # Do not reclaim anything until both manifests have been validated.
        # A damaged index must never turn all accepted uploads into orphans.
        for child in self._storage_root.iterdir():
            if child.is_dir() and child.name not in restored:
                if child.is_symlink():
                    child.unlink()
                else:
                    shutil.rmtree(child)

        if set(manifest) != restored.intersection(manifest):
            self._save_manifest()
        for token in set(temporary) - restored:
            self._index.remove(token)

        self._current_size = sum(e.file_size for e in self._entries.values())
        logger.info("Restored %d custody entries", len(self._entries))

    def _generate_token(self) -> str:
        for _ in range(64):
            prime = self._rng.choice(_PRIMES)
            words = self._rng.sample(_WORDS, 3)
            token = f"{prime}-{words[0]}-{words[1]}-{words[2]}"
            if token not in self._entries:
                return token
        raise RuntimeError("Failed to generate unique token after 64 attempts")

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        return self._read_manifest(
            self._manifest_path,
            temporary = False,
        )

    @staticmethod
    def _read_manifest(
        path: Path,
        temporary: bool,
    ) -> Dict[str, Dict[str, Any]]:
        try:
            with path.open(encoding="utf-8") as source:
                data = json.load(source)
        except FileNotFoundError:
            return {}
        except (ValueError, OSError):
            raise ValueError("Cannot read custody manifest; storage left unchanged") from None
        FileCustodyManager._validate_manifest(data, temporary)
        return data

    @staticmethod
    def _validate_manifest(
        data: dict,
        temporary: bool,
    ) -> None:
        try:
            if not isinstance(data, dict):
                raise ValueError()
            for token, meta in data.items():
                if (
                    not isinstance(token, str)
                    or not re.fullmatch(r"[0-9A-Za-z_-]+", token)
                    or not isinstance(meta, dict)
                    or not isinstance(meta["filename"], str)
                    or meta["filename"] != _sanitize_filename(meta["filename"])
                    or not isinstance(meta.get("is_directory", False), bool)
                ):
                    raise ValueError()
                if temporary and (
                    (meta["expires_at"] is not None and (
                        type(meta["expires_at"]) not in (int, float)
                        or not math.isfinite(meta["expires_at"])
                    ))
                    or (meta["expires_at"] is None and not meta.get("job_id"))
                    or (meta.get("job_id") is not None and not re.fullmatch(r"[0-9a-f]{16}", meta["job_id"]))
                    or type(meta["ttl_seconds"]) not in (int, float)
                    or not math.isfinite(meta["ttl_seconds"])
                    or type(meta["download_count"]) is not int
                    or meta["download_count"] < 0
                    or (meta["max_downloads"] is not None and (
                        type(meta["max_downloads"]) is not int or meta["max_downloads"] < 1
                    ))
                ):
                    raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("Invalid custody manifest; storage left unchanged") from None

    def _save_manifest(self) -> None:
        data: Dict[str, Dict[str, Any]] = {}
        for eid, entry in self._entries.items():
            if entry.permanent and not entry.writing and not entry.delete_pending:
                data[eid] = {
                    "filename": entry.original_filename,
                    "is_directory": entry.is_directory,
                }
        self._write_manifest(
            self._manifest_path,
            data,
        )

    @staticmethod
    def _entry_metadata(
        entry: CustodyEntry,
    ) -> dict:
        return {
            "filename": entry.original_filename,
            "is_directory": entry.is_directory,
            "expires_at": None if math.isinf(entry.expires_at) else entry.expires_at,
            "max_downloads": entry.max_downloads,
            "download_count": entry.download_count,
            "job_id": entry.job_id,
            "ttl_seconds": entry.ttl_seconds,
        }

    def pending_job_ids(
        self,
    ) -> set[str]:
        with self._lock:
            return {
                entry.job_id for entry in self._entries.values()
                if entry.job_id and not entry.writing and math.isinf(entry.expires_at)
                and not entry.delete_pending
            }

    def finish_job(
        self,
        job_id: str,
    ) -> None:
        with self._lock:
            now = time.time()
            for entry in self._entries.values():
                if entry.job_id != job_id or entry.delete_pending or entry.writing or not math.isinf(entry.expires_at):
                    continue
                previous = entry.expires_at
                entry.expires_at = now + entry.ttl_seconds
                try:
                    self._index.save(entry.entry_id, self._entry_metadata(entry))
                except BaseException:
                    entry.expires_at = previous
                    raise

    def import_receipt(
        self,
        job_id: str,
        token: str,
    ) -> str | None:
        with self._lock:
            return self._index.receipt(job_id, token)

    def acknowledge_import(
        self,
        job_id: str,
        token: str,
        digest: str,
    ) -> None:
        with self._lock:
            self._index.acknowledge(job_id, token, digest)

    def acknowledge_receipts(
        self,
        job_id: str,
        receipts: dict[str, str],
    ) -> None:
        with self._lock:
            self._index.acknowledge_receipts(job_id, receipts)

    @staticmethod
    def _write_manifest(
        path: Path,
        data: Dict[str, Any],
    ) -> None:
        tmp = path.with_suffix(".tmp")
        try:
            descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                json.dump(data, destination, allow_nan=False)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(tmp, path)
            _sync_directory(path.parent)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                logger.warning("Cannot remove failed custody manifest temporary file", exc_info=True)
            raise

    def store_file(
        self,
        filename: str,
        file_obj: BinaryIO | None,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
        max_downloads: Optional[int] = None,
        permanent: bool = False,
        explicit_token: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        *,
        job_id: str | None = None,
        source_digest: str | None = None,
        expected_size: int | None = None,
        receive: Callable[[Path], None] | None = None,
    ) -> str:
        # Sanitize at the entry point so all callers are protected centrally
        # rather than each one needing to remember `os.path.basename`.
        filename = _sanitize_filename(filename)

        # explicit_token is untrusted on relay sites (it comes from a job-written
        # meta.json) and is used verbatim as the on-disk entry directory. Reject any
        # token that isn't the SDK's exact relay shape so it can't traverse out of the
        # storage root. None (the HTTP path) skips this and uses _generate_token.
        if explicit_token is not None and not _RELAY_TOKEN_RE.match(explicit_token):
            raise ValueError("invalid explicit custody token")
        if max_downloads is not None and (type(max_downloads) is not int or max_downloads < 1):
            raise ValueError("max_downloads must be a positive integer")

        if job_id is not None and (permanent or not re.fullmatch(r"[0-9a-f]{16}", job_id)):
            raise ValueError("Invalid custody Job association")
        if source_digest is not None and (not job_id or not explicit_token or not re.fullmatch(r"[0-9a-f]{64}", source_digest)):
            raise ValueError("Invalid custody import receipt")
        if receive is not None and (type(expected_size) is not int or expected_size < 0):
            raise ValueError("A bounded receive requires the exact payload size")

        # permanent 条目由服务端内部代码控制（如头像），不受 max_ttl 限制
        if not permanent:
            if expire_minutes is None:
                expire_minutes = self._default_ttl_minutes
            expire_minutes = min(expire_minutes, self._max_ttl_minutes)

        # 先占位再写文件，避免并发请求绕过 _max_processes 限制
        with self._changed:
            if self._closed:
                raise RuntimeError("File custody is shutting down")
            if source_digest is not None:
                previous = self._index.receipt(job_id, explicit_token)
                if previous is not None:
                    if previous != source_digest:
                        raise ValueError("Custody import identity changed")
                    return explicit_token
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
                    if self._closed:
                        raise RuntimeError("File custody is shutting down")
                    continue
                if existing.delete_pending:
                    raise CustodyStorageFullError()
                if source_digest is not None and existing.job_id != job_id:
                    raise ValueError("Custody token belongs to another Job")
                return explicit_token
            if len(self._entries) >= self._max_processes:
                raise CustodyLimitError(self._max_processes)
            reservation = expected_size if receive is not None else 0
            if self._max_file_size is not None and reservation > self._max_file_size:
                raise FileTooLargeError(filename, self._max_file_size)
            if self._current_size >= self._max_size or self._current_size + reservation > self._max_size:
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
                file_size = reservation,
                job_id = job_id,
                ttl_seconds = 0 if permanent else expire_minutes * 60,
            )
            self._entries[entry_id] = placeholder
            self._current_size += reservation

        file_dir = placeholder.file_dir
        file_path = file_dir / filename
        try:
            file_dir.mkdir(parents=True, exist_ok=True)
            if receive is not None:
                receive(file_path)
                if file_path.stat().st_size != expected_size:
                    raise ValueError("Custody payload size changed during transfer")
                with file_path.open("r+b") as destination:
                    os.fsync(destination.fileno())
            else:
                assert file_obj is not None
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
                    f.flush()
                    os.fsync(f.fileno())
            _sync_directory(file_dir)
            _sync_directory(self._storage_root)
            with self._changed:
                if placeholder.delete_pending or (cancel_event is not None and cancel_event.is_set()):
                    raise CancelledError()
                placeholder.writing = False
                if permanent:
                    placeholder.expires_at = float("inf")
                    self._save_manifest()
                else:
                    assert expire_minutes is not None
                    if placeholder.expires_at == 0.0:
                        placeholder.expires_at = float("inf") if job_id else time.time() + placeholder.ttl_seconds
                    self._index.save(
                        entry_id,
                        self._entry_metadata(placeholder),
                        receipt = (job_id, source_digest) if source_digest is not None else None,
                    )
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

        logger.info("File custody stored: bytes=%d, permanent=%s", placeholder.file_size, permanent)
        return entry_id

    async def store_file_async(
        self,
        filename: str,
        file_obj: BinaryIO,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
        max_downloads: Optional[int] = None,
        permanent: bool = False,
        *,
        job_id: str | None = None,
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
            job_id = job_id,
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
            if count_download:
                entry.download_count += 1
                if not entry.permanent:
                    try:
                        # Commit the claim before exposing bytes, including the
                        # final claim whose reader may outlive graceful shutdown.
                        self._index.save(token, self._entry_metadata(entry))
                    except BaseException:
                        entry.download_count -= 1
                        raise
            entry.active_readers += 1
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
            elif not entry.permanent and not entry.writing and entry.expires_at > 0.0:
                self._index.remove(token)
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
            logger.warning("Cannot remove custody payload; cleanup will retry")
            return
        with self._changed:
            self._entries.pop(token)
            self._current_size -= entry.file_size
            self._changed.notify_all()
        logger.info("File custody payload purged")

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
                logger.warning("Custody cleanup failed; will retry")

    async def cleanup_loop(self) -> None:
        logger.info("File custody cleanup loop started.")
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(self.cleanup_expired)

    def shutdown(self) -> None:
        with self._changed:
            self._closed = True
            for entry in self._entries.values():
                if entry.writing:
                    entry.delete_pending = True
            self._changed.notify_all()
        self.cleanup_expired()
        logger.info("File custody shut down; completed entries retain their original limits")
