"""Resolve imported trust tokens without retaining their plaintext values."""
import hashlib
import hmac

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from .models import User, UserTokenAlias, ScopedUserTokenAlias


def token_digest(
    token: str,
) -> str:
    return hashlib.sha256(
        token.encode("utf-8"),
    ).hexdigest()


def find_user_by_token(
    db: Session,
    token: str,
    source_target: str | None = None,
) -> User | None:
    if source_target is not None:
        scoped = db.get(
            ScopedUserTokenAlias,
            (source_target, token_digest(token)),
        )
        if scoped is not None:
            # A revoked collision remains a tombstone: falling through would
            # silently authenticate the unrelated account on the new endpoint.
            return _user_for_alias(
                db,
                scoped,
            )
    user = db.query(User).filter(User.token == token).first()
    if user is not None:
        return user
    alias = db.get(
        UserTokenAlias,
        token_digest(token),
    )
    if alias is None:
        return None
    return _user_for_alias(
        db,
        alias,
    )


def _user_for_alias(
    db: Session,
    alias: UserTokenAlias | ScopedUserTokenAlias,
) -> User | None:
    if alias.user_id is None:
        return None
    user = db.get(
        User,
        alias.user_id,
    )
    # Rotation revokes every imported token, even across a restart. Never
    # cache alias authentication independently of this primary-token check.
    if user is None or not user.token or not hmac.compare_digest(
        alias.primary_digest,
        token_digest(user.token),
    ):
        return None
    return user


def replace_primary_token(
    db: Session,
    user: User,
    token: str,
) -> None:
    if user.token != token:
        db.execute(
            delete(UserTokenAlias).where(UserTokenAlias.user_id == user.id),
        )
        db.execute(
            update(ScopedUserTokenAlias)
            .where(ScopedUserTokenAlias.user_id == user.id)
            .values(primary_digest=""),
        )
    user.token = token
