"""Retry short database-only transactions without retaining a failed session."""

import logging
import random
import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)
T = TypeVar("T")


def is_sqlite_busy(
    error: BaseException,
) -> bool:
    # Extended codes include BUSY_SNAPSHOT. LOCKED is a different error and
    # may indicate misuse of a connection; do not classify by error text.
    original = error.orig if isinstance(error, OperationalError) else error
    return (
        isinstance(original, sqlite3.OperationalError)
        and getattr(original, "sqlite_errorcode", 0) & 0xFF == sqlite3.SQLITE_BUSY
    )


def write_transaction(
    sessions: Callable[[], Session],
    action: Callable[[Session], T],
    *,
    operation: str,
) -> T:
    """Run database-only work, returning detached values after commit.

    Each attempt uses the existing connection/pool timeouts. Three attempts
    bound retries; no connection settings or pool-wide timeouts are changed.
    The action must not commit, perform external I/O, or return ORM instances.
    """
    began = time.monotonic()
    for attempt in range(1, 4):
        try:
            with sessions() as db:
                try:
                    result = action(db)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            return result
        except OperationalError as error:
            if not is_sqlite_busy(error):
                raise
            logger.warning(
                "SQLite busy operation=%s attempt=%d/3 elapsed_seconds=%.3f code=%s",
                operation,
                attempt,
                time.monotonic() - began,
                getattr(error.orig, "sqlite_errorname", "SQLITE_BUSY"),
            )
            if attempt == 3:
                raise
            # Release the session before backoff so another writer can finish.
            time.sleep(random.uniform(0.05, 0.15) * attempt)
    raise AssertionError("Unreachable transaction retry state")
