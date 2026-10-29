"""Cross-platform liveness and lease checks without signalling processes."""

import json
import os
from pathlib import Path


def process_lease_is_idle(
    directory: Path,
) -> bool:
    try:
        lease = json.loads(
            (directory / "process.json").read_text(
                encoding="utf-8",
            ),
        )
    except (OSError, ValueError):
        return False
    return isinstance(lease, dict) and lease.get("state") == "idle"


def _process_exists(
    pid: int,
) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    # os.kill(pid, 0) is not a liveness probe on Windows: non-console signals
    # map to TerminateProcess. Query the process handle without sending signals.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID
    try:
        return kernel.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel.CloseHandle(handle)


def _process_group_exists(
    process_group: int,
) -> bool:
    try:
        os.killpg(
            process_group,
            0,
        )
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
