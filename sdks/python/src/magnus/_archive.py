"""Safe custody extraction, including Python builds before PEP 706 backports."""

import copy
import ntpath
import tarfile
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath


def _legacy_data_members(
    archive: tarfile.TarFile,
    destination: Path,
) -> Iterator[tarfile.TarInfo]:
    # SDK-produced archives dereference links. Older stdlib builds can support
    # that data-only format without attempting to recreate the link filter.
    root = destination.resolve()
    is_reserved = getattr(ntpath, "isreserved", None) or (
        lambda name: PureWindowsPath(name).is_reserved()
    )
    for member in archive:
        if not member.isfile() and not member.isdir():
            raise tarfile.TarError("This Python build supports only files and directories in custody archives")
        name = member.name
        if (
            name.startswith("/")
            or "\\" in name
            or ":" in name
            or any(
                part == ".." or is_reserved(part)
                for part in name.split("/")
            )
            or not (root / name).resolve().is_relative_to(root)
        ):
            raise tarfile.TarError("Unsafe custody archive member path")
        filtered = copy.copy(member)
        # Legacy extractall has no way to omit chown/chmod. -1 preserves the
        # current owner; keep temporary output readable and removable by it.
        filtered.uid = filtered.gid = -1
        filtered.uname = filtered.gname = None
        mode = member.mode & 0o755
        if member.isfile() and not mode & 0o100:
            mode &= ~0o111
        filtered.mode = mode | (0o700 if member.isdir() else 0o600)
        yield filtered


def extract_custody_archive(
    archive: Path,
    destination: Path,
) -> None:
    with tarfile.open(
        archive,
    ) as stream:
        if callable(
            getattr(tarfile, "data_filter", None),
        ):
            stream.extractall(
                destination,
                filter="data",
            )
        else:
            stream.extractall(
                destination,
                members=_legacy_data_members(
                    stream,
                    destination,
                ),
                numeric_owner=True,
            )
