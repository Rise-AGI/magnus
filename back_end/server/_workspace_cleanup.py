"""Remove terminal Job scratch, including read-only image directories."""
import json
import math
import os
import shutil
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from typing import NamedTuple, Optional, Tuple


class CleanupResult(NamedTuple):
    status: str
    removed: int
    elapsed_seconds: float
    error: str


def parse_cleanup_result(
    payload: str,
) -> CleanupResult:
    if len(payload) > 4096:
        raise ValueError("Cleanup response exceeds protocol limit")
    value = json.loads(payload)
    if not isinstance(value, dict) or set(value) != set(CleanupResult._fields):
        raise ValueError("Invalid cleanup response fields")
    if value["status"] not in ("done", "progress", "busy", "failed"):
        raise ValueError("Invalid cleanup status")
    if type(value["removed"]) is not int or value["removed"] < 0:
        raise ValueError("Invalid cleanup count")
    elapsed = value["elapsed_seconds"]
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Invalid cleanup duration")
    error = value["error"]
    if not isinstance(error, str) or len(error) > 64 or (error and not error.isidentifier()):
        raise ValueError("Invalid cleanup error code")
    if (value["status"] == "done") != (error == ""):
        raise ValueError("Inconsistent cleanup outcome")
    return CleanupResult(**value)


class _CleanupRun:
    def __init__(
        self,
        deadline: Optional[float],
        workers: int,
    ) -> None:
        self.deadline = deadline
        self.slots = threading.BoundedSemaphore(workers - 1)
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers - 1))
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.removed = 0
        self.error: Optional[BaseException] = None

    def check(
        self,
    ) -> None:
        _check_deadline(self.deadline)
        if self.stopped.is_set():
            raise InterruptedError("Cleanup worker stopped")

    def count(
        self,
    ) -> None:
        with self.lock:
            self.removed += 1

    def remove(
        self,
        parent_fd: int,
        name: str,
    ) -> None:
        try:
            _remove_entry(
                parent_fd,
                name,
                self,
            )
        except BaseException as error:
            with self.lock:
                if self.error is None:
                    self.error = error
            self.stopped.set()
            raise

    def parallel_remove(
        self,
        parent_fd: int,
        name: str,
    ) -> None:
        try:
            self.remove(
                parent_fd,
                name,
            )
        finally:
            self.slots.release()


def _remove_children(
    directory_fd: int,
    run: _CleanupRun,
    preserve: Tuple[str, ...],
    preserve_prefixes: Tuple[str, ...],
) -> None:
    pending = []
    try:
        # scandir(fd) needs Python 3.7. Procfs gives 3.6 the same pinned inode
        # without materializing every filename on a large shared filesystem.
        with os.scandir("/proc/self/fd/" + str(directory_fd)) as entries:
            names = (
                entry.name for entry in entries
                if entry.name not in preserve and not entry.name.startswith(preserve_prefixes)
            )
            name = next(names, None)
            while name is not None:
                run.check()
                following = next(names, None)
                for future in pending[:]:
                    if future.done():
                        future.result()
                        pending.remove(future)
                # Single-child spines stay in the caller; siblings share a
                # bounded pool. Parents join before closing their descriptors.
                if following is not None and run.slots.acquire(blocking=False):
                    try:
                        pending.append(run.pool.submit(
                            run.parallel_remove,
                            directory_fd,
                            name,
                        ))
                    except BaseException:
                        run.slots.release()
                        raise
                else:
                    run.remove(
                        directory_fd,
                        name,
                    )
                name = following
    finally:
        for future in pending:
            try:
                future.result()
            except BaseException:
                pass
    if run.error is not None:
        raise run.error


def _check_deadline(
    deadline: Optional[float],
) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("Job workspace cleanup has more entries to remove")


