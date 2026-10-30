"""Remove terminal Job scratch, including read-only image directories."""
import os
import shutil
import stat
from time import monotonic
from typing import Optional


def _check_deadline(
    deadline: Optional[float],
) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("Job workspace cleanup has more entries to remove")


def _remove_entry(
    parent_fd: int,
    name: str,
    deadline: Optional[float],
    *,
    lock: bool = False,
) -> None:
    _check_deadline(
        deadline,
    )
    try:
        os.unlink(
            name,
            dir_fd=parent_fd,
        )
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
            for child in os.listdir(
                readable_fd,
            ):
                _remove_entry(
                    readable_fd,
                    child,
                    deadline,
                )
            _check_deadline(
                deadline,
            )
            current = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise OSError("Job directory changed during cleanup")
            os.rmdir(
                name,
                dir_fd=parent_fd,
            )
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


def remove_job_path(
    path: str,
    *,
    timeout_seconds: Optional[float] = None,
) -> None:
    deadline = None if timeout_seconds is None else monotonic() + timeout_seconds
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
                deadline,
                lock=True,
            )
        finally:
            os.close(
                parent_fd,
            )
        return
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(
            path,
        )
        return
    shutil.rmtree(
        path,
    )
