"""Reject known impossible walltimes without guessing unavailable Slurm policy."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._transport import _Transport

logger = logging.getLogger(__name__)


def _duration_seconds(
    value: str,
) -> int | None:
    if value.upper() in {"UNLIMITED", "INFINITE"}:
        return None
    match = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d{2}):(\d{2})", value)
    if match is None:
        raise ValueError("Unrecognized partition time limit")
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError("Invalid partition time limit")
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


class PartitionTimeLimits:
    def __init__(
        self,
        transport: _Transport,
    ) -> None:
        self.transport = transport
        self.lock = threading.Lock()
        self.expires = 0.0
        self.policy: tuple[list[dict[str, str]], dict[str, bool]] | None = None

    def _read(
        self,
    ) -> tuple[list[dict[str, str]], dict[str, bool]] | None:
        # Submission runs in worker threads. Coalesce concurrent reads without
        # holding an event-loop lock or making each submit pay remote SSH latency.
        with self.lock:
            if time.monotonic() < self.expires:
                return self.policy
            deadline = time.monotonic() + 5.0
            try:
                partitions = self.transport.run(
                    ["scontrol", "--all", "--oneliner", "show", "partition"],
                    check=True,
                    timeout=max(0.01, deadline - time.monotonic()),
                ).stdout
                rows = []
                for line in partitions.splitlines():
                    if not line.strip():
                        continue
                    row = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9]*)=([^\s]*)", line))
                    if not {"PartitionName", "Default", "MaxTime"} <= row.keys():
                        raise ValueError("Incomplete partition policy")
                    _duration_seconds(row["MaxTime"])
                    rows.append(row)
                if not rows or len(rows) > 256:
                    raise ValueError("Invalid partition count")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Partition policy deadline")
                output = self.transport.run(
                    ["sacctmgr", "--noheader", "--parsable2", "show", "qos", "format=Name,Flags"],
                    check=True,
                    timeout=remaining,
                ).stdout
                qos = {}
                for line in output.splitlines():
                    if not line.strip():
                        continue
                    fields = line.split("|")
                    if len(fields) == 3 and fields[-1] == "":
                        fields.pop()
                    # Empty Flags is still a column in parsable2 output.
                    if "|" not in line:
                        raise ValueError("Invalid QOS policy")
                    name = fields[0].strip()
                    flags = fields[1].strip() if len(fields) == 2 else ""
                    if not name or len(fields) != 2 or name in qos:
                        raise ValueError("Invalid QOS row")
                    qos[name] = "PartitionTimeLimit" in flags.split(",")
                if not qos or len(qos) > 4096:
                    raise ValueError("Unavailable QOS policy")
                self.policy = rows, qos
                self.expires = time.monotonic() + 30.0
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.policy = None
                self.expires = time.monotonic() + 5.0
                logger.warning("Partition time precheck unavailable (%s)", type(error).__name__)
            return self.policy

    def validate(
        self,
        *,
        minutes: int | None,
        partition: str | None,
        qos: str | None,
    ) -> None:
        if minutes is None:
            return
        policy = self._read()
        if policy is None:
            return
        rows, flags = policy
        # A job QOS can explicitly override partition MaxTime. With an implicit
        # association default, reject only if no visible QOS grants that override.
        if qos is not None:
            selected_qos = qos.split(",")
            if any(name not in flags or flags[name] for name in selected_qos):
                return
        elif any(flags.values()):
            return
        if partition is None:
            selected = [row for row in rows if row["Default"] == "YES"]
            if len(selected) != 1:
                return
        else:
            names = set(partition.split(","))
            selected = [row for row in rows if row["PartitionName"] in names]
            if {row["PartitionName"] for row in selected} != names:
                return
        limits = [_duration_seconds(row["MaxTime"]) for row in selected]
        if any(limit is None or (minutes > 0 and minutes * 60 <= limit) for limit in limits):
            return
        detail = ", ".join(f"{row['PartitionName']}: {row['MaxTime']}" for row in selected)
        raise ValueError(
            f"time_limit={minutes} min exceeds Slurm partition MaxTime ({detail}); "
            "request a shorter time_limit or select another execution target",
        )
