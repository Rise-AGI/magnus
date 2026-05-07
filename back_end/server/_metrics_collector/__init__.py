# back_end/server/_metrics_collector/__init__.py
"""Host-side system metrics collector for Docker (local) mode.

In SLURM mode, system metrics are sampled by the in-container `_metrics_sidecar`
embedded in wrapper.py (see `_build_wrapper_content` in
`_scheduler/_wrapper_template.py`). In Docker mode there is no wrapper — we
cannot inject a sidecar inside the container. So this collector samples cgroup
data + nvidia-smi from the host and writes the same JSONL format (Magnus
Metrics Protocol v1) to `{work_dir}/metrics/system.jsonl`, keeping dual-mode
parity at the producer-output level.

Architecture (Option C): a single asyncio task started at scheduler init
manages all RUNNING docker jobs. Per-tick, it discovers RUNNING jobs from the
DB, resolves each container's cgroup path via host PID, samples cpu/memory/GPU,
and emits points. A single nvidia-smi invocation per tick is shared across all
jobs.

Fail-open is total: cgroup unreadable, container vanished, nvidia-smi missing —
silently skip that sample/job/metric and continue. Nothing here may raise into
the scheduler heartbeat.

Cgroup parsing here is intentionally duplicated from the equivalent logic in
wrapper.py (`_metrics_sidecar` / `_check_oom`), because wrapper.py is built by
f-string templating and cannot import from this module. Keep the two impls
behaviorally equivalent; if you change one, change the other.

Layout:
- _cgroup.py:           host-side cgroup parsing helpers
- _docker_inspect.py:   docker inspect wrappers (PID + NVIDIA_VISIBLE_DEVICES)
- _nvidia_smi.py:       node-wide GPU sampler
- _collector.py:        DockerMetricsCollector + per-job state

公共面：`DockerMetricsCollector`。
"""
import logging

logger = logging.getLogger(__name__)

from ._collector import DockerMetricsCollector

__all__ = ["DockerMetricsCollector"]
