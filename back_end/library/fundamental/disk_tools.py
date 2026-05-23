# back_end/library/fundamental/disk_tools.py
"""Shared disk-space awareness.

The platform tracks configured quotas (container cache size, repo cache size,
file-custody max size) but never the real free space of the underlying volume.
When that volume fills from any source, every write path -- image pull, repo
clone, ephemeral overlay, custody write -- hit a raw ENOSPC and surfaced it as
an opaque crash (and image pull additionally retried it as if transient). These
helpers give every path one consistent way to detect an out-of-space condition
and report it in human terms.
"""
import errno
import shutil

__all__ = [
    "is_disk_full_stderr",
    "is_disk_full_oserror",
    "disk_free_bytes",
    "disk_full_message",
]

# Substrings emitted on a full or quota-capped filesystem by the tools we shell
# out to (apptainer pull / overlay create, git clone) and by libc-level writes.
_DISK_FULL_MARKERS = (
    "no space left on device",
    "disk quota exceeded",
)

# OSError errnos meaning "the write could not fit": ENOSPC (no space) and, where
# the platform defines it, EDQUOT (quota exceeded).
_DISK_FULL_ERRNOS = {errno.ENOSPC}
if hasattr(errno, "EDQUOT"):
    _DISK_FULL_ERRNOS.add(errno.EDQUOT)


def is_disk_full_stderr(stderr: str) -> bool:
    """True if subprocess stderr indicates an out-of-space failure."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _DISK_FULL_MARKERS)


def is_disk_full_oserror(error: OSError) -> bool:
    """True if an OSError is an out-of-space failure (ENOSPC / EDQUOT)."""
    return error.errno in _DISK_FULL_ERRNOS


def disk_free_bytes(path: str) -> int:
    """Free bytes on the filesystem backing path (0 if it cannot be queried)."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def disk_full_message(path: str) -> str:
    """Human-facing out-of-space message naming the affected volume."""
    free_mib = disk_free_bytes(path) // (1024 * 1024)
    return (
        f"insufficient disk space on the volume holding {path} "
        f"({free_mib} MiB free); free up space or expand storage"
    )
