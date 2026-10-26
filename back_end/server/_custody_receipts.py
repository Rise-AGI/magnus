"""Durable import receipts, shared by staging and non-consuming status queries."""
import json
import os
from pathlib import Path
import re
import tempfile


_CUSTODY_RECEIPTS_FILE = ".magnus_custody_receipts.json"
_RELAY_TOKEN_RE = re.compile(r"relay-[0-9a-f]{32}\Z")


def _sync_directory(
    directory: str | Path,
) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_custody_receipts(
    local_table: str,
    *,
    durable: bool = False,
) -> dict:
    path = Path(local_table) / _CUSTODY_RECEIPTS_FILE
    try:
        with path.open(encoding="utf-8") as source:
            receipts = json.load(source)
    except FileNotFoundError:
        return {"entries": {}, "logs_complete": False}
    if (
        not isinstance(receipts, dict)
        or not isinstance(receipts.get("entries"), dict)
        or not isinstance(receipts.get("logs_complete"), bool)
        or not all(
            isinstance(token, str)
            and _RELAY_TOKEN_RE.fullmatch(token)
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)
            for token, digest in receipts["entries"].items()
        )
    ):
        raise ValueError("Invalid custody registration receipts")
    if durable:
        # A failed directory sync can leave a rename visible. Confirm it before
        # retiring the independent import guard or deleting the remote source.
        _sync_directory(path.parent)
    return receipts


def _write_custody_receipts(
    local_table: str,
    receipts: dict,
) -> None:
    os.makedirs(local_table, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".custody-receipts-", dir=local_table)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(receipts, destination, sort_keys=True)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, os.path.join(local_table, _CUSTODY_RECEIPTS_FILE))
        _sync_directory(local_table)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
