"""Standalone Python 3 receiver executed on the SLURM host over SSH stdin.

The first line is transfer metadata; the remaining bytes are the SIF. A remote
file lock covers reclamation, receipt and publication, including controller
restarts that leave an earlier SSH receiver alive.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time
import uuid


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"([0-9a-f]{64})\.sif\Z")
_PARTIAL = re.compile(r"\.partial-[0-9a-f]{32}\Z")
_ACCESS = re.compile(r"\.access-([0-9a-f]{64})\Z")
_CHUNK_SIZE = 1024 * 1024


def _timeout(
    _signum,
    _frame,
):
    raise TimeoutError("Remote image cache operation timed out")


def _file_signature(
    information,
):
    return (
        information.st_dev,
        information.st_ino,
        information.st_size,
        information.st_mtime_ns,
        information.st_ctime_ns,
    )


def _verified_signature(
    path,
    digest,
    expected_size,
    verified_signature=None,
):
    # The remote Python may predate hashlib.file_digest. Keep verification
    # bounded in memory and independent of installed Magnus dependencies.
    try:
        information = path.lstat()
        if not stat.S_ISREG(information.st_mode) or information.st_size != expected_size:
            return False
        signature = _file_signature(information)
        if verified_signature == list(signature):
            return signature
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            if _file_signature(os.fstat(stream.fileno())) != signature:
                return False
            while True:
                block = stream.read(_CHUNK_SIZE)
                if not block:
                    break
                checksum.update(block)
            if _file_signature(os.fstat(stream.fileno())) != signature:
                return False
        if "sha256:" + checksum.hexdigest() == digest and _file_signature(path.lstat()) == signature:
            return signature
        return None
    except TimeoutError:
        raise
    except OSError:
        return False


def _touch_access(
    image,
):
    # Access order must not change the inode metadata used as content identity.
    # This empty marker affects eviction order only, never integrity decisions.
    marker = image.with_name(".access-" + image.stem)
    descriptor = os.open(str(marker), os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Invalid image access marker")
        os.utime(descriptor, None)
    finally:
        os.close(descriptor)


def receive():
    # Only this script is sent to the remote Python; it has no Magnus imports.
    signal.signal(signal.SIGALRM, _timeout)
    signal.signal(signal.SIGTERM, _timeout)
    signal.alarm(int(sys.argv[2]))
    header = sys.stdin.buffer.readline(4 * 1024 * 1024)
    if not header.endswith(b"\n"):
        raise ValueError("Remote image transfer metadata exceeds 4 MiB")
    request = json.loads(header)
    digest = request["digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("Invalid built image digest")
    protected = set(request["protected"])
    existing_is_referenced = digest in protected
    protected.add(digest)
    if any(not _DIGEST.fullmatch(value) for value in protected):
        raise ValueError("Invalid protected image digest")
    size = int(request["size"])
    budget = int(request["budget"])
    free_floor = int(request["free_floor"])
    if size <= 0 or budget <= 0 or free_floor < 0 or size > budget:
        raise ValueError("Built image exceeds the remote image cache budget")

    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("Remote image cache directory cannot be a symbolic link")
    root = root.resolve()
    lock_path = root / ".lock"
    descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Remote image cache is busy; retry the Job")
                time.sleep(0.1)

        # The lock proves no live receiver owns these files. Recovery also
        # handles SIGKILL, where the previous receiver's finally did not run.
        for path in root.iterdir():
            if _PARTIAL.fullmatch(path.name):
                path.unlink()
            access = _ACCESS.fullmatch(path.name)
            if access and not (root / (access[1] + ".sif")).exists():
                path.unlink()

        images = []
        for path in root.iterdir():
            match = _IMAGE.fullmatch(path.name)
            if match is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Unexpected entry in the remote image cache")
            information = path.stat()
            marker = path.with_name(".access-" + match[1])
            accessed_at = marker.lstat().st_mtime if marker.exists() and not marker.is_symlink() else information.st_mtime
            images.append((accessed_at, path, information.st_size, "sha256:" + match[1]))

        destination = root / (digest.split(":")[1] + ".sif")
        signature = destination.is_file() and _verified_signature(
            destination,
            digest,
            size,
            request.get("verified_signature"),
        )
        cached = bool(signature)
        if destination.is_file() and not cached and not existing_is_referenced:
            # No other Job references these invalid bytes. Discard them before
            # reserving the replacement so a cache sized for one image can heal.
            destination.unlink()
            images = [item for item in images if item[1] != destination]
        # Referenced damaged copies stay protected and counted during receipt.
        # Publication restores the same content identity atomically; an already
        # open descriptor continues to refer to its original inode.
        incoming_size = 0 if cached else size
        used = sum(item[2] for item in images)
        for _, path, image_size, image_digest in sorted(images):
            if used + incoming_size <= budget and shutil.disk_usage(root).free >= free_floor + incoming_size:
                break
            if image_digest in protected:
                continue
            path.unlink()
            try:
                path.with_name(".access-" + path.stem).unlink()
            except FileNotFoundError:
                pass
            used -= image_size
        if used + incoming_size > budget:
            raise RuntimeError("Remote image cache is full with protected images")
        if shutil.disk_usage(root).free < free_floor + incoming_size:
            raise RuntimeError("Insufficient free disk space for remote image staging")
        if cached:
            _touch_access(destination)
            return {"cached": True, "signature": list(signature)}

        temporary = root / (".partial-" + uuid.uuid4().hex)
        try:
            received = 0
            checksum = hashlib.sha256()
            with temporary.open("xb") as output:
                while received < size:
                    block = sys.stdin.buffer.read(min(_CHUNK_SIZE, size - received))
                    if not block:
                        raise RuntimeError("Image transfer ended before the complete SIF arrived")
                    if shutil.disk_usage(root).free < free_floor + len(block):
                        raise RuntimeError("Remote disk free-space floor reached during image staging")
                    output.write(block)
                    checksum.update(block)
                    received += len(block)
                if sys.stdin.buffer.read(1):
                    raise ValueError("Image transfer is larger than its declared size")
                output.flush()
                os.fsync(output.fileno())
            if "sha256:" + checksum.hexdigest() != digest:
                raise ValueError("Transferred SIF checksum does not match its build result")
            os.chmod(str(temporary), 0o444)
            os.replace(str(temporary), str(destination))
            directory_fd = os.open(str(root), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            _touch_access(destination)
            signature = _verified_signature(destination, digest, size)
            if not signature:
                raise ValueError("Published image changed during verification")
            return {"cached": False, "signature": list(signature)}
        finally:
            if temporary.exists():
                temporary.unlink()


if __name__ == "__main__":
    try:
        print(json.dumps(receive()))
    except Exception as error:
        # Keep transport output bounded, including filesystem exception text.
        print(str(error)[:4096], file=sys.stderr)
        sys.exit(1)
