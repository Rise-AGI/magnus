"""One request per isolated process; never import the application package."""

import contextlib
import ctypes
import errno
import importlib
import json
import math
import os
import resource
import signal
import sys
import types
from pathlib import Path


def restrict_process(
    timeout: float,
    memory: int,
    output: int,
) -> None:
    resource.setrlimit(
        resource.RLIMIT_AS,
        (memory, memory),
    )
    cpu = max(
        1,
        math.ceil(
            timeout,
        ),
    )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (cpu, cpu),
    )
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (output, output),
    )
    resource.setrlimit(
        resource.RLIMIT_CORE,
        (0, 0),
    )
    resource.setrlimit(
        resource.RLIMIT_NOFILE,
        (32, 32),
    )
    signal.pthread_sigmask(
        signal.SIG_SETMASK,
        [],
    )
    signal.signal(
        signal.SIGALRM,
        signal.SIG_DFL,
    )
    signal.setitimer(
        signal.ITIMER_REAL,
        timeout,
    )

    library = ctypes.CDLL(
        "libseccomp.so.2",
        use_errno=True,
    )
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    library.seccomp_attr_set.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    policy = library.seccomp_init(
        0x00050000 | errno.EPERM,
    )  # SCMP_ACT_ERRNO
    if not policy:
        raise RuntimeError(
            "Cannot allocate evaluator syscall policy",
        )
    try:
        if (
            library.seccomp_attr_set(
                policy,
                4,
                1,
            )
            != 0
        ):  # SCMP_FLTATR_CTL_TSYNC
            raise RuntimeError(
                "Cannot synchronize evaluator syscall policy",
            )
        # Pure computation and reads of the read-only runtime are sufficient.
        # IPC, process creation, networking and signal/limit changes remain
        # denied even if Blueprint code reaches the real interpreter builtins.
        allowed = (
            "read",
            "write",
            "readv",
            "writev",
            "pread64",
            "pwrite64",
            "open",
            "openat",
            "close",
            "close_range",
            "lseek",
            "stat",
            "lstat",
            "fstat",
            "newfstatat",
            "statx",
            "access",
            "faccessat",
            "faccessat2",
            "readlink",
            "readlinkat",
            "getdents",
            "getdents64",
            "fcntl",
            "dup",
            "dup2",
            "dup3",
            "mmap",
            "mmap2",
            "mprotect",
            "munmap",
            "mremap",
            "madvise",
            "brk",
            "futex",
            "futex_time64",
            "sched_yield",
            "clock_gettime",
            "clock_gettime64",
            "gettimeofday",
            "time",
            "times",
            "clock_nanosleep",
            "clock_nanosleep_time64",
            "nanosleep",
            "getrandom",
            "getpid",
            "getppid",
            "gettid",
            "getpgrp",
            "getsid",
            "getuid",
            "geteuid",
            "getgid",
            "getegid",
            "getgroups",
            "getrusage",
            "getcwd",
            "uname",
            "exit",
            "exit_group",
        )
        for name in allowed:
            number = library.seccomp_syscall_resolve_name(
                name.encode(
                    "ascii",
                ),
            )
            if (
                number >= 0
                and library.seccomp_rule_add(
                    policy,
                    0x7FFF0000,
                    number,
                    0,
                )
                != 0
            ):
                raise RuntimeError(
                    f"Cannot restrict evaluator syscall {name}",
                )
        if (
            library.seccomp_load(
                policy,
            )
            != 0
        ):
            raise RuntimeError(
                "Cannot install evaluator syscall policy",
            )
    finally:
        library.seccomp_release(
            policy,
        )


def main() -> None:
    # Cover trusted bootstrap and stdin as well: the Docker CLI can disappear
    # between container creation and attaching its input stream.
    signal.signal(
        signal.SIGALRM,
        signal.SIG_DFL,
    )
    signal.setitimer(
        signal.ITIMER_REAL,
        float(
            sys.argv[1],
        )
        + 3,
    )
    root = (
        Path(
            __file__,
        )
        .resolve()
        .parent
    )
    sys.path.insert(
        0,
        "/runtime/site-packages",
    )
    package = types.ModuleType(
        "_magnus_evaluator",
    )
    package.__path__ = [
        str(
            root.parent,
        )
    ]
    sys.modules[package.__name__] = package
    engine_package = types.ModuleType(
        "_magnus_evaluator._blueprint_manager",
    )
    engine_package.__path__ = [
        str(
            root,
        )
    ]
    sys.modules[engine_package.__name__] = engine_package
    engine = importlib.import_module(
        "_magnus_evaluator._blueprint_manager._engine",
    )
    limit = int(
        sys.argv[4],
    )
    raw = sys.stdin.buffer.read(
        limit + 1,
    )
    if (
        len(
            raw,
        )
        > limit
    ):
        raise ValueError(
            "Blueprint request is too large",
        )
    request = json.loads(
        raw,
    )
    restrict_process(
        float(
            sys.argv[1],
        ),
        int(
            sys.argv[2],
        ),
        int(
            sys.argv[3],
        ),
    )
    response = {}
    try:
        with contextlib.redirect_stdout(
            sys.stderr,
        ):
            evaluator = engine.BlueprintEvaluator(
                request["code"],
                request["job_types"],
            )
            response["schema"] = evaluator.schema()
            if request["operation"] == "execute":
                response["job"] = evaluator.execute(
                    request["parameters"],
                )
            elif request["operation"] != "schema":
                raise ValueError(
                    "Unsupported Blueprint operation",
                )
    except Exception as error:
        response = {
            "error": str(
                error,
            )[:2000]
        }
    # os.write avoids a user blueprint replacing sys.stdout's Python object.
    data = json.dumps(
        response,
        ensure_ascii=False,
        allow_nan=False,
    ).encode(
        "utf-8",
    )
    view = memoryview(
        data,
    )
    while view:
        view = view[
            os.write(
                1,
                view,
            ) :
        ]


if __name__ == "__main__":
    main()
