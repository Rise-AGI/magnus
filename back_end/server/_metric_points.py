"""Validate and stream metric records without loading unrelated data points."""

import json
import math
from collections.abc import Iterator
from typing import BinaryIO, NotRequired, TypedDict, cast

MAX_LINE_BYTES = 256 * 1024


class MetricPoint(TypedDict):
    name: str
    value: int | float
    time_unix_ms: int
    kind: NotRequired[str]
    labels: NotRequired[dict[str, str] | None]
    step: NotRequired[int | None]
    step_domain: NotRequired[str | None]
    unit: NotRequired[str | None]


def _text(
    value: object,
) -> bool:
    if not isinstance(
        value,
        str,
    ):
        return False
    try:
        value.encode(
            "utf-8",
        )
    except UnicodeError:
        return False
    return True


def _coordinate(
    value: object,
) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _labels(
    value: object,
) -> bool:
    return isinstance(
        value,
        dict,
    ) and all(_text(key) and _text(label) for key, label in value.items())


def parse_labels(
    value: str | None,
) -> dict[str, str] | None:
    """Preserve the existing no-filter fallback for malformed query labels."""
    if not value:
        return None
    try:
        labels = json.loads(
            value,
        )
    except (ValueError, RecursionError):
        return None
    return labels if _labels(labels) else None


def parse_point(
    line: bytes,
) -> MetricPoint | None:
    try:
        point = json.loads(
            line.decode(
                "utf-8",
            ),
        )
        if not isinstance(point, dict) or not _text(point.get("name")):
            return None
        value = point.get("value")
        if type(value) not in (int, float) or not math.isfinite(value):
            return None
        if not _coordinate(point.get("time_unix_ms")):
            return None
        # Historical producers omitted kind; the reader has always used gauge.
        if point.get("kind", "gauge") not in ("gauge", "counter"):
            return None
        if point.get("labels") is not None and not _labels(point["labels"]):
            return None
        if point.get("step") is not None and not _coordinate(point["step"]):
            return None
        for key in ("unit", "step_domain"):
            if point.get(key) is not None and not _text(point[key]):
                return None
        return cast(
            MetricPoint,
            point,
        )
    except (ValueError, UnicodeError, OverflowError, RecursionError):
        return None


def iter_points(
    stream: BinaryIO,
    size_bytes: int,
) -> Iterator[MetricPoint]:
    """Read a size snapshot, bounding a malformed line and ignoring later appends."""
    remaining = size_bytes
    oversized = False
    while remaining > 0:
        line = stream.readline(
            min(remaining, MAX_LINE_BYTES + 1),
        )
        if not line:
            return
        remaining -= len(line)
        if oversized or len(line) > MAX_LINE_BYTES:
            oversized = not line.endswith(b"\n")
            continue
        if not line.endswith(b"\n"):
            return
        point = parse_point(
            line,
        )
        if point is not None:
            yield point
