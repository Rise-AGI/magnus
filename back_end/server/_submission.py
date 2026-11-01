"""Attach retry identity and Blueprint preference writes to Job acceptance."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from ._blueprint_signature import code_revision
from ._user_lifecycle import lock_user_for_write


@dataclass(
    frozen=True,
)
class BlueprintRun:
    identifier: str
    revision: str
    signature_hash: str
    cached_parameters: str | None = None


@dataclass(
    frozen=True,
)
class SubmissionContext:
    request_key: str | None
    request_hash: str
    blueprint: BlueprintRun | None = None

    @classmethod
    def from_request(
        cls,
        key: str | None,
        operation: str,
        request: dict[str, Any],
        default_target: str | None,
    ) -> "SubmissionContext":
        if key is not None and not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}",
            key,
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid Idempotency-Key",
            )
        try:
            payload = json.dumps(
                [operation, default_target, request],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (ValueError, TypeError, RecursionError) as error:
            raise HTTPException(
                status_code=400,
                detail="Invalid submission parameters",
            ) from error
        return cls(
            key,
            hashlib.sha256(
                payload.encode(
                    "utf-8",
                ),
            ).hexdigest(),
        )

    def replay(
        self,
        db: Session,
        user_id: str,
    ) -> models.Job | None:
        if self.request_key is None:
            return None
        record = (
            db.query(
                models.JobAcceptance,
            )
            .filter(
                models.JobAcceptance.user_id == user_id,
                models.JobAcceptance.request_key == self.request_key,
            )
            .first()
        )
        if record is None:
            return None
        if record.request_hash != self.request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different request",
            )
        job = db.get(
            models.Job,
            record.job_id,
        )
        if job is None:
            raise HTTPException(
                status_code=409,
                detail="The accepted Job is no longer available",
            )
        return job

    def replay_after_failure(
        self,
        db: Session,
        user_id: str,
    ) -> models.Job | None:
        """A failed precheck must not hide an already committed acceptance."""
        if self.request_key is None:
            return None
        db.rollback()
        try:
            lock_user_for_write(
                db,
                user_id,
            )
            existing = self.replay(
                db,
                user_id,
            )
            db.commit()
            return existing
        except BaseException:
            db.rollback()
            raise

    def attach(
        self,
        db: Session,
        job: models.Job,
    ) -> None:
        """Caller holds the user writer lock; commit Job and these writes together."""
        source = self.blueprint
        if source is not None:
            current = (
                db.query(
                    models.Blueprint.code,
                )
                .filter(
                    models.Blueprint.id == source.identifier,
                )
                .first()
            )
            if (
                current is None
                or code_revision(
                    current[0],
                )
                != source.revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Blueprint changed while preparing the submission; reload and retry",
                )
            if source.cached_parameters is not None:
                save_preference(
                    db,
                    job.user_id,
                    source.identifier,
                    source.signature_hash,
                    source.cached_parameters,
                )
        if self.request_key is not None or source is not None:
            db.flush()
            db.add(
                models.JobAcceptance(
                    job_id=job.id,
                    user_id=job.user_id,
                    request_key=self.request_key,
                    request_hash=self.request_hash,
                    blueprint_id=source.identifier if source else None,
                    blueprint_revision=source.revision if source else None,
                ),
            )


def save_preference(
    db: Session,
    user_id: str,
    blueprint_id: str,
    schema_hash: str,
    cached_parameters: str,
) -> models.BlueprintUserPreference:
    """Read/update only after the common writer lock, including initial insert."""
    preference = (
        db.query(
            models.BlueprintUserPreference,
        )
        .filter(
            models.BlueprintUserPreference.user_id == user_id,
            models.BlueprintUserPreference.blueprint_id == blueprint_id,
        )
        .populate_existing()
        .first()
    )
    if preference is None:
        preference = models.BlueprintUserPreference(
            user_id=user_id,
            blueprint_id=blueprint_id,
        )
        db.add(
            preference,
        )
    preference.blueprint_hash = schema_hash
    preference.cached_params = cached_parameters
    preference.updated_at = datetime.now(
        timezone.utc,
    )
    return preference
