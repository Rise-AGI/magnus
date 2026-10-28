"""Durable tus reception; publication commits in the existing custody index."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from .._custody_receipts import _sync_directory

logger = logging.getLogger(__name__)
CHUNK_SIZE = 4 * 1024 * 1024
SESSION_TTL = 24 * 3600
_KEY = re.compile(r"([0-9]{10})-([0-9a-f]{32})\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


class UploadError(Exception):
    def __init__(
        self,
        status: int,
        detail: str,
    ):
        super().__init__(detail)
        self.status = status


class UploadStore:
    def __init__(
        self,
        custody,
    ):
        self.custody = custody
        self.index = custody._index
        self.root = custody._storage_root.parent / "upload_sessions"
        if self.root.is_symlink():
            raise ValueError("Linked upload storage")
        self.root.mkdir(
            mode=0o700,
            exist_ok=True,
        )
        _sync_directory(self.root.parent)
        self.lock = threading.RLock()
        self.busy = {}
        self.closed = False
        self.pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="custody-publish",
        )
        with self.index.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, creation_key TEXT NOT NULL,
                spec TEXT NOT NULL, length INTEGER NOT NULL, offset INTEGER NOT NULL,
                state TEXT NOT NULL, token TEXT, created REAL NOT NULL,
                expires REAL NOT NULL, error TEXT, retry_at REAL NOT NULL,
                UNIQUE(owner, creation_key)
            )""")
            db.execute("UPDATE uploads SET state='open' WHERE state='finalizing'")
        self.cleanup()

    def _path(
        self,
        upload_id: str,
    ) -> Path:
        if not _ID.fullmatch(upload_id):
            raise UploadError(
                404,
                "Upload not found",
            )
        path = self.root / upload_id
        if path.is_symlink():
            raise UploadError(
                503,
                "Upload storage is unavailable",
            )
        return path

    def _read(
        self,
        upload_id: str,
        owner: str,
    ) -> dict:
        self._path(upload_id)
        with self.index.transaction() as db:
            cursor = db.execute(
                "SELECT * FROM uploads WHERE id=? AND owner=?",
                (upload_id, str(owner)),
            )
            row = cursor.fetchone()
            names = [column[0] for column in cursor.description]
        if row is None:
            raise UploadError(
                404,
                "Upload not found",
            )
        result = dict(
            zip(
                names,
                row,
            )
        )
        result["spec"] = json.loads(result["spec"])
        if result["state"] in {"open", "finalizing"} and result["expires"] <= time.time():
            raise UploadError(
                410,
                "Upload expired",
            )
        return result

    def create(
        self,
        owner: str,
        creation_key: str,
        length: int,
        spec: dict,
    ) -> dict:
        canonical = json.dumps(
            spec,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(canonical.encode()) > 8192:
            raise UploadError(
                400,
                "Upload metadata is too large",
            )
        match = _KEY.fullmatch(creation_key)
        if not match:
            raise UploadError(
                400,
                "Invalid upload creation key",
            )
        with self.lock:
            if self.closed:
                raise UploadError(
                    503,
                    "Uploads are shutting down",
                )
            self.cleanup()
            with self.index.transaction() as db:
                previous = db.execute(
                    "SELECT id, length, spec FROM uploads WHERE owner=? AND creation_key=?",
                    (str(owner), creation_key),
                ).fetchone()
                if previous:
                    if previous[1:] != (length, canonical):
                        raise UploadError(
                            409,
                            "Upload creation key is bound to different content or options",
                        )
                    return self._read(
                        previous[0],
                        owner,
                    )
                if db.execute("SELECT count(*) FROM uploads").fetchone()[0] >= 4096:
                    raise UploadError(
                        429,
                        "Upload receipt ledger is full; retry later",
                    )
                now = time.time()
                if not now - SESSION_TTL <= int(match[1]) <= now + 300:
                    raise UploadError(
                        410,
                        "Upload creation key expired; no new upload was created",
                    )
                limit = min(
                    self.custody._max_size,
                    self.custody._max_file_size or self.custody._max_size,
                )
                if not 0 <= length <= limit:
                    raise UploadError(
                        413,
                        "Upload exceeds custody size limits",
                    )
                count, reserved = db.execute(
                    "SELECT count(*), coalesce(sum(length),0) FROM uploads WHERE state IN ('open','finalizing')"
                ).fetchone()
                if count >= min(
                    32,
                    self.custody._max_processes,
                ):
                    raise UploadError(
                        429,
                        "Too many unfinished uploads",
                    )
                if reserved + length > self.custody._max_size:
                    raise UploadError(
                        503,
                        "Unfinished upload storage is full",
                    )
                # Reserve declared sizes, including bytes that have not arrived.
                remaining = db.execute(
                    "SELECT coalesce(sum(length-offset),0) FROM uploads WHERE state IN ('open','finalizing')"
                ).fetchone()[0]
                if shutil.disk_usage(self.root).free < remaining + length + CHUNK_SIZE:
                    raise UploadError(
                        503,
                        "Insufficient upload disk space",
                    )
                upload_id = uuid.uuid4().hex
                descriptor = os.open(
                    self._path(upload_id),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.close(descriptor)
                try:
                    _sync_directory(self.root)
                    db.execute(
                        "INSERT INTO uploads VALUES (?,?,?,?,?,0,'open',NULL,?,?,NULL,0)",
                        (
                            upload_id,
                            str(owner),
                            creation_key,
                            canonical,
                            length,
                            now,
                            now + SESSION_TTL,
                        ),
                    )
                except BaseException:
                    self._path(upload_id).unlink(missing_ok=True)
                    raise
            return self._read(
                upload_id,
                owner,
            )

    def claim(
        self,
        upload_id: str,
        owner: str,
        offset: int,
        lease: str,
    ) -> dict:
        with self.lock:
            if self.closed:
                raise UploadError(
                    503,
                    "Uploads are shutting down",
                )
            row = self._read(
                upload_id,
                owner,
            )
            if row["state"] != "open" or upload_id in self.busy:
                raise UploadError(
                    409,
                    "Upload is not writable",
                )
            if offset != row["offset"]:
                raise UploadError(
                    409,
                    "Upload offset differs; query HEAD before resuming",
                )
            if len(self.busy) >= 4:
                raise UploadError(
                    429,
                    "Upload receivers are busy",
                )
            self.busy[upload_id] = lease
            return row

    def release(
        self,
        upload_id: str,
        lease: str = "publication",
    ) -> None:
        with self.lock:
            if self.busy.get(upload_id) == lease:
                self.busy.pop(
                    upload_id,
                    None,
                )

    def append(
        self,
        row: dict,
        data: bytes,
        checksum: tuple[str, bytes] | None,
    ) -> int:
        if len(data) > CHUNK_SIZE or row["offset"] + len(data) > row["length"]:
            raise UploadError(
                413,
                "Upload chunk exceeds its bound",
            )
        if checksum is not None and hashlib.new(checksum[0], data).digest() != checksum[1]:
            raise UploadError(
                460,
                "Upload checksum mismatch",
            )
        path = self._path(row["id"])
        descriptor = os.open(
            path,
            os.O_RDWR
            | getattr(
                os,
                "O_NOFOLLOW",
                0,
            ),
        )
        with os.fdopen(
            descriptor,
            "r+b",
        ) as stream:
            if os.fstat(stream.fileno()).st_size < row["offset"]:
                raise UploadError(
                    503,
                    "Upload data is incomplete",
                )
            # Only the committed offset is durable acknowledgement. Discard a
            # previous writer's uncommitted tail after a crash or failed commit.
            stream.truncate(row["offset"])
            stream.seek(row["offset"])
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        offset = row["offset"] + len(data)
        with self.index.transaction() as db:
            db.execute(
                "UPDATE uploads SET offset=? WHERE id=?",
                (offset, row["id"]),
            )
        return offset

    def status(
        self,
        upload_id: str,
        owner: str,
    ) -> dict:
        with self.lock:
            row = self._read(
                upload_id,
                owner,
            )
            if row["state"] == "finalizing" and upload_id not in self.busy:
                with self.index.transaction() as db:
                    db.execute(
                        "UPDATE uploads SET state='open' WHERE id=? AND token IS NULL",
                        (upload_id,),
                    )
                row["state"] = "open"
            if (
                not self.closed
                and row["state"] == "open"
                and row["offset"] == row["length"]
                and row["retry_at"] <= time.time()
                and upload_id not in self.busy
                and len(self.busy) < 4
            ):
                with self.index.transaction() as db:
                    db.execute(
                        "UPDATE uploads SET state='finalizing', error=NULL WHERE id=?",
                        (upload_id,),
                    )
                self.busy[upload_id] = "publication"
                try:
                    self.pool.submit(
                        self._publish,
                        row,
                    )
                except BaseException:
                    self.busy.pop(
                        upload_id,
                        None,
                    )
                    with self.index.transaction() as db:
                        db.execute(
                            "UPDATE uploads SET state='open' WHERE id=?",
                            (upload_id,),
                        )
                    raise
                row["state"] = "finalizing"
            return row

    def _publish(
        self,
        row: dict,
    ) -> None:
        upload_id = row["id"]
        try:
            path = self._path(upload_id)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while data := stream.read(1024 * 1024):
                    digest.update(data)
            expected = row["spec"].get("sha256")
            if expected and digest.hexdigest() != expected:
                path.unlink(missing_ok=True)
                with self.index.transaction() as db:
                    db.execute(
                        "UPDATE uploads SET state='failed', error='content_checksum' WHERE id=? AND token IS NULL",
                        (upload_id,),
                    )
                return
            self.custody.store_file(
                filename=row["spec"]["filename"],
                file_obj=None,
                expected_size=row["length"],
                receive=lambda destination: self._link_payload(
                    path,
                    destination,
                ),
                upload_id=upload_id,
                **row["spec"]["options"],
            )
            path.unlink(missing_ok=True)
        except Exception:
            # Never disclose raw paths, user metadata or custody secrets. A
            # committed receipt wins even if unlinking the partial file failed.
            logger.warning("Upload publication will be retried")
            with self.index.transaction() as db:
                db.execute(
                    "UPDATE uploads SET state='open', error='publication_unavailable', retry_at=? WHERE id=? AND token IS NULL",
                    (time.time() + 30, upload_id),
                )
        finally:
            self.release(upload_id)

    @staticmethod
    def _link_payload(
        source: Path,
        destination: Path,
    ) -> None:
        try:
            os.link(
                source,
                destination,
            )
        except OSError:
            shutil.copyfile(
                source,
                destination,
            )

    def terminate(
        self,
        upload_id: str,
        owner: str,
    ) -> None:
        with self.lock:
            row = self._read(
                upload_id,
                owner,
            )
            if upload_id in self.busy or row["state"] == "complete":
                raise UploadError(
                    409,
                    "Upload publication cannot be cancelled now",
                )
            # Remove bytes before releasing the reservation.
            self._path(upload_id).unlink(missing_ok=True)
            with self.index.transaction() as db:
                db.execute(
                    "UPDATE uploads SET state='cancelled' WHERE id=?",
                    (upload_id,),
                )

    def cleanup(
        self,
    ) -> None:
        with self.lock:
            now = time.time()
            with self.index.transaction() as db:
                rows = db.execute("SELECT id, state, expires FROM uploads").fetchall()
                known = {row[0] for row in rows}
                for upload_id, state, expires in rows:
                    if upload_id in self.busy:
                        continue
                    if expires <= now or state in {"complete", "cancelled", "failed"}:
                        self._path(upload_id).unlink(missing_ok=True)
                        if expires <= now and state in {"open", "finalizing"}:
                            db.execute(
                                "UPDATE uploads SET state='expired' WHERE id=?",
                                (upload_id,),
                            )
                        if expires + SESSION_TTL < now:
                            db.execute(
                                "DELETE FROM uploads WHERE id=?",
                                (upload_id,),
                            )
                for path in self.root.iterdir():
                    if _ID.fullmatch(path.name) and path.name not in known:
                        path.unlink()

    def shutdown(
        self,
    ) -> None:
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True)
