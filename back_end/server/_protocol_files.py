"""Access Job protocol files without following runner-controlled links."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import stat


def _open_windows(
    stack: ExitStack,
    root: Path,
    parts: tuple[str, ...],
    mode: str,
    create_parents: bool,
    source: str | None = None,
) -> int | None:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME),
            ("write", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    inspect = kernel.GetFileInformationByHandle
    inspect.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    inspect.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    def open_handle(
        path: Path,
        directory: bool,
    ):
        absolute = str(path)
        if not absolute.startswith("\\\\?\\"):
            absolute = "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
        generic_read, generic_write = 0x80000000, 0x40000000
        share_read, share_write = 1, 2
        open_existing, open_always = 3, 4
        backup_semantics, open_reparse_point = 0x02000000, 0x00200000
        access = generic_read if directory or mode == "rb" else generic_write
        # Omit FILE_SHARE_DELETE: an ancestor cannot be replaced while the
        # leaf is open. OPEN_REPARSE_POINT lets us reject junctions and links.
        handle = create(
            absolute,
            access,
            share_read | share_write,
            None,
            open_existing if directory or mode == "rb" else open_always,
            backup_semantics | open_reparse_point,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        information = FileInformation()
        if not inspect(handle, ctypes.byref(information)):
            error = ctypes.WinError(ctypes.get_last_error())
            close(handle)
            raise error
        if information.attributes & 0x400 or bool(information.attributes & 0x10) != directory:
            close(handle)
            raise OSError("Job protocol path must not contain reparse points or special files")
        return handle

    path = root
    stack.callback(close, open_handle(path, True))
    for component in parts[:-1]:
        path /= component
        if create_parents:
            path.mkdir(exist_ok=True)
        stack.callback(close, open_handle(path, True))
    if source is not None:
        os.replace(source, path / parts[-1])
        return None
    handle = open_handle(path / parts[-1], False)
    try:
        return msvcrt.open_osfhandle(
            handle,
            os.O_BINARY | (os.O_RDONLY if mode == "rb" else os.O_WRONLY) | (os.O_APPEND if mode == "ab" else 0),
        )
    except BaseException:
        close(handle)
        raise


def _protocol_path(
    root: str | Path,
    relative_path: str | Path,
) -> tuple[Path, Path]:
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or any(part in ("..", ".") for part in relative.parts):
        raise ValueError("Protocol file must be beneath the Job root")
    if os.name == "nt" and any(":" in part for part in relative.parts):
        raise ValueError("Alternate data streams are not protocol files")
    return Path(root).resolve(strict=True), relative


def _open_posix_parent(
    stack: ExitStack,
    root: Path,
    relative: Path,
    create_parents: bool,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(root, flags)
    stack.callback(os.close, parent)
    for component in relative.parts[:-1]:
        if create_parents:
            try:
                os.mkdir(component, dir_fd=parent)
            except FileExistsError:
                pass
        parent = os.open(component, flags, dir_fd=parent)
        stack.callback(os.close, parent)
    return parent


def replace_protocol_file(
    root: str | Path,
    relative_path: str | Path,
    source: str,
) -> None:
    """Atomically publish a trusted same-filesystem download into a Job tree."""
    root, relative = _protocol_path(root, relative_path)
    with ExitStack() as stack:
        if os.name == "nt":
            _open_windows(stack, root, relative.parts, "wb", True, source)
        else:
            parent = _open_posix_parent(stack, root, relative, True)
            os.replace(source, relative.name, dst_dir_fd=parent)


@contextmanager
def open_protocol_file(
    root: str | Path,
    relative_path: str | Path,
    mode: str = "rb",
    *,
    create_parents: bool = False,
):
    """Open a regular, unshared file beneath a trusted workspace/jobs root.

    The root is administrator-owned; every component below it is untrusted.
    No truncation occurs until the opened descriptor has passed validation.
    """
    if mode not in ("rb", "wb", "ab"):
        raise ValueError("Unsupported protocol file mode")
    root, relative = _protocol_path(root, relative_path)
    with ExitStack() as stack:
        if os.name == "nt":
            descriptor = _open_windows(stack, root, relative.parts, mode, create_parents)
        else:
            parent = _open_posix_parent(stack, root, relative, create_parents)
            flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT
            if mode == "ab":
                flags |= os.O_APPEND
            descriptor = os.open(
                relative.name,
                flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o644,
                dir_fd=parent,
            )
        try:
            assert descriptor is not None
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError("Job protocol file must be regular and not hard-linked")
            if mode == "wb":
                os.ftruncate(descriptor, 0)
            stream = os.fdopen(descriptor, mode)
        except BaseException:
            os.close(descriptor)
            raise
        with stream:
            yield stream
