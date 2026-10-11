"""Launch Git inside a Windows Job Object before any transport helpers exist.

This file is executed by path, using the backend's Python interpreter; importing
the server package here would initialize application state. The launcher owns a
non-inheritable job handle. Its exit (normal, exceptional or forced) closes that
handle and terminates all descendants, including helpers retaining pipe handles.
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _run_git() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    # NULL security attributes make the job handle non-inheritable. Git cannot
    # keep the job alive after the launcher dies or break away from its limits.
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = _ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not kernel32.SetInformationJobObject(
            job,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.AssignProcessToJobObject(
            job,
            kernel32.GetCurrentProcess(),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel32.CloseHandle(job)
        raise

    # Keep our only job handle open until process exit. Closing it explicitly
    # while we are assigned would kill this launcher before it returns Git's
    # exit code. Windows closes it on every exit path, including TerminateProcess.
    return subprocess.call(
        ["git", *sys.argv[1:]],
        stdin = sys.stdin,
        stdout = sys.stdout,
        stderr = sys.stderr,
        close_fds = True,
    )


if __name__ == "__main__":
    try:
        status = _run_git()
    except OSError as error:
        print(
            f"Could not launch Git in a Windows Job Object: {error}",
            file = sys.stderr,
        )
        status = 127
    sys.exit(status)
