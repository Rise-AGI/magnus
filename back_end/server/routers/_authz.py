# back_end/server/routers/_authz.py
"""Resource authorization helpers shared across resource routers.

The Magnus permission model is a three-way OR:
    is_admin OR is_owner OR is_ancestor (recursive supervisor)

This module centralizes that check so that adding new roles (e.g. team admin)
only requires editing one place. User-management permissions
(``users.py:_can_manage``) intentionally stay separate — those are about
managing user records, not resources.
"""
from typing import Optional, Set

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from .._magnus_config import is_admin_user
from .users import _is_ancestor


def assert_can_manage_resource(
    db: Session,
    current_user: models.User,
    owner_id: str,
    resource_label: str = "resource",
    *,
    hint: Optional[str] = None,
) -> None:
    """Raise HTTP 403 unless ``current_user`` may manage a resource owned by ``owner_id``.

    Used by delete / overwrite / transfer-source / mutating endpoints that need
    to abort with a 403 when the caller lacks permission.

    The 403 detail is::

        f"You cannot modify a {resource_label} created by another user."

    optionally followed by ``" {hint}"`` when ``hint`` is supplied.
    """
    if is_admin_user(current_user) or current_user.id == owner_id:
        return
    if _is_ancestor(db, current_user.id, owner_id):
        return

    detail = f"You cannot modify a {resource_label} created by another user."
    if hint:
        detail = f"{detail} {hint}"
    raise HTTPException(status_code=403, detail=detail)


def assert_valid_transfer_target(
    db: Session,
    current_user: models.User,
    new_owner_id: str,
) -> models.User:
    """Validate a transfer target user.

    Returns the target ``User`` object on success (callers usually want it
    anyway, so we save them an extra query).

    Priority of failures (do not change order — endpoints rely on it):

    * target user does not exist → ``HTTPException(404, "Target user not found")``
    * caller is not admin and target is neither self nor a subordinate →
      ``HTTPException(403, "Target must be yourself or your subordinate")``
    """
    target = db.query(models.User).filter(models.User.id == new_owner_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found")
    if is_admin_user(current_user):
        return target
    if new_owner_id == current_user.id:
        return target
    if _is_ancestor(db, current_user.id, new_owner_id):
        return target
    raise HTTPException(
        status_code=403,
        detail="Target must be yourself or your subordinate",
    )


def compute_can_manage(
    db: Session,
    current_user: models.User,
    owner_id: str,
    *,
    subordinate_ids: Optional[Set[str]] = None,
) -> bool:
    """Return whether ``current_user`` can manage a resource owned by ``owner_id``.

    Two calling modes:

    * **Batch (list endpoints):** caller pre-computes
      ``subordinate_ids = set(_get_all_subordinate_ids(db, current_user.id))``
      once and passes it in. The ancestor check then becomes O(1) ``in``.
    * **Single (detail endpoints):** caller omits ``subordinate_ids`` and the
      helper walks the parent chain with one ``_is_ancestor`` query.
    """
    if is_admin_user(current_user) or current_user.id == owner_id:
        return True
    if subordinate_ids is not None:
        return owner_id in subordinate_ids
    return _is_ancestor(db, current_user.id, owner_id)
