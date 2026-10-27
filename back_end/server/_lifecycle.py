"""Application teardown that retains ownership until background work exits."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager


async def cancel_background_tasks(
    tasks: list[asyncio.Task],
) -> None:
    for task in tasks:
        task.cancel()
    await join_background_tasks(tasks)


async def join_background_tasks(
    tasks: list[asyncio.Task],
) -> None:
    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )
    errors = [
        result for result in results
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
    ]
    if errors:
        raise BaseExceptionGroup(
            "Background tasks failed during application lifetime",
            errors,
        )


@asynccontextmanager
async def lifespan_resources():
    resources = AsyncExitStack()
    try:
        yield resources
    finally:
        # AsyncExitStack attempts every callback after a failure; shielding the
        # whole unwind also keeps repeated ASGI cancellation from detaching it.
        closing = asyncio.create_task(resources.aclose())
        interrupted = None
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as error:
                interrupted = error
            except BaseException:
                break
        closing.result()
        if interrupted is not None:
            raise interrupted
