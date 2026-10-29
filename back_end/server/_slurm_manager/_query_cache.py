"""Bound read-only scheduler polling independently of execution reconciliation."""

import threading
import time
from collections.abc import Callable, Hashable

from cachetools import TTLCache


class QueryBusy(RuntimeError):
    pass


class SchedulerQueryCache:
    def __init__(
        self,
        *,
        ttl: float = 20,
        maxsize: int = 256,
        max_inflight: int = 4,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache = TTLCache(
            maxsize=maxsize,
            ttl=ttl,
            timer=timer,
        )
        self._lock = threading.Lock()
        self._connections: set[Hashable] = set()
        self._max_inflight = max_inflight

    def get[T](
        self,
        key: Hashable,
        connection: Hashable,
        producer: Callable[[], T],
    ) -> T:
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if connection in self._connections or len(self._connections) >= self._max_inflight:
                raise QueryBusy("A scheduler observation is already in progress")
            self._connections.add(connection)
        try:
            result = producer()
            with self._lock:
                self._cache[key] = result
            return result
        finally:
            with self._lock:
                self._connections.remove(connection)
