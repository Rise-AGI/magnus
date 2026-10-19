"""Pure resource display arithmetic; no configuration, database or runtime I/O."""
from collections.abc import Mapping
from typing import Any, Literal

from ._size_utils import _parse_size_string


ResourceMode = Literal["slurm_per_cpu", "slurm_per_node", "local"]
RESOURCE_MODES = frozenset({"slurm_per_cpu", "slurm_per_node", "local"})


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
    raw = value.strip()
    # sbatch --mem uses MiB for unsuffixed numbers. Local metadata follows the
    # common size parser's byte convention and is not an enforced Docker limit.
    if mode != "local" and raw[-1].isdigit():
        raw += "M"
    try:
        size = _parse_size_string(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    # Slurm --mem=0 requests all memory on a node, not zero bytes. The amount
    # cannot be known until allocation; missing/malformed old values are unknown.
    return size if size > 0 else None


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
    return {
        "basis": "allocation",
        "mode": mode,
        "mode_inferred": False,
        "cpu_count": _positive_int(task.get("cpu_count")),
        "memory_bytes": memory_mb * 1024**2 if memory_mb is not None else None,
        "gpu_count": _positive_int(task.get("gpu_count")) or 0,
        "node_count": _positive_int(task.get("node_count")),
        # Total ranks do not prove an even layout across nodes. The cluster
        # projection does not currently collect a uniform per-node task count.
        "tasks_per_node": None,
    }
