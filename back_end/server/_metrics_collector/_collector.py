# back_end/server/_metrics_collector/_collector.py
"""主收集器：单 asyncio task 管所有 RUNNING docker job 的指标采样。"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

from .._magnus_config import magnus_config
from ..database import SessionLocal
from ..models import Job, JobStatus
from . import logger
from ._cgroup import (
    _read_allocated_cpus,
    _read_cpu_usage_usec,
    _read_memory_used_bytes,
)
from ._docker_inspect import (
    _docker_inspect_container_pid,
    _docker_inspect_visible_gpus,
)
from ._nvidia_smi import _sample_nvidia_smi


_SAMPLE_INTERVAL_SECONDS = 5.0  # match SLURM-side sidecar cadence (see metrics.md §17.2)


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
                except Exception as error:
                    logger.warning(f"Docker metrics tick failed: {error}")
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
            except Exception as error:
                logger.debug(f"Sample failed for docker job {job_id}: {error}")

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
