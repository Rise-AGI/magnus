"""Event-loop-owned admission for distinct image preparations."""

import asyncio
from collections import OrderedDict, deque


class BuildAdmission:
    def __init__(
        self,
        capacity: int,
        per_user: int,
    ) -> None:
        if not 1 <= per_user <= capacity:
            raise ValueError("Build concurrency must satisfy 1 <= per_user <= capacity")
        self.capacity = capacity
        self.per_user = per_user
        self._active: dict[str | None, int] = {}
        self._used = 0
        self._waiting: OrderedDict[str | None, deque[asyncio.Future[None]]] = OrderedDict()

    async def acquire(
        self,
        user_id: str | None,
    ) -> None:
        waiter = asyncio.get_running_loop().create_future()
        self._waiting.setdefault(
            user_id,
            deque(),
        ).append(waiter)
        self._dispatch()
        try:
            await waiter
        except BaseException:
            # Cancellation can arrive after a grant but before its task resumes.
            if waiter.done() and not waiter.cancelled():
                self.release(
                    user_id,
                )
            else:
                waiter.cancel()
                pending = self._waiting.get(user_id)
                if pending is not None and waiter in pending:
                    pending.remove(waiter)
                    if not pending:
                        del self._waiting[user_id]
                self._dispatch()
            raise

    def release(
        self,
        user_id: str | None,
    ) -> None:
        count = self._active[user_id]
        if count == 1:
            del self._active[user_id]
        else:
            self._active[user_id] = count - 1
        self._used -= 1
        self._dispatch()

    def _dispatch(
        self,
    ) -> None:
        while self._used < self.capacity and self._waiting:
            for _ in range(len(self._waiting)):
                user_id, pending = self._waiting.popitem(last=False)
                while pending and pending[0].cancelled():
                    pending.popleft()
                if not pending:
                    continue
                eligible = (
                    self._active.get(
                        user_id,
                        0,
                    )
                    < self.per_user
                )
                waiter = pending.popleft() if eligible else None
                if pending:
                    self._waiting[user_id] = pending
                if waiter is not None:
                    self._active[user_id] = (
                        self._active.get(
                            user_id,
                            0,
                        )
                        + 1
                    )
                    self._used += 1
                    waiter.set_result(None)
                    break
            else:
                break
