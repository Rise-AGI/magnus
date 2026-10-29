"""Git process ownership and diagnostics without application initialization."""

import asyncio
import os
import signal
import sys

GIT_STDERR_LIMIT = 64 * 1024

_git_env = os.environ.copy()
_git_env["GIT_TERMINAL_PROMPT"] = "0"
for _key in ("GIT_ASKPASS", "SSH_ASKPASS"):
    _git_env.pop(
        _key,
        None,
    )


class GitOperationError(RuntimeError):
    """A safe preparation diagnostic, without command arguments or raw output."""


def describe_git_failure(
    operation: str,
    returncode: int,
    stderr: bytes,
) -> str:
    # Git/proxy helpers can echo credential URLs or response bodies. Classify
    # known failures rather than publishing arbitrary stderr into shared Jobs.
    text = stderr.lower()
    reasons = (
        ((b"proxy authentication", b"error: 407"), "proxy authentication failed"),
        ((b"error: 403", b"forbidden"), "access denied (HTTP 403)"),
        ((b"authentication failed", b"error: 401", b"permission denied (publickey"),
         "repository authentication failed"),
        ((b"host key verification failed", b"remote host identification has changed"),
         "SSH host key verification failed"),
        ((b"could not resolve", b"name or service not known", b"name resolution"),
         "host name resolution failed"),
        ((b"certificate", b"ssl peer", b"ssl connect", b"tls connection"),
         "TLS connection or certificate validation failed"),
        ((b"connection timed out", b"operation timed out", b"connection timeout"),
         "network connection timed out"),
        ((b"connection reset", b"connection closed", b"remote end hung up", b"early eof"),
         "connection interrupted"),
        ((b"failed to connect", b"connection refused", b"network is unreachable"),
         "network connection failed"),
        ((b"error: 429", b"too many requests"), "upstream rate limit reached"),
        ((b"error: 500", b"error: 502", b"error: 503", b"error: 504"),
         "upstream service unavailable"),
        ((b"repository not found", b"does not appear to be a git repository"),
         "repository unavailable or inaccessible"),
        ((b"remote branch", b"couldn't find remote ref", b"invalid reference", b"not a valid"),
         "requested revision unavailable"),
        ((b"no space left", b"disk quota exceeded"), "repository storage exhausted"),
        ((b"permission denied", b"read-only file system"), "repository access denied"),
        ((b"index.lock", b"unable to create"), "repository write failed"),
    )
    reason = next(
        (
            reason
            for patterns, reason in reasons
            if any(
                pattern in text for pattern in patterns
            )
        ),
        "unclassified Git error",
    )
    return f"git {operation} failed (exit {returncode}): {reason}"


async def _read_stderr_tail(
    stream: asyncio.StreamReader | None,
) -> bytes:
    if stream is None:
        return b""
    tail = bytearray()
    storage_failure = b""
    while chunk := await stream.read(
        16 * 1024,
    ):
        if not storage_failure:
            text = (tail[-32:] + chunk).lower()
            for marker in (b"no space left on device", b"disk quota exceeded"):
                if marker in text:
                    storage_failure = marker
                    break
        tail.extend(
            chunk,
        )
        if len(
            tail,
        ) > GIT_STDERR_LIMIT:
            del tail[:-GIT_STDERR_LIMIT]
    if storage_failure and storage_failure not in tail.lower():
        # Keep the early failure that controls disk-full fail-fast, even when
        # later helper output has displaced it. Never retain that raw line.
        prefix = b"[earlier Git stderr: " + storage_failure + b"]\n"
        return prefix + bytes(
            tail[-(GIT_STDERR_LIMIT - len(prefix)):],
        )
    return bytes(
        tail,
    )


async def _communicate_git(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    async def read_stdout() -> bytes:
        if process.stdout is None:
            return b""
        return await process.stdout.read()

    stdout, stderr, _ = await asyncio.gather(
        read_stdout(),
        _read_stderr_tail(
            process.stderr,
        ),
        process.wait(),
    )
    return stdout, stderr


async def _kill_git_process(
    process: asyncio.subprocess.Process,
) -> None:
    if os.name == "posix":
        # Helpers can keep pipes open or keep writing packs after Git exits.
        try:
            os.killpg(
                process.pid,
                signal.SIGKILL,
            )
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        # The Windows launcher owns a KILL_ON_JOB_CLOSE Job Object.
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def _run_git(
    *arguments: str,
    cwd: str | None = None,
    timeout: float | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> tuple[int, bytes, bytes]:
    """Retain full stdout and bounded stderr, preserving disk-full detection.

    The caller keeps its repo lock until every child has stopped, even during
    spawn or repeated cancellation. Clone has no deadline; owner cancellation
    still interrupts its network I/O and joins cleanup before returning.
    """
    command = ["git"]
    if os.name == "nt":
        command = [
            sys.executable,
            os.path.join(
                os.path.dirname(
                    os.path.abspath(
                        __file__,
                    ),
                ),
                "_git_windows.py",
            ),
        ]
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            *arguments,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
            env=_git_env,
            start_new_session=os.name == "posix",
        ),
    )
    communication = None
    try:
        process = await asyncio.shield(
            spawning,
        )
        communication = asyncio.create_task(
            _communicate_git(
                process,
            ),
        )
        if timeout is None:
            stdout, stderr = await asyncio.shield(
                communication,
            )
        else:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(
                    communication,
                ),
                timeout=timeout,
            )
        assert process.returncode is not None
        return process.returncode, stdout, stderr
    except BaseException as error:
        async def stop_and_reap() -> None:
            try:
                process = await spawning
            except Exception:
                return
            reader = communication or asyncio.create_task(
                _communicate_git(
                    process,
                ),
            )
            try:
                await _kill_git_process(
                    process,
                )
            finally:
                await asyncio.gather(
                    reader,
                    return_exceptions=True,
                )

        cleanup = asyncio.create_task(
            stop_and_reap(),
        )
        while not cleanup.done():
            try:
                await asyncio.shield(
                    cleanup,
                )
            except asyncio.CancelledError:
                continue
        cleanup.result()
        if isinstance(
            error,
            OSError,
        ) and not isinstance(
            error,
            TimeoutError,
        ):
            raise GitOperationError(
                f"Git process could not run (OS error {error.errno})",
            ) from None
        raise
