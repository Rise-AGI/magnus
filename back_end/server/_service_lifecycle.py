"""Serialize decisions about one Service across async callers and DB workers."""
from contextlib import contextmanager
import threading
import weakref


_registry_guard = threading.Lock()
_operations = weakref.WeakValueDictionary()


@contextmanager
def service_operation(
    service_id: str,
):
    with _registry_guard:
        lock = _operations.get(service_id)
        if lock is None:
            lock = threading.RLock()
            _operations[service_id] = lock
    # Hold a strong reference through the entire operation. The registry does
    # not retain locks for deleted or arbitrary requested Service IDs.
    with lock:
        yield
