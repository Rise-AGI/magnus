"""Small, backend-owned preparation snapshots survive workspace cleanup."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import shutil
import time
import uuid


logger = logging.getLogger(__name__)
LIMIT = 4096
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_INSTANCE = uuid.uuid4().hex


def _path(
    root: str,
    job_id: str,
    *,
    create: bool = False,
) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid staging diagnostic identifier")
    directory = Path(root) / "staging_diagnostics"
    if directory.is_symlink():
        raise ValueError("Linked staging diagnostic directory")
    if create:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = directory / job_id
    if directory.is_symlink():
        raise ValueError("Linked staging diagnostic directory")
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    return directory / "progress.json"


def read_staging(
    root: str,
    job_id: str,
) -> dict | None:
    try:
        path = _path(root, job_id)
        if path.is_symlink():
            raise ValueError("Linked staging diagnostic file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("Oversized staging diagnostic")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Invalid staging diagnostic")
        for name in ("completed_operations", "completed_bytes"):
            value = data.setdefault(name, 0)
            if type(value) is not int or value < 0:
                raise ValueError("Invalid staging counter")
        elapsed = data.get("elapsed_seconds")
        if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError("Invalid staging duration")
        if data.get("phase") is not None and not re.fullmatch(r"[a-z_]{1,64}", str(data["phase"])):
            raise ValueError("Invalid staging phase")
        if data.get("error") is not None and not re.fullmatch(r"[a-z_]{1,64}", str(data["error"])):
            raise ValueError("Invalid staging error")
        if data.get("state") not in {"starting", "running", "completed", "failed", "interrupted", "ready"}:
            raise ValueError("Invalid staging state")
        if data.get("state") in {"running", "starting", "completed"}:
            if data.get("instance") != _INSTANCE:
                data.update(state="interrupted", error="controller_restarted")
            elif data.get("state") != "completed" and data.get("started_at"):
                data["elapsed_seconds"] = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(data["started_at"])).total_seconds())
        return data
    except FileNotFoundError:
        return None


def staging_message(
    data: dict,
) -> str:
    phase = data.get("phase", "preparation")
    state = data.get("state", "pending")
    details = [f"Preparation {state}: {phase}"]
    if data.get("index") is not None:
        details.append(f"object {data['index']}/{data['total']}")
    if data.get("expected_bytes") is not None:
        details.append(f"{data['expected_bytes']} bytes")
    if data.get("elapsed_seconds") is not None:
        details.append(f"{data['elapsed_seconds']:.1f}s")
    details.append(f"{data.get('completed_operations', 0)} operations completed")
    if data.get("error"):
        details.append(data["error"])
    return "; ".join(details)[:1024]


class StagingProgress:
    def __init__(
        self,
        root: str,
        job_id: str,
        *,
        restart: bool = False,
    ):
        self.root = root
        self.job_id = job_id
        try:
            self.data = None if restart else read_staging(root, job_id)
        except (OSError, ValueError):
            self.data = None
        if self.data is None:
            self.data = {"version": 1, "attempt": uuid.uuid4().hex, "completed_operations": 0, "completed_bytes": 0}
        self.data["instance"] = _INSTANCE

    def save(
        self,
    ) -> None:
        try:
            self._save()
        except (OSError, ValueError):
            logger.warning("Cannot persist staging diagnostics for Job %s", self.job_id)

    def _save(
        self,
    ) -> None:
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = _path(self.root, self.job_id, create=True)
        temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
        raw = json.dumps(self.data, allow_nan=False).encode()
        if len(raw) > LIMIT:
            raise ValueError("Staging diagnostic exceeded its bound")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def state(
        self,
        state: str,
    ) -> None:
        self.data["state"] = state
        self.save()

    @contextmanager
    def operation(
        self,
        phase: str,
        *,
        size: int | None = None,
        index: int | None = None,
        total: int | None = None,
        budget: float | None = None,
        count_bytes: bool = True,
    ):
        started = time.monotonic()
        self.data.update(
            phase=phase,
            state="running",
            expected_bytes=size,
            index=index,
            total=total,
            budget_seconds=budget,
            started_at=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=0,
            error=None,
        )
        self.save()
        try:
            yield
        except BaseException as error:
            self.data["state"] = "interrupted" if isinstance(error, InterruptedError) else "failed"
            self.data["error"] = (
                "transfer_timeout" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired))
                else "cancelled" if isinstance(error, InterruptedError)
                else "transfer_error"
            )
            raise
        else:
            self.data["completed_operations"] += 1
            self.data["completed_bytes"] += (size or 0) if count_bytes else 0
            self.data["state"] = "completed"
            self.data["last_completed_at"] = datetime.now(timezone.utc).isoformat()
        finally:
            self.data["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.save()


    def call(
        self,
        phase,
        function,
        *arguments,
        size=None,
        index=None,
        total=None,
        budget=None,
        require_success=False,
        count_bytes=True,
        **keywords,
    ):
        with self.operation(
            phase,
            size=size,
            index=index,
            total=total,
            budget=budget,
            count_bytes=count_bytes,
        ):
            result = function(*arguments, **keywords)
            if require_success and result.returncode:
                raise RuntimeError("Remote preparation command failed")
            return result


def finish_staging(
    root,
    job_id,
    *,
    failed=False,
):
    progress = StagingProgress(root, job_id)
    if failed:
        progress.data.setdefault("phase", "preparation")
        progress.data["state"] = "failed"
        progress.data["error"] = progress.data.get("error") or "preparation_error"
        progress.save()
        return staging_message(progress.data)
    progress.state("ready")
    return None


def prune_staging(
    root,
    active_ids,
    is_active,
    *,
    retained=4096,
):
    directory = Path(root) / "staging_diagnostics"
    if not directory.is_dir() or directory.is_symlink():
        return
    entries = []
    for child in directory.iterdir():
        if _JOB_ID.fullmatch(child.name) and child.name not in active_ids and child.is_dir() and not child.is_symlink():
            try:
                entries.append((child.stat().st_mtime, child))
            except FileNotFoundError:
                continue
    entries.sort(key=lambda entry: entry[0])
    for _, child in entries[:max(0, len(entries) - retained)]:
        if not is_active(child.name):
            shutil.rmtree(child)
