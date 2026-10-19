"""Pure resource contracts and arithmetic; no database or runtime I/O."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ._size_utils import _parse_size_string, parse_memory_bytes


ResourceMode = Literal["slurm_per_cpu", "slurm_per_node", "local"]
RESOURCE_MODES = frozenset({"slurm_per_cpu", "slurm_per_node", "local"})


@dataclass(frozen=True)
class ResourceTarget:
    partition: str | None
    gpu_type: str | None


def resolve_job_resource_target(
    *,
    gpu_count: int,
    gpu_type: str | None,
    config: Mapping[str, Any],
) -> ResourceTarget:
    """Resolve the same partition/GRES target for admission and sbatch.

    Validated config defaults gres_type to the UI value; explicit None selects
    untyped GRES. Preserve the old untyped fallback for raw, unvalidated entries
    missing gres_type, and preserve spelling passed to SLURM.
    """
    partition = config["execution"]["slurm"].get("partition")
    gres_type = None
    if gpu_count > 0 and gpu_type and gpu_type != "cpu":
        entry = next((
            entry for entry in config["cluster"]["gpus"]
            if str(entry.get("value", "")).lower() == gpu_type.lower()
        ), None)
        if entry is None:
            gres_type = gpu_type
        else:
            partition = entry.get("partition") or partition
            gres_type = entry.get("gres_type")
    return ResourceTarget(
        partition = partition,
        gpu_type = gres_type,
    )


def resource_mode_for_execution(
    execution: Mapping[str, Any],
) -> ResourceMode:
    if execution.get("backend") == "local":
        return "local"
    if (execution.get("slurm") or {}).get("mem_mode") == "per_cpu":
        return "slurm_per_cpu"
    return "slurm_per_node"


def _positive_int(
    value: Any,
) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _memory_bytes(
    value: Any,
    mode: ResourceMode,
) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        size = _parse_size_string(value) if mode == "local" else parse_memory_bytes(value)
    except (ValueError, TypeError, OverflowError):
        return None
    # Slurm --mem=0 requests all memory on a node, not zero bytes. The amount
    # cannot be known until allocation; missing/malformed old values are unknown.
    if size <= 0:
        return None
    # SLURM rounds each node/task memory request before nodes are combined.
    return ((size + 1024**2 - 1) // 1024**2) * 1024**2 if mode != "local" else size


def requested_job_resources(
    *,
    cpu_count: Any,
    memory_demand: Any,
    gpu_count: Any,
    node_count: Any,
    tasks_per_node: Any,
    mode: ResourceMode,
    mode_inferred: bool = False,
) -> dict[str, Any]:
    nodes = 1 if mode == "local" else (_positive_int(node_count) or 1)
    tasks = 1 if mode == "local" else (_positive_int(tasks_per_node) or 1)
    cpus = _positive_int(cpu_count)
    memory = _memory_bytes(memory_demand, mode)
    memory_multiplier = nodes * tasks if mode == "slurm_per_cpu" else nodes
    return {
        "basis": "request",
        "mode": mode,
        "mode_inferred": mode_inferred,
        "cpu_count": cpus * nodes * tasks if cpus is not None else None,
        "memory_bytes": memory * memory_multiplier if memory is not None else None,
        "gpu_count": (_positive_int(gpu_count) or 0) * nodes,
        "node_count": nodes,
        "tasks_per_node": tasks,
    }


def allocated_job_resources(
    task: Mapping[str, Any],
    *,
    mode: ResourceMode,
) -> dict[str, Any]:
    """squeue task fields already contain whole-job totals; never multiply them."""
    memory_mb = _positive_int(task.get("memory_mb"))
    cpu_count = _positive_int(task.get("cpu_count"))
    if task.get("memory_mb") == 0 and task.get("memory_source") == "allocated_tres":
        memory_mb = 0
    if task.get("cpu_count") == 0 and task.get("cpu_count_source") in {"allocated_tres", "allocated_cpus"}:
        cpu_count = 0
    return {
        "basis": "allocation",
        "mode": mode,
        "mode_inferred": False,
        "cpu_count": cpu_count,
        "memory_bytes": memory_mb * 1024**2 if memory_mb is not None else None,
        "gpu_count": _positive_int(task.get("gpu_count")) or 0,
        "node_count": _positive_int(task.get("node_count")),
        # Total ranks do not prove an even layout across nodes. The cluster
        # projection does not currently collect a uniform per-node task count.
        "tasks_per_node": None,
        "allocation_inferred": bool(task.get("allocation_inferred", False)),
        "cpu_source": task.get("cpu_count_source"),
        "memory_source": task.get("memory_source"),
        "gpu_source": task.get("gpu_count_source"),
        "slurm_state": task.get("job_state"),
    }
