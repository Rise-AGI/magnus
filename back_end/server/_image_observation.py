"""Cache observations distinguish missing objects from unavailable storage."""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ImageObservation:
    status: Literal["cached", "missing", "unknown"]
    size_bytes: int | None = None
    error: str | None = None


def inspect_docker_image(
    reference: str,
    *,
    timeout: float = 10,
) -> ImageObservation:
    # subprocess.run kills and reaps this direct CLI on timeout. Async callers
    # must join their worker before releasing ownership of the image record.
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", reference],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "LC_ALL": "C"},
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ImageObservation(
            status="unknown",
            error="timeout",
        )
    except OSError:
        return ImageObservation(
            status="unknown",
            error="runtime_unavailable",
        )
    if result.returncode == 0 and re.fullmatch(r"[0-9]+", result.stdout.strip()):
        return ImageObservation(
            status="cached",
            size_bytes=int(result.stdout.strip()),
        )
    if result.returncode == 1 and re.fullmatch(
        r"(?:Error response from daemon:|Error:) No such image: " + re.escape(reference),
        result.stderr.strip(),
    ):
        return ImageObservation(
            status="missing",
        )
    return ImageObservation(
        status="unknown",
        error="invalid_output" if result.returncode == 0 else "query_failed",
    )


def inspect_sif_image(
    path: str,
) -> ImageObservation:
    try:
        size = os.stat(path).st_size
    except FileNotFoundError:
        return ImageObservation(
            status="missing",
        )
    except OSError:
        return ImageObservation(
            status="unknown",
            error="storage_unavailable",
        )
    return ImageObservation(
        status="cached",
        size_bytes=size,
    )
