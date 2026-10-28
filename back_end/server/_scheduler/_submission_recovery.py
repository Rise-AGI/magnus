"""Durable submission intent and conservative recovery of lost Slurm replies."""
from datetime import datetime, timedelta, timezone
import getpass
import json
import socket
import threading
from time import monotonic
import uuid
from typing import TYPE_CHECKING

from ..database import SessionLocal
from ..models import Job, JobStatus
from .._slurm_manager._submission import find_submissions
from . import logger


_BACKOFF_LOCK = threading.Lock()
_BACKOFF: dict[tuple[str, ...], float] = {}


def new_submission_attempt(
    job_id: str,
) -> str:
    return json.dumps({
        "comment": f"magnus:{job_id}:{uuid.uuid4().hex}",
        "at": datetime.now(timezone.utc).isoformat(),
    })


def submission_quota_key(
    config: dict,
) -> tuple[str, ...]:
    transport = config.get("transport", {})
    ssh = transport.get("ssh", {}) if transport.get("mode") == "ssh" else {}
    return (
        ssh.get("host") or socket.gethostname(),
        ssh.get("user") or getpass.getuser(),
        config["execution"]["slurm"].get("account") or "",
    )


if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _SubmissionRecoveryMixinBase = _SchedulerProtocol
else:
    _SubmissionRecoveryMixinBase = object


class _SubmissionRecoveryMixin(_SubmissionRecoveryMixinBase):
    def _submission_deferred(
        self,
    ) -> bool:
        key = submission_quota_key(self.config)
        with _BACKOFF_LOCK:
            deadline = _BACKOFF.get(key, 0)
            if deadline <= monotonic():
                _BACKOFF.pop(key, None)
                return False
            return True

    def _defer_submission(
        self,
    ) -> None:
        with _BACKOFF_LOCK:
            _BACKOFF[submission_quota_key(self.config)] = monotonic() + 60

    def _begin_submission(
        self,
        db,
        job,
        attempt: str,
    ) -> None:
        changed = self._job_query(db, Job).filter(
            Job.id == job.id,
            Job.status == JobStatus.PENDING,
            Job.slurm_job_id.is_(None),
            Job.submission_attempt.is_(None),
        ).update({Job.submission_attempt: attempt}, synchronize_session=False)
        db.commit()
        db.refresh(job)
        if not changed:
            raise InterruptedError("Job no longer eligible for submission")

    def _clear_rejected_submission(
        self,
        db,
        job_id: str,
        attempt: str,
    ) -> None:
        db.rollback()
        self._job_query(db, Job).filter(
            Job.id == job_id,
            Job.submission_attempt == attempt,
            Job.slurm_job_id.is_(None),
        ).update({Job.submission_attempt: None}, synchronize_session=False)
        db.commit()

    def _reconcile_submissions(
        self,
    ) -> None:
        if monotonic() < self._submission_reconcile_after:
            return
        with SessionLocal() as db:
            rows = self._job_query(db, Job.id, Job.submission_attempt).filter(
                Job.submission_attempt.isnot(None),
            ).all()
        with self._preparing_jobs_lock:
            rows = [(job_id, attempt) for job_id, attempt in rows if job_id not in self._submitting_jobs]
        if not rows:
            return
        self._submission_reconcile_after = monotonic() + 60
        grouped = {}
        for job_id, encoded in rows:
            value = json.loads(encoded)
            bucket = int(datetime.fromisoformat(value["at"]).timestamp()) // 600
            grouped.setdefault(bucket, {})[job_id] = (encoded, value)
        # One old unknown must not turn each retry into an ever-growing scan
        # of cluster accounting. Rotate fixed windows without assuming absence.
        buckets = sorted(grouped)
        cursor = self._submission_reconcile_cursor % len(buckets)
        bucket = buckets[cursor]
        self._submission_reconcile_cursor = cursor + 1
        attempts = grouped[bucket]
        since = datetime.fromtimestamp(bucket * 600, timezone.utc) - timedelta(minutes=5)
        until = since + timedelta(minutes=20)
        found = find_submissions(
            self.slurm_manager.transport,
            {value["comment"] for _, value in attempts.values()},
            since.strftime("%Y-%m-%dT%H:%M:%S"),
            until.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        for job_id, (encoded, value) in attempts.items():
            external_id = found.get(value['comment'])
            if external_id is None:
                continue
            with SessionLocal() as db:
                job = self._job_query(db, Job).filter(
                    Job.id == job_id,
                    Job.submission_attempt == encoded,
                ).first()
                if job is not None:
                    self._record_submission(db, job, external_id, attempt=encoded)
                    logger.info("Recovered Slurm submission for Job %s", job_id)
