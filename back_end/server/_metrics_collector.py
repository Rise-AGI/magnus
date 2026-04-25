# back_end/server/_metrics_collector.py
"""
Host-side system metrics collector for Docker (local) mode.

In SLURM mode, system metrics are sampled by the in-container `_metrics_sidecar`
embedded in wrapper.py (see `_build_wrapper_content` in `_scheduler.py`). In Docker
mode there is no wrapper — we cannot inject a sidecar inside the container. So
this collector samples cgroup data + nvidia-smi from the host and writes the same
JSONL format (Magnus Metrics Protocol v1) to `{work_dir}/metrics/system.jsonl`,
keeping dual-mode parity at the producer-output level.

Architecture (Option C): a single asyncio task started at scheduler init manages
all RUNNING docker jobs. Per-tick, it discovers RUNNING jobs from the DB, resolves
each container's cgroup path via host PID, samples cpu/memory/GPU, and emits points.
A single nvidia-smi invocation per tick is shared across all jobs.

Fail-open is total: cgroup unreadable, container vanished, nvidia-smi missing —
silently skip that sample/job/metric and continue. Nothing here may raise into
the scheduler heartbeat.

Cgroup parsing here is intentionally duplicated from the equivalent logic in
wrapper.py (`_metrics_sidecar` / `_check_oom`), because wrapper.py is built by
f-string templating and cannot import from this module. Keep the two impls
behaviorally equivalent; if you change one, change the other.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from ._magnus_config import magnus_config
from .database import SessionLocal
from .models import Job, JobStatus


__all__ = ["DockerMetricsCollector"]


logger = logging.getLogger(__name__)


_SAMPLE_INTERVAL_SECONDS = 5.0  # match SLURM-side sidecar cadence (see metrics.md §17.2)
_NVIDIA_SMI_TIMEOUT = 10
_DOCKER_INSPECT_TIMEOUT = 5


# ─────────────────────────── cgroup helpers (host-side) ───────────────────────────
# Mirror of the parsing logic embedded in wrapper.py's _metrics_sidecar / _check_oom.

def _read_proc_cgroup_for_pid(pid: int) -> Tuple[Optional[str], Optional[str]]:
    """Return (v2_rel, v1_memory_rel) from /proc/<pid>/cgroup. Either may be None.

    For v2 ("0::<rel>"), v2_rel is set and v1_memory_rel is None.
    For v1 (multiple "<id>:<controllers>:<rel>" lines), v2_rel is None and
    v1_memory_rel is the memory controller's relative path.
    On hybrid systems, prefer v2 if a "0::" line is present.
    """
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    except OSError:
        return None, None

    v2_rel: Optional[str] = None
    v1_mem: Optional[str] = None
    v1_cpu: Optional[str] = None
    for line in lines:
        if line.startswith("0::"):
            v2_rel = line[3:]
        else:
            cols = line.split(":", 2)
            if len(cols) != 3:
                continue
            controllers = cols[1].split(",")
            if "memory" in controllers and v1_mem is None:
                v1_mem = cols[2]
            if ("cpuacct" in controllers or "cpu" in controllers) and v1_cpu is None:
                v1_cpu = cols[2]

    # We only return memory-rel for v1; cpu-rel is computed inline by callers
    # that need both. Caller distinguishes v1 vs v2 by which value is set.
    if v2_rel is not None:
        return v2_rel, None
    return None, v1_mem


def _read_v1_cpu_rel(pid: int) -> Optional[str]:
    """Return cpuacct (preferred) or cpu controller relative path for v1; None on v2/error."""
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("0::"):
                    continue
                cols = line.split(":", 2)
                if len(cols) != 3:
                    continue
                controllers = cols[1].split(",")
                if "cpuacct" in controllers:
                    return cols[2]
    except OSError:
        return None
    # Fallback: any line with "cpu"
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("0::"):
                    continue
                cols = line.split(":", 2)
                if len(cols) != 3:
                    continue
                controllers = cols[1].split(",")
                if "cpu" in controllers:
                    return cols[2]
    except OSError:
        return None
    return None


def _read_cpu_usage_usec(pid: int) -> Optional[int]:
    """Return cumulative cpu usage in microseconds for the cgroup containing pid."""
    v2_rel, _ = _read_proc_cgroup_for_pid(pid)
    if v2_rel is not None:
        path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/cpu.stat"
        try:
            with open(path, "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "usage_usec":
                        return int(parts[1])
        except (OSError, ValueError):
            return None
        return None

    cpu_rel = _read_v1_cpu_rel(pid)
    if cpu_rel is None:
        return None
    # cpuacct.usage is in nanoseconds
    path = "/sys/fs/cgroup/cpuacct" + cpu_rel.rstrip("/") + "/cpuacct.usage"
    try:
        with open(path, "r") as f:
            ns = int(f.read().strip())
        return ns // 1000
    except (OSError, ValueError):
        return None


def _read_memory_used_bytes(pid: int) -> Optional[int]:
    """Return current memory usage in bytes for the cgroup containing pid."""
    v2_rel, v1_mem = _read_proc_cgroup_for_pid(pid)
    if v2_rel is not None:
        path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/memory.current"
        try:
            with open(path, "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None
    if v1_mem is None:
        return None
    path = "/sys/fs/cgroup/memory" + v1_mem.rstrip("/") + "/memory.usage_in_bytes"
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_allocated_cpus(pid: int) -> Optional[float]:
    """Read CPU quota allocated to the cgroup, in fractional cores. None if unlimited/error."""
    v2_rel, _ = _read_proc_cgroup_for_pid(pid)
    if v2_rel is not None:
        path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/cpu.max"
        try:
            with open(path, "r") as f:
                content = f.read().strip().split()
            if len(content) != 2:
                return None
            quota_s, period_s = content
            if quota_s == "max":
                return None
            quota = int(quota_s)
            period = int(period_s)
            if period <= 0 or quota <= 0:
                return None
            return quota / period
        except (OSError, ValueError):
            return None

    cpu_rel = _read_v1_cpu_rel(pid)
    if cpu_rel is None:
        return None
    base = "/sys/fs/cgroup/cpu" + cpu_rel.rstrip("/")
    try:
        with open(base + "/cpu.cfs_quota_us", "r") as f:
            quota = int(f.read().strip())
        with open(base + "/cpu.cfs_period_us", "r") as f:
            period = int(f.read().strip())
        if quota <= 0 or period <= 0:
            return None
        return quota / period
    except (OSError, ValueError):
        return None


# ───────────────────────────── docker inspect helpers ─────────────────────────────

def _docker_inspect_container_pid(container_name: str) -> Optional[int]:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Pid}}", container_name],
            capture_output=True, text=True, timeout=_DOCKER_INSPECT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        pid = int(result.stdout.strip())
        # docker reports pid=0 when container has exited
        return pid if pid > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _docker_inspect_visible_gpus(container_name: str) -> Optional[List[str]]:
    """Return the list of GPU indices the container has access to.

    Reads NVIDIA_VISIBLE_DEVICES from container env first. Returns:
      - ["0", "3"] for explicit indices
      - [] for "none" / no GPU access
      - None for "all" / "void" / unset (caller should not filter, OR sample nothing)
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", container_name],
            capture_output=True, text=True, timeout=_DOCKER_INSPECT_TIMEOUT,
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
                    return None  # ambiguous — sample nothing, conservative
                indices = [s.strip() for s in value.split(",") if s.strip().isdigit()]
                return indices if indices else []
        return []  # env var absent → no GPU access
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


