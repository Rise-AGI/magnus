"""Durable temporary custody metadata; payloads stay in the filesystem."""
from contextlib import closing, contextmanager
import errno
import json
import os
from pathlib import Path
import sqlite3


class CustodyIndex:
    def __init__(
        self,
        path: Path,
    ) -> None:
        self.path = path
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        with self.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise OSError(errno.EIO, "Unsupported custody index; storage left unchanged")
            db.execute("CREATE TABLE IF NOT EXISTS entries (token TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS imports (
                token TEXT PRIMARY KEY, job_id TEXT NOT NULL, digest TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS imports_job_id ON imports (job_id)")
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def transaction(
        self,
    ):
        try:
            # Connections never cross threads or remain open during payload I/O.
            with closing(sqlite3.connect(self.path)) as db, db:
                yield db
        except sqlite3.Error as error:
            code = errno.ENOSPC if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL else errno.EIO
            raise OSError(code, "Custody index is unavailable; storage left unchanged") from None

    def load(
        self,
    ) -> dict:
        with self.transaction() as db:
            return {token: json.loads(metadata) for token, metadata in db.execute("SELECT token, metadata FROM entries")}

    def save(
        self,
        token: str,
        metadata: dict,
        receipt: tuple[str, str] | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?, ?)",
                (token, json.dumps(metadata, allow_nan=False)),
            )
            if receipt is not None:
                # Visibility and import identity commit together. A crash after
                # the final download must not allow a remote retry to resurrect it.
                db.execute(
                    "INSERT INTO imports VALUES (?, ?, ?)",
                    (token, *receipt),
                )

    def remove(
        self,
        token: str,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "DELETE FROM entries WHERE token = ?",
                (token,),
            )

    def receipt(
        self,
        job_id: str,
        token: str,
    ) -> str | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT job_id, digest FROM imports WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        if row[0] != job_id:
            raise ValueError("Custody token belongs to another Job")
        return row[1]

    def acknowledge(
        self,
        job_id: str,
        token: str,
        digest: str,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "DELETE FROM imports WHERE token = ? AND job_id = ? AND digest = ?",
                (token, job_id, digest),
            )

    def acknowledge_receipts(
        self,
        job_id: str,
        receipts: dict[str, str],
    ) -> None:
        with self.transaction() as db:
            pending = db.execute("SELECT token, digest FROM imports WHERE job_id = ?", (job_id,)).fetchall()
            db.executemany(
                "DELETE FROM imports WHERE token = ?",
                [(token,) for token, digest in pending if receipts.get(token) == digest],
            )