def _remove_entry(
    parent_fd: int,
    name: str,
    run: _CleanupRun,
    *,
    lock: bool = False,
    preserve: Tuple[str, ...] = (),
    preserve_prefixes: Tuple[str, ...] = (),
) -> None:
    run.check()
    try:
        os.unlink(
            name,
            dir_fd=parent_fd,
        )
        run.count()
        return
    except FileNotFoundError:
        return
    except IsADirectoryError:
        pass

    try:
        directory_fd = os.open(
            name,
            os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(
            directory_fd,
        )
        if metadata.st_dev != os.fstat(parent_fd).st_dev:
            raise OSError("Cleanup refuses to cross a filesystem boundary")
        permissions = stat.S_IMODE(metadata.st_mode) | 0o700
        if metadata.st_uid == os.geteuid() and permissions != stat.S_IMODE(metadata.st_mode):
            # O_PATH pins mode-000 directories. Old remote Python cannot combine
            # chmod(dir_fd=...) with no-follow; procfs addresses the pinned inode.
            os.chmod(
                "/proc/self/fd/" + str(directory_fd),
                permissions,
            )
        readable_fd = os.open(
            ".",
            os.O_RDONLY | os.O_DIRECTORY,
            dir_fd=directory_fd,
        )
        try:
            if lock:
                # SSH disconnects need not stop a remote process blocked in I/O.
                # Hold the root inode, so retries cannot start overlapping walks.
                import fcntl

                fcntl.flock(
                    readable_fd,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            _remove_children(
                readable_fd,
                run,
                preserve,
                preserve_prefixes,
            )
            run.check()
            current = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise OSError("Job directory changed during cleanup")
            if preserve or preserve_prefixes:
                with os.scandir("/proc/self/fd/" + str(readable_fd)) as entries:
                    if any(
                        entry.name not in preserve and not entry.name.startswith(preserve_prefixes)
                        for entry in entries
                    ):
                        raise TimeoutError("Job workspace gained entries during cleanup")
            else:
                os.rmdir(
                    name,
                    dir_fd=parent_fd,
                )
                run.count()
        except FileNotFoundError:
            return
        finally:
            os.close(
                readable_fd,
            )
    finally:
        os.close(
            directory_fd,
        )


def _remove_path(
    path: str,
    run: _CleanupRun,
    preserve: Tuple[str, ...],
    preserve_prefixes: Tuple[str, ...],
) -> None:
    if hasattr(os, "O_PATH") and os.path.isdir("/proc/self/fd"):
        absolute = os.path.abspath(
            path,
        )
        try:
            parent_fd = os.open(
                os.path.dirname(absolute),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except FileNotFoundError:
            return
        try:
            # Delete as we traverse; a whole-tree permission prepass can consume
            # every retry's budget without reclaiming anything on a shared disk.
            _remove_entry(
                parent_fd,
                os.path.basename(absolute),
                run,
                lock=True,
                preserve=preserve,
                preserve_prefixes=preserve_prefixes,
            )
        finally:
            os.close(
                parent_fd,
            )
        return
    run.check()
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(
            path,
        )
        run.count()
        return
    if preserve or preserve_prefixes:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name not in preserve and not entry.name.startswith(preserve_prefixes):
                    _remove_path(
                        entry.path,
                        run,
                        (),
                        (),
                    )
    else:
        # Keep the platform's safe rmtree implementation outside Linux.
        shutil.rmtree(path)
        run.count()


def _run_cleanup(
    path: str,
    timeout_seconds: Optional[float],
    workers: int,
    preserve: Tuple[str, ...],
    preserve_prefixes: Tuple[str, ...],
) -> Tuple[_CleanupRun, float]:
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("Cleanup workers must be between 1 and 8")
    if timeout_seconds is not None and (
        type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 3600
    ):
        raise ValueError("Cleanup timeout must be positive and at most 3600 seconds")
    began = monotonic()
    run = _CleanupRun(
        None if timeout_seconds is None else began + timeout_seconds,
        workers,
    )
    try:
        _remove_path(
            path,
            run,
            preserve,
            preserve_prefixes,
        )
    except Exception as error:
        if run.error is None:
            run.error = error
    finally:
        run.stopped.set()
        run.pool.shutdown(wait=True)
    return run, monotonic() - began


def remove_job_path(
    path: str,
    *,
    timeout_seconds: Optional[float] = None,
) -> None:
    run, _ = _run_cleanup(
        path,
        timeout_seconds,
        1,
        (),
        (),
    )
    if run.error is not None:
        raise run.error


def cleanup_job_path(
    path: str,
    *,
    timeout_seconds: float = 20,
    workers: int = 4,
    preserve: Tuple[str, ...] = (),
    preserve_prefixes: Tuple[str, ...] = (),
) -> CleanupResult:
    run, elapsed = _run_cleanup(
        path,
        timeout_seconds,
        workers,
        preserve,
        preserve_prefixes,
    )
    error = run.error
    if error is None:
        status = "done"
    elif isinstance(error, TimeoutError):
        status = "progress"
    elif isinstance(error, BlockingIOError):
        status = "busy"
    else:
        status = "failed"
    return CleanupResult(
        status,
        run.removed,
        round(elapsed, 3),
        "" if error is None else type(error).__name__,
    )