# ────────────────────────────── nvidia-smi sampler ────────────────────────────────

def _sample_nvidia_smi() -> Optional[Dict[str, Tuple[float, float]]]:
    """Return {gpu_idx: (util_pct, mem_used_bytes)} or None on failure."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            timeout=_NVIDIA_SMI_TIMEOUT, text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    result: Dict[str, Tuple[float, float]] = {}
    for row in out.strip().split("\n"):
        parts = [p.strip() for p in row.split(",")]
        if len(parts) != 3:
            continue
        try:
            idx = parts[0]
            util = float(parts[1])
            mem_mib = float(parts[2])
        except ValueError:
            continue
        result[idx] = (util, mem_mib * 1048576)
    return result


# ─────────────────────────────── per-job state ───────────────────────────────────

class _JobMetricsState:
    __slots__ = (
        "container_name", "pid", "prev_usage_usec", "prev_wall_ms",
        "alloc_cpus", "visible_gpus",
    )

    def __init__(self, container_name: str, pid: int) -> None:
        self.container_name = container_name
        self.pid = pid
        self.prev_usage_usec: Optional[int] = None
        self.prev_wall_ms: Optional[int] = None
        # Cache allocated CPUs lookup; refreshed only if state is rebuilt.
        self.alloc_cpus: Optional[float] = None
        # Cache visible-GPU lookup; refreshed only if state is rebuilt.
        # None = "all"/"void"/inspect-failed (conservative: sample nothing);
        # [] = explicit no GPU access; non-empty list = filter to these indices.
        # The None vs [] distinction is preserved so callers stay fail-open.
        self.visible_gpus: Optional[List[str]] = None


# ──────────────────────────── main collector class ────────────────────────────────

class DockerMetricsCollector:
    """Single asyncio task that samples system metrics for all RUNNING docker jobs."""

    def __init__(self) -> None:
        self._states: Dict[str, _JobMetricsState] = {}  # job_id -> state
        self._hostname = socket.gethostname()
        self._task: Optional[asyncio.Task] = None
        self._workspace = (
            f"{magnus_config['server']['root']}/workspace"
        )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        logger.info("Docker metrics collector started")
        try:
            while True:
                try:
                    await asyncio.to_thread(self._tick)
                except Exception as e:
                    logger.warning(f"Docker metrics tick failed: {e}")
                await asyncio.sleep(_SAMPLE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("Docker metrics collector stopped")
            raise

    # -------- tick (runs in thread) --------

    def _list_running_docker_jobs(self) -> List[str]:
        with SessionLocal() as db:
            rows = db.query(Job.id).filter(Job.status == JobStatus.RUNNING).all()
            return [r[0] for r in rows]

    def _tick(self) -> None:
        running_ids = self._list_running_docker_jobs()
        running_set = set(running_ids)

        # Drop stale states (job no longer running)
        for stale_id in [jid for jid in self._states if jid not in running_set]:
            self._states.pop(stale_id, None)

        if not running_ids:
            return

        now_ms = int(time.time() * 1000)
        # Lazy: sample nvidia-smi only if at least one job actually has visible GPUs.
        # _sample_job calls back into self via this slot; resolve on first use.
        gpu_snapshot_box: List[Optional[Dict[str, Tuple[float, float]]]] = [None]
        gpu_snapshot_resolved = [False]

        def get_gpu_snapshot() -> Optional[Dict[str, Tuple[float, float]]]:
            if not gpu_snapshot_resolved[0]:
                gpu_snapshot_box[0] = _sample_nvidia_smi()
                gpu_snapshot_resolved[0] = True
            return gpu_snapshot_box[0]

        for job_id in running_ids:
            try:
                self._sample_job(job_id, get_gpu_snapshot, now_ms)
            except Exception as e:
                logger.debug(f"Sample failed for docker job {job_id}: {e}")

    def _sample_job(
        self,
        job_id: str,
        get_gpu_snapshot: Any,
        now_ms: int,
    ) -> None:
        state = self._states.get(job_id)
        if state is None:
            container_name = f"magnus-job-{job_id}"
            pid = _docker_inspect_container_pid(container_name)
            if pid is None:
                return  # container not yet running or already gone — fail-open
            state = _JobMetricsState(container_name, pid)
            state.alloc_cpus = _read_allocated_cpus(pid)
            state.visible_gpus = _docker_inspect_visible_gpus(container_name)
            self._states[job_id] = state

        lines: List[str] = []
        node_labels = {"node": self._hostname}

        # CPU utilization (cgroup-based)
        usage_usec = _read_cpu_usage_usec(state.pid)
        if usage_usec is not None:
            if state.prev_usage_usec is not None and state.prev_wall_ms is not None:
                d_usage = usage_usec - state.prev_usage_usec
                d_wall_ms = now_ms - state.prev_wall_ms
                cpus = state.alloc_cpus or float(os.cpu_count() or 1)
                if d_wall_ms > 0 and cpus > 0 and d_usage >= 0:
                    cpu_pct = round((d_usage / (d_wall_ms * 1000.0) / cpus) * 100, 1)
                    # Clamp to [0, 100] — quota under-counts can drift a hair past 100
                    if cpu_pct > 100.0:
                        cpu_pct = 100.0
                    lines.append(json.dumps({
                        "name": "system.cpu.utilization", "kind": "gauge",
                        "value": cpu_pct, "time_unix_ms": now_ms,
                        "unit": "percent", "labels": node_labels,
                    }))
            state.prev_usage_usec = usage_usec
            state.prev_wall_ms = now_ms

        # Memory used
        mem_bytes = _read_memory_used_bytes(state.pid)
        if mem_bytes is not None:
            lines.append(json.dumps({
                "name": "system.memory.used_bytes", "kind": "gauge",
                "value": float(mem_bytes), "time_unix_ms": now_ms,
                "unit": "bytes", "labels": node_labels,
            }))

        # GPU metrics (filtered by NVIDIA_VISIBLE_DEVICES, cached on state).
        # `None` (ambiguous/inspect-failed) and `[]` (explicit no GPUs) both skip;
        # a non-empty list of indices triggers the nvidia-smi sample for this job.
        visible = state.visible_gpus
        if visible:
            gpu_snapshot = get_gpu_snapshot()
            if gpu_snapshot:
                for idx in visible:
                    if idx not in gpu_snapshot:
                        continue
                    util, mem_used_bytes = gpu_snapshot[idx]
                    gpu_labels = {"device": f"cuda:{idx}", "node": self._hostname}
                    lines.append(json.dumps({
                        "name": "system.gpu.utilization", "kind": "gauge",
                        "value": float(util), "time_unix_ms": now_ms,
                        "unit": "percent", "labels": gpu_labels,
                    }))
                    lines.append(json.dumps({
                        "name": "system.gpu.memory.used_bytes", "kind": "gauge",
                        "value": float(mem_used_bytes), "time_unix_ms": now_ms,
                        "unit": "bytes", "labels": gpu_labels,
                    }))

        if not lines:
            return

        metrics_path = f"{self._workspace}/jobs/{job_id}/metrics/system.jsonl"
        try:
            with open(metrics_path, "a") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            # metrics dir may not exist yet (job tear-down race); skip
            pass
