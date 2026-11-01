"""Execute Magnus actions with the producing client's custody context."""

import asyncio
import os
import shlex
import signal
import subprocess
import time
from typing import TYPE_CHECKING, Optional

from .config import ENV_MAGNUS_ADDRESS, ENV_MAGNUS_TOKEN
from .exceptions import ExecutionError

if TYPE_CHECKING:
    from .client import MagnusClient


def _commands(
    action: str,
    job_id: Optional[str],
) -> list[list[str]]:
    commands = []
    for line in action.strip().splitlines():
        if not line.strip():
            continue
        try:
            tokens = shlex.split(
                line,
            )
        except ValueError as error:
            raise ExecutionError(
                "Invalid action command quoting",
            ) from error
        if not tokens or tokens[0] != "magnus":
            raise ExecutionError(
                "Action blocked: only 'magnus' commands are allowed",
            )
        if tokens[1:2] == ["receive"] and job_id is not None:
            separator = (
                tokens.index(
                    "--",
                )
                if "--" in tokens
                else len(
                    tokens,
                )
            )
            options = tokens[:separator]
            extra = []
            if not any(
                token == "--job"
                or token.startswith(
                    "--job=",
                )
                for token in options
            ):
                extra.extend(
                    ["--job", job_id],
                )
            if not any(
                token == "--wait"
                or token.startswith(
                    "--wait=",
                )
                for token in options
            ):
                extra.extend(
                    ["--wait", "600"],
                )
            tokens[separator:separator] = extra
        commands.append(
            tokens,
        )
    return commands


def _environment(
    client: Optional["MagnusClient"],
) -> Optional[dict[str, str]]:
    if client is None:
        return None
    environment = os.environ.copy()
    environment[ENV_MAGNUS_ADDRESS] = client.address
    environment[ENV_MAGNUS_TOKEN] = client.token
    return environment


def _remaining(
    deadline: Optional[float],
) -> Optional[float]:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExecutionError(
            "Action exceeded the Job wait timeout",
        )
    return remaining


def _signal_process(
    process,
    force: bool,
) -> None:
    try:
        if os.name == "nt":
            process.kill() if force else process.terminate()
        else:
            os.killpg(
                process.pid,
                signal.SIGKILL if force else signal.SIGTERM,
            )
    except ProcessLookupError:
        pass


def execute_action(
    action: str,
    *,
    client: Optional["MagnusClient"] = None,
    job_id: Optional[str] = None,
    timeout: Optional[float] = None,
) -> None:
    deadline = time.monotonic() + timeout if timeout is not None else None
    for tokens in _commands(
        action,
        job_id,
    ):
        _remaining(
            deadline,
        )
        try:
            process = subprocess.Popen(
                tokens,
                env=_environment(
                    client,
                ),
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            raise ExecutionError(
                "Could not start the Magnus action CLI",
            ) from error
        try:
            code = process.wait(
                timeout=_remaining(
                    deadline,
                ),
            )
        except subprocess.TimeoutExpired as error:
            raise ExecutionError(
                "Action exceeded the Job wait timeout",
            ) from error
        finally:
            if process.poll() is None:
                _signal_process(
                    process,
                    False,
                )
                try:
                    process.wait(
                        timeout=5,
                    )
                except subprocess.TimeoutExpired:
                    _signal_process(
                        process,
                        True,
                    )
                    process.wait()
        if code:
            raise ExecutionError(
                f"Action command failed (exit {code})",
            )


async def _join_task(
    task,
):
    """Finish owned process work even if the caller is cancelled repeatedly."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(
                task,
            )
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def _stop_async(
    process,
) -> None:
    if process.returncode is not None:
        return
    _signal_process(
        process,
        False,
    )
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=5,
        )
    except asyncio.TimeoutError:
        _signal_process(
            process,
            True,
        )
        await process.wait()


async def execute_action_async(
    action: str,
    *,
    client: Optional["MagnusClient"] = None,
    job_id: Optional[str] = None,
    timeout: Optional[float] = None,
) -> None:
    deadline = time.monotonic() + timeout if timeout is not None else None
    for tokens in _commands(
        action,
        job_id,
    ):
        _remaining(
            deadline,
        )
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *tokens,
                env=_environment(
                    client,
                ),
                start_new_session=os.name != "nt",
            ),
        )
        try:
            process = await asyncio.shield(
                spawning,
            )
        except asyncio.CancelledError:
            process, _ = await _join_task(
                spawning,
            )
            await _join_task(
                asyncio.create_task(
                    _stop_async(
                        process,
                    ),
                ),
            )
            raise
        except OSError as error:
            raise ExecutionError(
                "Could not start the Magnus action CLI",
            ) from error
        try:
            code = await asyncio.wait_for(
                process.wait(),
                timeout=_remaining(
                    deadline,
                ),
            )
        except asyncio.TimeoutError as error:
            raise ExecutionError(
                "Action exceeded the Job wait timeout",
            ) from error
        finally:
            _, cancelled = await _join_task(
                asyncio.create_task(
                    _stop_async(
                        process,
                    ),
                ),
            )
            if cancelled:
                raise asyncio.CancelledError
        if code:
            raise ExecutionError(
                f"Action command failed (exit {code})",
            )
