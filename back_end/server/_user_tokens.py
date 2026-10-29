"""Resolve imported trust tokens without retaining their plaintext values."""
import hashlib
import hmac

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import User, UserTokenAlias


def token_digest(
    token: str,
) -> str:
    return hashlib.sha256(
        token.encode("utf-8"),
    ).hexdigest()


def find_user_by_token(
    db: Session,
    token: str,
) -> User | None:
    user = db.query(User).filter(User.token == token).first()
    if user is not None:
        return user
    alias = db.get(
        UserTokenAlias,
        token_digest(token),
    )
    if alias is None:
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
    user.token = token
