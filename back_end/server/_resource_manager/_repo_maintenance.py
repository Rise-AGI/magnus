"""Administrative invalidation of one owned repository cache."""

import asyncio
import os
import stat

from .._blocking_io import _run_blocking_io
from ._helpers import _remove_tree


def _inspect_cache(
    path: str,
) -> int | None:
    try:
        root = os.lstat(
            path,
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(
        root.st_mode,
    ) or os.path.isjunction(
        path,
    ):
        raise ValueError(
            "Repository cache root is not an owned directory",
        )
    size = 0
    pending = [path]
    while pending:
        with os.scandir(
            pending.pop(),
        ) as entries:
            for entry in entries:
                info = entry.stat(
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(
                    info.st_mode,
                ) and not os.path.isjunction(
                    entry.path,
                ):
                    pending.append(
                        entry.path,
                    )
                else:
                    size += info.st_size
    return size


def _invalidate_cache(
    path: str,
    dry_run: bool,
) -> dict:
    size = _inspect_cache(
        path,
    )
    if size is None:
        return {"status": "absent", "cache_bytes": 0}
    if dry_run:
        return {"status": "present", "cache_bytes": size}
    _remove_tree(
        path,
    )
    if os.path.lexists(
        path,
    ):
        raise OSError(
            "Repository cache removal did not complete",
        )
    # Logical bytes of removed entries, not a claim about global disk free space.
    return {"status": "removed", "cache_bytes": size}


async def invalidate_cache(
    path: str,
    lock: asyncio.Lock,
    dry_run: bool,
) -> dict:
    try:
        # Match LRU admission: do not overtake a woken waiter or wait on a Job.
        async with asyncio.timeout(
            0,
        ):
            await lock.acquire()
    except TimeoutError:
        return {"status": "busy", "cache_bytes": None}
    try:
        return await _run_blocking_io(
            _invalidate_cache,
            path,
            dry_run,
        )
    finally:
        lock.release()
