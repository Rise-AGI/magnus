"""Keep user deletion and new references in the same database write order."""
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import User


def lock_user_for_write(
    db: Session,
    user_id: str,
) -> User:
    # SQLite ignores SELECT FOR UPDATE. A no-op write reserves its writer until
    # the caller commits the dependent record or deletion, without a migration.
    result = db.execute(
        update(User).where(User.id == user_id).values(id=User.id),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="User not found")
    return db.query(User).filter(User.id == user_id).populate_existing().one()


def assert_user_unreferenced(
    db: Session,
    user_id: str,
) -> None:
    """Call after lock_user_for_write and before deleting the user."""
    for table in User.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            if foreign_key.column is not User.__table__.c.id:
                continue
            if foreign_key.ondelete in {"CASCADE", "SET NULL"}:
                # Credentials are deleted or revoked by the declared action;
                # historical resources and subordinates must block deletion.
                continue
            reference = foreign_key.parent
            if db.execute(
                select(reference).where(reference == user_id).limit(1),
            ).first() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot delete user with existing records or subordinates",
                )
