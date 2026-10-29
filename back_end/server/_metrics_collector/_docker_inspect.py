# back_end/server/_metrics_collector/_docker_inspect.py
"""docker inspect 调用包装。读 container PID + NVIDIA_VISIBLE_DEVICES。"""
from __future__ import annotations

import json
import subprocess
from typing import List, Optional


_DOCKER_INSPECT_TIMEOUT = 5


def _docker_inspect_container_pid(container_name: str) -> Optional[int]:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format", "{{.State.Pid}}",
                container_name,
            ],
            capture_output = True,
            text = True,
            timeout = _DOCKER_INSPECT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        pid = int(result.stdout.strip())
        # docker reports pid=0 when container has exited
        return pid if pid > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _docker_inspect_visible_gpus(
    container_name: str,
) -> Optional[List[str]]:
    """Return the list of GPU indices the container has access to.

    Reads NVIDIA_VISIBLE_DEVICES from container env. Returns:
      - ["0", "3"] for explicit indices
      - [] when no explicit indices can be safely attributed to this container
      - None for an unreadable response, which the caller may retry
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format", "{{json .Config.Env}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=_DOCKER_INSPECT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        env_list = json.loads(result.stdout.strip())
        if not isinstance(env_list, list):
            return None
        for entry in env_list:
            if isinstance(entry, str) and entry.startswith("NVIDIA_VISIBLE_DEVICES="):
                value = entry[len("NVIDIA_VISIBLE_DEVICES="):].strip()
                if value in ("none", ""):
                    return []
                if value in ("all", "void"):
                    # Shared-host device totals cannot be attributed to this Job.
                    return []
                indices = [s.strip() for s in value.split(",") if s.strip().isdigit()]
                return indices if indices else []
        # env var 缺失 → 容器没有 GPU 访问
        return []
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
