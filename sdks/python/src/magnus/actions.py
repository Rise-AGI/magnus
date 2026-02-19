# sdks/python/src/magnus/actions.py
import shlex
import subprocess
from .exceptions import ExecutionError


def execute_action(action: str) -> None:
    for line in action.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = shlex.split(line)
        if not tokens or tokens[0] != "magnus":
            raise ExecutionError(
                f"Action blocked: only 'magnus' commands are allowed. Got: {line}"
            )
        ret = subprocess.call(tokens)
        if ret != 0:
            raise ExecutionError(f"Action command failed (exit {ret}): {line}")
