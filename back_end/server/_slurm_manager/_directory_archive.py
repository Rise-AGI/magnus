"""Portable, cancellable directory packaging for SSH staging."""
import hashlib
import os
from pathlib import Path
import stat
import tarfile
import threading
import time


def pack_directory(
    source: str,
    destination: str,
    *,
    deadline: float,
    cancel_event: threading.Event | None,
) -> dict:
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Job staging cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("Directory packaging exceeded its time budget")

    class Reader:
        def __init__(
            self,
            stream,
        ):
            self.stream = stream

        def read(
            self,
            size,
        ):
            check()
            return self.stream.read(size)

    def fail_walk(error):
        raise error

    root = Path(source)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Directory staging requires a real source directory")
    unpacked = 0
    with tarfile.open(destination, "w:gz", compresslevel=1, dereference=False) as archive:
        def add(
            path,
        ):
            nonlocal unpacked
            check()
            name = path.relative_to(root).as_posix()
            information = archive.gettarinfo(str(path), arcname=name)
            if information.isfile() or information.islnk():
                # Materialize hard links as regular files, as SCP did, without
                # dereferencing symbolic links or depending on remote link order.
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    actual = os.fstat(stream.fileno())
                    expected = path.lstat()
                    if not stat.S_ISREG(actual.st_mode) or (
                        actual.st_dev, actual.st_ino, actual.st_size,
                    ) != (expected.st_dev, expected.st_ino, expected.st_size):
                        raise ValueError("Source changed while packaging directory")
                    information.type = tarfile.REGTYPE
                    information.linkname = ""
                    information.size = actual.st_size
                    archive.addfile(information, Reader(stream))
                    final = os.fstat(stream.fileno())
                    if (final.st_size, final.st_mtime_ns, final.st_ctime_ns) != (
                        actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns,
                    ):
                        raise ValueError("Source changed while packaging directory")
                    unpacked += information.size
            elif information.isdir() or information.issym():
                archive.addfile(information)
            else:
                raise ValueError("Unsupported special file in staged directory")

        add(root)
        for current, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
            for name in sorted(directories + files):
                add(Path(current) / name)
    checksum = hashlib.sha256()
    with open(destination, "rb") as stream:
        while block := stream.read(1024 * 1024):
            check()
            checksum.update(block)
    check()
    return {
        "size": os.path.getsize(destination),
        "sha256": checksum.hexdigest(),
        "unpacked_bytes": unpacked,
    }
