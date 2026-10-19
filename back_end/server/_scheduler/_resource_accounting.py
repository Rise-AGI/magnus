"""Pure request/allocation arithmetic for conservative SLURM admission.

This module does not predict placement or completion times. A whole-cluster
vector cannot establish whether a multi-node request fits individual nodes.
"""
from collections.abc import Mapping, Sequence
from typing import Any

from library.fundamental.scheduling import BackfillCandidate, ResourceVector

from .._job_resources import (
    ResourceTarget,
    requested_job_resources,
    resolve_job_resource_target,
    resource_mode_for_execution,
)
from .._size_utils import effective_cpu_count_per_cpu


_MIB = 1024**2


def job_resource_target(
    job: Any,
    config: Mapping[str, Any],
) -> ResourceTarget:
    return resolve_job_resource_target(
        gpu_count = job.gpu_count,
        gpu_type = job.gpu_type,
        config = config,
    )


def gpu_dimensions(
    snapshot: Any,
    tasks: Sequence[Mapping[str, Any]],
    jobs: Sequence[Any],
    config: Mapping[str, Any],
) -> tuple[str, ...]:
    types = set(snapshot.gpu_by_type)
    for task in tasks:
        types.update(task.get("gpu_by_type") or {})
    for job in jobs:
        target = job_resource_target(job, config)
        if target.gpu_type is not None:
            types.add(target.gpu_type.lower())
    return tuple(sorted(gpu_type for gpu_type in types if gpu_type != "unknown"))


def job_resource_vector(
    job: Any,
    config: Mapping[str, Any],
    gpu_types: Sequence[str],
) -> ResourceVector | None:
    """Use the *next submission's* mode, not a historical display snapshot.

    None means SLURM must resolve a default or --mem=0. It never means zero
    memory/CPU demand and cannot participate in backfill or preemption.
    """
    execution = config["execution"]
    cpu_count = job.cpu_count
    memory_demand = job.memory_demand
    slurm = execution["slurm"]
    if slurm["mem_mode"] == "per_cpu":
        cpu_count = effective_cpu_count_per_cpu(
            cpu_count = cpu_count,
            memory_demand = memory_demand,
            mem_per_cpu_mb = slurm["mem_per_cpu_mb"],
        )
        if cpu_count > 0:
            memory_demand = f"{cpu_count * slurm['mem_per_cpu_mb']}M"
    request = requested_job_resources(
        cpu_count = cpu_count,
        memory_demand = memory_demand,
        gpu_count = job.gpu_count,
        node_count = job.node_count,
        tasks_per_node = job.tasks_per_node,
        mode = resource_mode_for_execution(execution),
    )
    if request["cpu_count"] is None or request["memory_bytes"] is None:
        return None
    target = job_resource_target(job, config)
    requested_gpu_type = target.gpu_type.lower() if target.gpu_type else None
    return ResourceVector(
        components = (
            request["gpu_count"],
            request["cpu_count"],
            (request["memory_bytes"] + _MIB - 1) // _MIB,
            *(request["gpu_count"] if gpu_type == requested_gpu_type else 0 for gpu_type in gpu_types),
        ),
    )


def allocation_resource_vector(
    task: Mapping[str, Any],
    gpu_types: Sequence[str],
    *,
    charge_unknown_types: bool = False,
) -> ResourceVector:
    """Allocated totals are never multiplied by nodes or tasks again.

    An unknown GPU's model can consume any typed free pool, but killing that
    allocation cannot be credited as recovering a particular model.
    """
    if not task.get("accounting_complete", False):
        raise ValueError("SLURM allocation accounting is incomplete")
    typed = task.get("gpu_by_type") or {}
    unknown = max(0, task["gpu_count"] - sum(
        count for gpu_type, count in typed.items() if gpu_type != "unknown"
    )) if charge_unknown_types else 0
    return ResourceVector(
        components = (
            task["gpu_count"],
            task["cpu_count"],
            task["memory_mb"],
            *(typed.get(gpu_type, 0) + unknown for gpu_type in gpu_types),
        ),
    )


def node_resource_vector(
    nodes: Sequence[Any],
    gpu_types: Sequence[str],
    *,
    for_queue: bool = False,
) -> ResourceVector:
    """Queue eligibility can include healthy nodes reserved for other work."""
    available = [
        node for node in nodes
        if node.schedulable or (for_queue and getattr(node, "hardware_ready", False))
    ]
    return ResourceVector(
        components = (
            sum(sum(node.gpu_by_type.values()) for node in available),
            sum(node.cpu_total for node in available),
            sum(node.mem_total_mb for node in available),
            *(sum(node.gpu_by_type.get(gpu_type, 0) for node in available) for gpu_type in gpu_types),
        ),
    )


def target_nodes(
    nodes: Sequence[Any],
    target: ResourceTarget,
    *,
    for_queue: bool = False,
) -> list[Any]:
    partitions = set(target.partition.split(",")) if target.partition else None
    return [
        node for node in nodes
        if (node.schedulable or (for_queue and getattr(node, "hardware_ready", False))) and (
            partitions is None or partitions.intersection(node.partitions)
        )
    ]


def subtract_allocations(
    total: ResourceVector,
    tasks: Sequence[Mapping[str, Any]],
    gpu_types: Sequence[str],
) -> ResourceVector | None:
    allocated = ResourceVector(components=(0,) * len(total.components))
    for task in tasks:
        allocated = allocated + allocation_resource_vector(
            task,
            gpu_types,
            charge_unknown_types = True,
        )
    # Oversubscription (or uncertain GPU typing) leaves resource debt. Clamping
    # it to zero would falsely credit a victim's entire allocation as free.
    return total - allocated if allocated.fits_within(total) else None


def select_preemption_victims(
    head: ResourceVector,
    total: ResourceVector,
    free: ResourceVector,
    candidates: Sequence[BackfillCandidate[Any]],
) -> list[Any]:
    """Only return a complete, helpful recovery plan; no partial kills.

    Candidates already follow B2/B1 and LIFO order. Their vectors describe
    actual allocations, not their original per-task requests.
    """
    if head.fits_within(free) or not head.fits_within(total):
        return []
    selected = []
    recovered = free
    for candidate in candidates:
        if not any(
            allocated > 0 and have < need
            for allocated, have, need in zip(
                candidate.demand.components,
                recovered.components,
                head.components,
            )
        ):
            continue
        selected.append(candidate)
        recovered = recovered + candidate.demand
        if head.fits_within(recovered):
            # A later victim may have covered an earlier victim's only deficit.
            # Remove redundant kills while preferring earlier low-priority Bs.
            for victim in reversed(selected[:]):
                without = recovered - victim.demand
                if head.fits_within(without):
                    selected.remove(victim)
                    recovered = without
            return [victim.payload for victim in selected]
    return []
