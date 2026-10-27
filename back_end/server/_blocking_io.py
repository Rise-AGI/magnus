"""Cancellation-safe worker ownership for database and filesystem operations."""
import asyncio
from collections.abc import Callable


async def _run_blocking_io(
    function: Callable,
    *arguments,
    **keywords,
):
    """Keep filesystem workers joined before a cancelled caller releases ownership.

    Cancelling an asyncio waiter cannot stop its thread. Shield the operation,
    then finish joining it despite repeated cancellation before propagating the
    original cancellation. Cleanup may only remove its destination afterwards.
    """
    task = asyncio.create_task(asyncio.to_thread(
        function,
        *arguments,
        **keywords,
    ))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
