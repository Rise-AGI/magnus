"""Windows ownership and ACL checks for backend-private build scratch.

Only the current account and Windows administrators may control the leaf.
Ancestor directories may be readable/shared, but another user must not be able
to replace them or change their security. Existing ACLs are never rewritten.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


_SYSTEM = "S-1-5-18"
_ADMINISTRATORS = "S-1-5-32-544"
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
_PARENT_MUTATION = 0x500D0150  # generic write/all, DELETE, WRITE_DAC/OWNER, delete-child, write EA/attributes
_SYNCHRONIZE = 0x00100000


class _Acl(ctypes.Structure):
    _fields_ = [
        ("revision", wintypes.BYTE),
        ("reserved", wintypes.BYTE),
        ("size", wintypes.WORD),
        ("count", wintypes.WORD),
        ("reserved2", wintypes.WORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.BYTE),
        ("flags", wintypes.BYTE),
        ("size", wintypes.WORD),
    ]


class _FileInformation(ctypes.Structure):
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


class _WindowsSecurity:
    def __init__(
        self,
    ) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer = ctypes.c_void_p
        out_pointer = ctypes.POINTER(pointer)
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [pointer]
        self.kernel.LocalFree.restype = pointer
        self.kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_FileInformation),
        ]
        self.kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        self.security.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        self.security.OpenProcessToken.restype = wintypes.BOOL
        self.security.GetTokenInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, pointer, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.security.GetTokenInformation.restype = wintypes.BOOL
        self.security.GetSecurityInfo.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            out_pointer, out_pointer, out_pointer, out_pointer, out_pointer,
        ]
        self.security.GetSecurityInfo.restype = wintypes.DWORD
        self.security.GetAce.argtypes = [pointer, wintypes.DWORD, out_pointer]
        self.security.GetAce.restype = wintypes.BOOL
        self.security.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
        self.security.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.user_sid = self._current_user_sid()
        self.trusted = {self.user_sid, _SYSTEM, _ADMINISTRATORS, _TRUSTED_INSTALLER}

    def _sid_string(
        self,
        sid: int,
    ) -> str:
        text = wintypes.LPWSTR()
        if not self.security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            self.kernel.LocalFree(text)

    def _current_user_sid(
        self,
    ) -> str:
        token = wintypes.HANDLE()
        if not self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            information = ctypes.create_string_buffer(size.value)
            if not self.security.GetTokenInformation(token, 1, information, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(information, ctypes.POINTER(ctypes.c_void_p)).contents.value
            return self._sid_string(sid)
        finally:
            self.kernel.CloseHandle(token)

    def open_directory(
        self,
        directory: Path,
    ) -> int:
        # READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY. A handle
        # requesting only metadata rights does not enforce sharing restrictions;
        # list access makes omission of FILE_SHARE_DELETE prevent replacement.
        handle = self.kernel.CreateFileW(
            str(directory), 0x00020081, 0x00000003, None, 3,
            0x02200000, None,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            information = _FileInformation()
            if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(information)):
                raise ctypes.WinError(ctypes.get_last_error())
            if information.attributes & 0x400 or not information.attributes & 0x10:
                raise PermissionError(f"Image build scratch must not use a linked directory: {directory}")
            return handle
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def validate_directory(
        self,
        handle: int,
        directory: Path,
        *,
        private: bool,
    ) -> None:
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        error = self.security.GetSecurityInfo(
            handle, 1, 5, ctypes.byref(owner), None,
            ctypes.byref(dacl), None, ctypes.byref(descriptor),
        )
        if error:
            raise ctypes.WinError(error)
        try:
            owner_sid = self._sid_string(owner.value)
            if owner_sid not in ({self.user_sid} if private else self.trusted):
                raise PermissionError(f"Image build scratch path has an untrusted owner: {directory}")
            if not dacl.value:
                raise PermissionError(f"Image build scratch path has an unrestricted DACL: {directory}")
            count = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents.count
            for index in range(count):
                entry = ctypes.c_void_p()
                if not self.security.GetAce(dacl, index, ctypes.byref(entry)):
                    raise ctypes.WinError(ctypes.get_last_error())
                header = ctypes.cast(entry, ctypes.POINTER(_AceHeader)).contents
                if header.flags & 0x08 or header.type in {1, 6, 10, 12}:
                    # INHERIT_ONLY does not grant rights here; denial ACEs can
                    # only reduce access. We conservatively inspect all grants.
                    continue
                if header.type != 0 or header.size < 12:
                    raise PermissionError(f"Image build scratch path has an unsupported ACL grant: {directory}")
                mask = wintypes.DWORD.from_address(entry.value + 4).value
                sid = self._sid_string(entry.value + 8)
                if sid in self.trusted or sid == "S-1-3-4":  # OWNER RIGHTS refers to the validated owner
                    continue
                forbidden = mask & (~_SYNCHRONIZE if private else _PARENT_MUTATION)
                if forbidden:
                    raise PermissionError(f"Image build scratch path grants access to another account: {directory}")
        finally:
            self.kernel.LocalFree(descriptor)


def prepare_private_directory(
    directory: Path,
) -> Path:
    """Create/verify scratch, retaining ancestor handles throughout the walk."""
    directory = directory.parent.resolve() / directory.name
    security = _WindowsSecurity()
    handles: list[int] = []
    try:
        for path in [*reversed(directory.parents), directory]:
            try:
                handle = security.open_directory(path)
            except FileNotFoundError:
                try:
                    # Python >=3.13 creates a private DACL for mode 0o700 on
                    # Windows. Never weaken or rewrite a pre-existing ACL.
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                handle = security.open_directory(path)
            handles.append(handle)
            security.validate_directory(handle, path, private=path == directory)
        return directory
    finally:
        for handle in reversed(handles):
            security.kernel.CloseHandle(handle)
