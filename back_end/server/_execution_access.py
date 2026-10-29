"""Submission authority is separate from the site's shared Job visibility."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ._execution_targets import ExecutionTarget
from .models import User


def assert_target_submission_allowed(
    db: Session,
    user_id: str,
    target: ExecutionTarget,
) -> None:
    if target.allowed_users is None:
        return
    visited = set()
    candidate = user_id
    while candidate and candidate not in visited:
        visited.add(candidate)
        user = db.get(
            User,
            candidate,
        )
        if user is None:
            break
        if user.id in target.allowed_users:
            return
        candidate = user.parent_id
    raise HTTPException(
        status_code=403,
        detail=f"You are not authorized to submit to execution target {target.id!r}",
    )
