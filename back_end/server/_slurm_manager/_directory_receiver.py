"""Standalone Python >=3.6 receiver; never extract through archive symlinks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import tempfile


def _timeout(
    _number,
    _frame,
):
    raise TimeoutError("Directory transfer timed out")


def unpack_directory(
    archive_path,
    destination,
    unpacked_bytes,
):
    root = Path(destination)
    directories = []
    links = []
    link_paths = set()
    names = set()
    total = 0
    with tarfile.open(str(archive_path), "r:gz") as archive:
        for member in archive:
            parts = member.name.split("/")
            if member.name == "." and member.isdir():
                parts = []
            elif any(part in ("", ".", "..") for part in parts):
                raise ValueError("Unsafe archive member path")
            name = tuple(parts)
            if name in names or not (member.isdir() or member.isfile() or member.issym()):
                raise ValueError("Duplicate or unsupported archive member")
            names.add(name)
            target = root.joinpath(*parts)
            # Links are created last. No archive-controlled path can redirect
            # a write, even when an absolute or dangling link is preserved.
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                directories.append((target, member.mode & 0o777))
            elif member.issym():
                links.append((target, member.linkname))
                link_paths.add(name)
            else:
                if member.size < 0:
                    raise ValueError("Invalid archive member size")
                total += member.size
                if total > unpacked_bytes:
                    raise ValueError("Archive exceeds declared unpacked size")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                os.chmod(str(target), member.mode & 0o777)
        if total != unpacked_bytes:
            raise ValueError("Archive unpacked size mismatch")
    if any(name[:index] in link_paths for name in names for index in range(1, len(name))):
        raise ValueError("Archive member is below a symbolic link")
    for target, link in links:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(link, str(target))
    for target, mode in reversed(directories):
        os.chmod(str(target), mode)


def receive():
    signal.signal(signal.SIGALRM, _timeout)
    signal.signal(signal.SIGTERM, _timeout)
    signal.alarm(int(sys.argv[2]))
    header = sys.stdin.buffer.readline(65536)
    if not header.endswith(b"\n"):
        raise ValueError("Invalid directory transfer header")
    request = json.loads(header)
    size = request["size"]
    unpacked = request["unpacked_bytes"]
    if not isinstance(size, int) or size <= 0 or not isinstance(unpacked, int) or unpacked < 0:
        raise ValueError("Invalid directory transfer size")
    target = Path(sys.argv[1])
    if os.path.lexists(str(target)):
        raise ValueError("Directory staging destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".magnus-directory-", dir=str(target.parent)))
    try:
        compressed = temporary / "archive.tar.gz"
        checksum = hashlib.sha256()
        remaining = size
        with compressed.open("xb") as output:
            while remaining:
                block = sys.stdin.buffer.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("Incomplete directory transfer")
                output.write(block)
                checksum.update(block)
                remaining -= len(block)
            if sys.stdin.buffer.read(1):
                raise ValueError("Directory transfer exceeds declared size")
        if checksum.hexdigest() != request["sha256"]:
            raise ValueError("Directory transfer checksum mismatch")
        tree = temporary / "tree"
        tree.mkdir()
        unpack_directory(compressed, tree, unpacked)
        if os.path.lexists(str(target)):
            raise ValueError("Directory staging destination changed")
        os.rename(str(tree), str(target))
    finally:
        # Restore owner traversal only inside our unpublished temporary tree.
        for current, directories, _files in os.walk(str(temporary), followlinks=False):
            os.chmod(current, 0o700)
            for name in directories:
                path = os.path.join(current, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700)
        shutil.rmtree(str(temporary))


if __name__ == "__main__":
    try:
        receive()
    except Exception:
        print("Directory staging failed; no incomplete directory was published", file=sys.stderr)
        sys.exit(1)
