"""Bounded native-build SIF cache on an SSH execution host."""
from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path
import re
import threading

from sqlalchemy import or_

from .._execution_targets import ExecutionTarget
from .._magnus_config import configured_execution_targets
from .._size_utils import _parse_size_string
from .._slurm_manager._transport import _Transport, check_transfer_cancelled
from ..database import SessionLocal
from ..models import Job, JobStatus


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
# Scheduler submission runs in worker threads. Keep the DB protection snapshot,
# remote reclamation and entire transfer in one critical section.
_STAGING_LOCKS: dict[tuple[str, ...], threading.Lock] = {}
_STAGING_LOCKS_GUARD = threading.Lock()
_VERIFIED_IMAGES: dict[tuple[str, ...], OrderedDict[str, list[int]]] = {}
_VERIFIED_LIMIT = 256
_RECEIVER_PATH = Path(__file__).with_name("_relay_build_receiver.py")


def remote_built_sif_path(
    digest: str,
    *,
    cache_path: str,
) -> str:
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("Invalid built image digest")
    return f"{cache_path}/native_builds/{digest.split(':')[1]}.sif"


def stage_built_sif(
    transport: _Transport,
    local_path: str,
    digest: str,
    *,
    job_id: str,
    target: ExecutionTarget,
    cancel_event: threading.Event | None = None,
) -> None:
    remote_path = remote_built_sif_path(
        digest,
        cache_path=f"{target.remote_root}/container_cache",
    )
    config = target.config["execution"]["image_build"]
    timeout = int(config["timeout_seconds"])
    cache_key = target.image_cache_key
    sharing_targets = [
        candidate
        for candidate in configured_execution_targets.values()
        if candidate.image_cache_key == cache_key
    ]
    with _STAGING_LOCKS_GUARD:
        staging_lock = _STAGING_LOCKS.setdefault(cache_key, threading.Lock())
        verified = _VERIFIED_IMAGES.setdefault(cache_key, OrderedDict())
    check_transfer_cancelled(cancel_event)
    while not staging_lock.acquire(timeout=0.1):
        check_transfer_cancelled(cancel_event)
    try:
        check_transfer_cancelled(cancel_event)
        with SessionLocal() as db:
            records = db.query(Job.id, Job.build_result, Job.slurm_job_id).filter(
                or_(*(candidate.job_filter(Job) for candidate in sharing_targets)),
                Job.build_result.isnot(None),
                or_(
                    Job.status.notin_([JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TERMINATED]),
                    Job.slurm_job_id.isnot(None),
                    Job.submission_attempt.isnot(None),
                ),
            ).all()
        protected = set()
        for active_job_id, value, external_id in records:
            # This submission has not consumed the remote SIF yet. It must not
            # prevent replacement of its own damaged cache entry; every other
            # pending/active Job and any external execution still protects it.
            if active_job_id == job_id and external_id is None:
                continue
            result = json.loads(value)
            active_digest = result.get("digest")
            if not isinstance(active_digest, str) or not _DIGEST.fullmatch(active_digest):
                raise ValueError("Active Job has an invalid built image digest")
            protected.add(active_digest)
        size = Path(local_path).stat().st_size
        metadata = json.dumps({
            "digest": digest,
            "size": size,
            "budget": _parse_size_string(config["cache_size"]),
            "free_floor": _parse_size_string(config["min_free_space"]),
            "protected": sorted(protected),
            # Trust lives only in this controller process. Restart or any
            # remote inode/size/time change requires full content verification.
            "verified_signature": verified.pop(digest, None),
        }).encode("utf-8") + b"\n"
        if len(metadata) > 4 * 1024 * 1024:
            raise ValueError("Remote image protection metadata exceeds 4 MiB")
        result = transport.run_with_file_input(
            [
                "python3",
                "-c",
                _RECEIVER_PATH.read_text(encoding="utf-8"),
                remote_path.rsplit("/", 1)[0],
                str(timeout),
            ],
            local_path,
            prefix=metadata,
            timeout=timeout + 10,
            cancel_event=cancel_event,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Remote built image preparation failed: {result.stderr.strip()[:4096]}")
        if len(result.stdout) > 4096:
            raise ValueError("Oversized remote image receipt")
        receipt = json.loads(result.stdout)
        signature = receipt.get("signature")
        if (
            not isinstance(signature, list)
            or len(signature) != 5
            or any(type(value) is not int for value in signature)
            or signature[2] != size
        ):
            raise ValueError("Invalid remote image verification receipt")
        verified[digest] = signature
        while len(verified) > _VERIFIED_LIMIT:
            verified.popitem(last=False)
    finally:
        staging_lock.release()
