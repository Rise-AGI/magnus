# back_end/server/_slurm_manager/_resource_query.py
"""SLURM 资源查询：scontrol/squeue 解析容量、占用、运行任务列表。"""
import json
import re
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from . import logger
from ._transport import _Transport


_HELD_RESOURCE_STATES = (
    "SUSPENDED", "STOPPED", "RESIZING", "CONFIGURING", "POWER_UP_NODE",
    "STAGE_OUT", "COMPLETING", "SIGNALING", "RUNNING",
)


def _slurm_lifecycle_state(
    state: str,
) -> str:
    state = state.strip().upper()
    if re.fullmatch(r"CANCELLED(?: BY [0-9]+|\+)?", state):
        return "FAILED"
    if state in {
        "R", "RUNNING", "CG", "COMPLETING", "S", "SUSPENDED", "ST", "STOPPED",
        "CF", "CONFIGURING", "RS", "RESIZING", "SO", "STAGE_OUT", "SI", "SIGNALING",
        "POWER_UP_NODE", "LAUNCH_FAILED", "RECONFIG_FAIL", "UPDATE_DB",
    }:
        return "RUNNING"
    if state in {
        "PD", "PENDING", "RQ", "REQUEUED", "RF", "REQUEUE_FED", "RH", "REQUEUE_HOLD",
        "RD", "RESV_DEL_HOLD", "SE", "SPECIAL_EXIT", "EXPEDITING",
    }:
        return "PENDING"
    if state in {"CD", "COMPLETED"}:
        return "COMPLETED"
    if state in {
        "F", "FAILED", "CA", "TO", "TIMEOUT", "BF", "BOOT_FAIL", "NF", "NODE_FAIL",
        "OOM", "OUT_OF_MEMORY", "DL", "DEADLINE", "PR", "PREEMPTED",
    }:
        return "FAILED"
    return "UNKNOWN"


def _accounting_job_status(
    output: str,
    slurm_job_id: str,
) -> Optional[str]:
    """Select an allocation incarnation, never a successful/failed child step.

    Submit changes on requeue; --duplicates also includes reused numeric IDs.
    https://slurm.schedmd.com/sacct.html documents both behaviors. Equal-time
    conflicting records cannot establish which incarnation owns the allocation.
    """
    if not output.strip():
        return None
    records = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 5:
            return "UNKNOWN"
        job_id, state, submitted, ended, exit_code = fields
        if job_id != slurm_job_id:
            continue
        try:
            submit_time = datetime.strptime(submitted, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return "UNKNOWN"
        records.append((submit_time, state, ended, exit_code))
    if not records:
        return "UNKNOWN"
    latest_submit = max(record[0] for record in records)
    latest = {record for record in records if record[0] == latest_submit}
    if len(latest) != 1:
        return "UNKNOWN"
    submitted, state, ended, exit_code = latest.pop()
    status = _slurm_lifecycle_state(state)
    if status in {"COMPLETED", "FAILED"}:
        try:
            end_time = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return "UNKNOWN"
        if end_time < submitted or not re.fullmatch(r"[0-9]+:[0-9]+", exit_code):
            return "UNKNOWN"
        if status == "COMPLETED" and exit_code != "0:0":
            return "FAILED"
    return status


def _resolve_default_gpu_model(
    config: dict | None = None,
) -> str:
    """Only infer an untyped allocation's model on a single-model station."""
    try:
        gpus = (config if config is not None else magnus_config).get("cluster", {}).get("gpus") or []
        models = {
            str(gpu["value"]).lower()
            for gpu in gpus
            if isinstance(gpu, dict) and gpu.get("value")
        }
        if len(models) == 1:
            return next(iter(models))
    except Exception:
        pass
    return "unknown"


def _parse_gpu_resources(
    resources: Optional[str],
) -> List[Tuple[Optional[str], int]]:
    """Read GPU counts from GRES or named TRES without interpreting device IDs.

    Slurm annotates each GRES with ``(S:0-1)`` or ``(IDX:0,2)``. Strip those
    groups before splitting commas/colons, preserving the ``(null)`` type
    placeholder in detailed GresUsed. TRES uses ``gres/gpu[:type]=N``;
    GRES/tres_per_node uses ``[gres/]gpu[:type]:N``. Other resources, including
    gpumem/gpuutil, are not GPU counts. Preserve the untyped entry separately:
    in allocated TRES it is the aggregate, not an additional allocation.
    """
    if not resources:
        return []
    entries = []
    for item in re.sub(r"\((?!null\))[^)]*\)", "", resources).split(","):
        parts = item.strip().replace("=", ":").split(":")
        if (
            len(parts) not in (2, 3)
            or parts[0] not in {"gpu", "gres/gpu"}
            or not re.fullmatch(r"[0-9]+", parts[-1])
            or (len(parts) == 3 and not parts[1])
        ):
            continue
        model = parts[1].lower() if len(parts) == 3 and parts[1] != "(null)" else None
        entries.append((model, int(parts[-1])))
    return entries


def _job_gpu_allocation(
    job: Dict,
    default_model: str,
) -> Tuple[int, str]:
    """Return whole-job GPU allocation, preferring allocated TRES over requests.

    ``gres_detail`` contains node allocations; only the last fallback,
    ``tres_per_node``, must be multiplied by the allocated node count. See
    https://slurm.schedmd.com/squeue.html (tres-alloc / tres-per-node).
    """
    allocated = _parse_gpu_resources(job.get("tres_alloc_str"))
    details = job.get("gres_detail")
    if details is None:
        details = []
    if isinstance(details, str):
        details = [details]
    detail_entries = [
        entry
        for detail in details
        for entry in _parse_gpu_resources(detail)
    ]
    per_node = _parse_gpu_resources(job.get("tres_per_node"))
    if allocated:
        aggregate = [count for model, count in allocated if model is None]
        gpu_count = sum(aggregate) if aggregate else sum(count for _, count in allocated)
        entries = allocated
    elif detail_entries:
        gpu_count = sum(count for _, count in detail_entries)
        entries = detail_entries
    else:
        node_count = _unwrap_slurm_int(job.get("node_count")) or 1
        gpu_count = sum(count for _, count in per_node) * node_count
        entries = per_node

    models = {model for model, count in entries if model and count > 0}
    if not models:
        models = {model for model, count in detail_entries or per_node if model and count > 0}
    gpu_type = next(iter(models)) if len(models) == 1 else "mixed" if models else default_model
    return gpu_count, gpu_type


def _gpu_source_valid(
    resources,
) -> bool:
    if resources is None:
        return True
    if not isinstance(resources, str):
        return False
    for token in re.sub(r"\((?!null\))[^)]*\)", "", resources).split(","):
        if re.match(r"^(?:gres/)?gpu(?::|=|$)", token.strip()) and not _parse_gpu_resources(token):
            return False
    return True


def _gpu_accounting_complete(
    job: Dict,
) -> bool:
    allocated = job.get("tres_alloc_str")
    per_node = job.get("tres_per_node")
    details = job.get("gres_detail")
    if details is None:
        details = []
    if isinstance(details, str):
        details = [details]
    if not isinstance(details, list) or not all(isinstance(detail, str) and _gpu_source_valid(detail) for detail in details):
        return False
    if not _gpu_source_valid(allocated) or not _gpu_source_valid(per_node):
        return False
    entries = _parse_gpu_resources(allocated)
    aggregate = [count for model, count in entries if model is None]
    typed = sum(count for model, count in entries if model is not None)
    return not aggregate or (len(aggregate) == 1 and typed <= aggregate[0])


def _slurm_int(
    value,
) -> Optional[int]:
    """Respect Slurm's set/infinite flags; preserve a real zero separately."""
    if isinstance(value, dict):
        if value.get("set") is not True or value.get("infinite") is True:
            return None
        value = value.get("number")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    if value in {2**32 - 1, 2**32 - 2} or value >= 2**63 - 1:
        return None
    return value


def _unwrap_slurm_int(
    value,
) -> int:
    return _slurm_int(value) or 0


def _tres_value(
    tres: Optional[str],
    key: str,
) -> Optional[str]:
    if not isinstance(tres, str):
        return None
    for token in tres.split(","):
        name, separator, value = token.strip().partition("=")
        if separator and name == key:
            return value
    return None


def _parse_tres_mem_mb(
    tres_str: str,
) -> Optional[int]:
    """Allocated memory in MiB; bare TRES memory is MiB, zero is not missing.

    Slurm squeue/sacct --noconvert uses native memory units (MiB). The suffixes
    K/M/G/T/P scale by 1024; round upward at this MiB accounting boundary.
    """
    raw = _tres_value(tres_str, "mem")
    if raw is None or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[KMGTPE]?", raw, re.IGNORECASE):
        return None
    unit = raw[-1].upper() if raw[-1].isalpha() else "M"
    number = raw[:-1] if raw[-1].isalpha() else raw
    try:
        amount = Decimal(number) * Decimal(1024) ** ({"K": -1, "M": 0, "G": 1, "T": 2, "P": 3, "E": 4}[unit])
        return int(amount.to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _parse_tres_int(
    tres: Optional[str],
    key: str,
) -> Optional[int]:
    raw = _tres_value(tres, key)
    return _slurm_int(int(raw)) if raw is not None and raw.isdecimal() else None


def resource_partitions(
    config: Dict,
) -> List[str]:
    """The same tenant partition union is used by live views and history."""
    if config["cluster"]["scheduling"]["mode"] != "tenant":
        return []
    candidates = [config["execution"]["slurm"].get("partition")]
    candidates.extend(gpu.get("partition") for gpu in config["cluster"].get("gpus", []))
    return list(dict.fromkeys(
        name.strip()
        for partitions in candidates if partitions
        for name in partitions.split(",") if name.strip()
    ))


def _node_hardware_ready(
    state: str,
) -> bool:
    parts = state.upper().split("+")
    return parts[0] in {"IDLE", "MIXED", "ALLOCATED", "PLANNED", "RESERVED", "RESV", "BLOCKED", "COMPLETING"} and not any(
        modifier in {"DRAIN", "DRAINING", "DRAINED", "DOWN", "DOWNED", "MAINT", "FAIL", "FAILING",
                     "REBOOT_REQUESTED", "REBOOT_ISSUED", "NOT_RESPONDING", "NO_RESPOND", "POWER_DOWN",
                     "POWERED_DOWN", "POWERING_DOWN", "POWERING_UP", "POWER_UP"}
        for modifier in parts[1:]
    )


def _node_schedulable(
    state: str,
) -> bool:
    # Reservations/backfill plans block immediate availability, but must not
    # prevent submissions from entering Slurm's queue on healthy hardware.
    return _node_hardware_ready(state) and not set(state.upper().split("+")).intersection(
        {"PLANNED", "RESERVED", "RESV", "BLOCKED", "COMPLETING"},
    )


def _gpu_totals_by_type(
    entries: List[Tuple[Optional[str], int]],
    default_model: str,
) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for model, count in entries:
        key = model or default_model
        totals[key] = totals.get(key, 0) + count
    return totals


def _job_gpu_breakdown(
    job: Dict,
    total: int,
    default_model: str,
) -> Dict[str, int]:
    allocated = _parse_gpu_resources(job.get("tres_alloc_str"))
    typed = [(model, count) for model, count in allocated if model is not None]
    if not typed:
        details = job.get("gres_detail") or []
        if isinstance(details, str):
            details = [details]
        typed = [entry for detail in details for entry in _parse_gpu_resources(detail)]
    if not typed:
        nodes = _unwrap_slurm_int(job.get("node_count")) or 1
        typed = [(model, count * nodes) for model, count in _parse_gpu_resources(job.get("tres_per_node"))]
    # UI GPU values can be aliases or deliberately untyped GRES. They cannot
    # establish the physical type used by scheduler placement/preemption.
    counts = _gpu_totals_by_type(typed, "unknown")
    known = sum(counts.values())
    if known > total:
        # Conflicting detail/request data cannot establish the allocation type.
        return {"unknown": total} if total else {}
    if known < total:
        counts["unknown"] = counts.get("unknown", 0) + total - known
    return {model: count for model, count in counts.items() if count}


def _expand_node_names(
    value,
) -> Optional[List[str]]:
    """Expand numeric Slurm hostlists without shell calls; unknown syntax stays unknown."""
    if not isinstance(value, str) or not value or value in {"(null)", "None", "N/A"}:
        return None
    try:
        groups = re.split(r",(?![^\[]*\])", value)
        names = []
        for group in groups:
            pending = [group]
            while any("[" in name for name in pending):
                expanded = []
                for name in pending:
                    match = re.search(r"\[([^\[\]]+)\]", name)
                    if match is None:
                        if "[" in name or "]" in name:
                            return None
                        expanded.append(name)
                        continue
                    replacements = []
                    for span in match.group(1).split(","):
                        bounds = span.split("-")
                        if not all(part.isdecimal() for part in bounds) or len(bounds) not in {1, 2}:
                            return None
                        lower, upper = int(bounds[0]), int(bounds[-1])
                        if lower > upper or upper - lower > 10000:
                            return None
                        width = len(bounds[0]) if bounds[0].startswith("0") else 0
                        replacements.extend(str(number).zfill(width) for number in range(lower, upper + 1))
                        if len(replacements) > 10000:
                            return None
                    expanded.extend(name[:match.start()] + replacement + name[match.end():] for replacement in replacements)
                    if len(expanded) > 10000:
                        return None
                pending = expanded
            if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", name) for name in pending):
                return None
            names.extend(pending)
            if len(names) > 10000:
                return None
        return list(dict.fromkeys(names))
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class NodeCapacity:
    name: str
    partitions: Tuple[str, ...]
    state: str
    cpu_total: int
    mem_total_mb: int
    gpu_by_type: Dict[str, int]
    schedulable: bool
    configured_cpu_total: int = 0
    configured_mem_total_mb: int = 0
    allocated_gpus: Optional[int] = None
    allocated_cpus: Optional[int] = None
    allocated_mem_mb: Optional[int] = None
    hardware_ready: bool = False


@dataclass(frozen=True)
class NodeSnapshot:
    """Configured capacity and the portion unavailable for immediate placement.

    Node allocation describes Slurm's current node accounting. Scheduling also
    checks held job allocations: CPUAlloc/AllocMem can clear during CG.
    Per-node capacities exclude reserved CPU/memory; top-level totals retain
    configured inventory.
    """

    total_gpus: int
    cpu_total: int
    mem_total_mb: int
    node_count: int = 0
    gpu_by_type: Dict[str, int] = field(default_factory=dict)
    unavailable_gpus: int = 0
    unavailable_cpus: int = 0
    unavailable_mem_mb: int = 0
    unavailable_gpu_by_type: Dict[str, int] = field(default_factory=dict)
    nodes: Tuple[NodeCapacity, ...] = ()
    observed_allocated_gpus: Optional[int] = None
    observed_allocated_cpus: Optional[int] = None
    observed_allocated_mem_mb: Optional[int] = None


def validate_allocation_visibility(
    snapshot: NodeSnapshot,
    tasks: List[Dict],
) -> None:
    # PrivateData can hide jobs even with --all. Node allocation is a lower
    # bound (it may clear during CG); never advertise a visibility gap as free.
    for observed, field in (
        (snapshot.observed_allocated_gpus, "gpu_count"),
        (snapshot.observed_allocated_cpus, "cpu_count"),
        (snapshot.observed_allocated_mem_mb, "memory_mb"),
    ):
        if observed is not None and sum(task.get(field, 0) for task in tasks) < observed:
            raise ValueError(f"Visible jobs do not explain node allocation for {field}")


class _ResourceQueryMixin:

    _transport: _Transport

    def _get_capacity_and_usage(self) -> Tuple[int, int]:
        """[Internal] 复用逻辑：获取 (总容量, 当前总占用)。"""
        try:
            # 步骤 1：scontrol 解析 Gres= 行得到 cluster GPU 总容量
            capacity_command = [
                "scontrol",
                "show",
                "node",
                "--future",
            ]
            capacity_result = self._transport.run(
                capacity_command,
                check = True,
                timeout = 30,
            )

            total_capacity = 0
            for line in capacity_result.stdout.split('\n'):
                for token in line.split():
                    if token.startswith("Gres="):
                        total_capacity += sum(
                            count for _, count in _parse_gpu_resources(token[len("Gres="):])
                        )

            # 步骤 2：squeue 解析 RUNNING + COMPLETING 任务的 GPU 申请总和得到当前总占用。
            # COMPLETING (CG) 阶段 SLURM 物理上仍把 GPU 算 Alloc 不释放给新 job，
            # 必须计入 used，否则 cluster snapshot 的 slurm_used_gpus 会在 epilog
            # 跑期间瞬时低估，跟 check_job_status (CG → RUNNING) 的语义错位。
            # Use the same whole-job allocation as cluster display/backfill.
            # squeue %b only reports per-node requests and misses --gpus jobs.
            total_allocated = sum(
                task["gpu_count"]
                for task in self.get_all_running_tasks(_raise_on_error=True)
            )

            return total_capacity, total_allocated

        except Exception as error:
            logger.error(f"Error querying cluster resources: {error}")
            return 0, 0

    def get_node_snapshot(
        self,
        partitions: Optional[List[str]] = None,
        *,
        _raise_on_error: bool = False,
    ) -> NodeSnapshot:
        """Read a deduplicated node inventory for a union of partitions.

        Configured totals include offline nodes. Allocation and admission
        restrictions are separate: a draining node can still hold running jobs.
        """
        partitions = list(dict.fromkeys(
            name.strip()
            for value in partitions or []
            for name in value.split(",") if name.strip()
        ))
        try:
            result = self._transport.run(
                ["scontrol", "show", "node", "--oneliner", "--future", "--details"],
                check = True,
                timeout = 30,
            )
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError("scontrol node query failed")
            inventory = {}
            # Accept multiline records too, so fixtures/older wrappers need not
            # emulate formatting. NodeName is the record boundary, not a line.
            for record in re.split(r"(?=\bNodeName=)", result.stdout):
                fields = dict(token.split("=", 1) for token in record.split() if "=" in token)
                name = fields.get("NodeName")
                if not name:
                    continue
                node_parts = tuple(part for part in fields.get("Partitions", "").split(",") if part and part != "(null)")
                if partitions and not set(partitions).intersection(node_parts):
                    continue
                try:
                    cpus = _slurm_int(int(fields["CPUTot"]))
                    memory = _slurm_int(int(fields["RealMemory"]))
                except (KeyError, ValueError):
                    raise ValueError(f"Node {name} has incomplete capacity") from None
                if cpus is None or memory is None:
                    raise ValueError(f"Node {name} has invalid capacity")
                try:
                    effective_cpus = _slurm_int(int(fields.get("CPUEfctv", cpus)))
                    reserved_memory = _slurm_int(int(fields.get("MemSpecLimit", 0)))
                except (ValueError, TypeError):
                    raise ValueError(f"Node {name} has invalid reserved capacity") from None
                if effective_cpus is None or reserved_memory is None:
                    raise ValueError(f"Node {name} has unknown reserved capacity")
                gres = fields.get("Gres", "")
                parsed_gpus = _parse_gpu_resources(gres)
                if not _gpu_source_valid(gres):
                    raise ValueError(f"Node {name} has invalid GPU capacity")
                gpus = _gpu_totals_by_type(parsed_gpus, "unknown")
                state = fields.get("State", "UNKNOWN")
                allocated = fields.get("AllocTRES")
                gres_used = fields.get("GresUsed")
                if not _gpu_source_valid(allocated) or not _gpu_source_valid(gres_used):
                    raise ValueError(f"Node {name} has invalid allocated GPU data")
                # GPU TRES accounting is optional; omission cannot prove zero usage.
                if _parse_gpu_resources(gres_used):
                    allocated_gpus = _job_gpu_allocation(
                        {"tres_alloc_str": gres_used},
                        "unknown",
                    )[0]
                elif _parse_gpu_resources(allocated):
                    allocated_gpus = _job_gpu_allocation(
                        {"tres_alloc_str": allocated},
                        "unknown",
                    )[0]
                else:
                    allocated_gpus = None if any(gpus.values()) else 0
                allocated_cpus = _parse_tres_int(allocated, "cpu")
                allocated_memory = _parse_tres_mem_mb(allocated or "")
                if allocated_cpus is None and fields.get("CPUAlloc", "").isdecimal():
                    allocated_cpus = _slurm_int(int(fields["CPUAlloc"]))
                if allocated_memory is None and fields.get("AllocMem", "").isdecimal():
                    allocated_memory = _slurm_int(int(fields["AllocMem"]))
                node = NodeCapacity(
                    name = name,
                    partitions = node_parts,
                    state = state,
                    cpu_total = min(cpus, effective_cpus),
                    mem_total_mb = max(0, memory - reserved_memory),
                    gpu_by_type = gpus,
                    schedulable = _node_schedulable(state),
                    configured_cpu_total = cpus,
                    configured_mem_total_mb = memory,
                    allocated_gpus = allocated_gpus,
                    allocated_cpus = allocated_cpus,
                    allocated_mem_mb = allocated_memory,
                    hardware_ready = _node_hardware_ready(state),
                )
                if name in inventory and inventory[name] != node:
                    raise ValueError(f"Conflicting capacity records for node {name}")
                inventory[name] = node
            if not inventory:
                raise ValueError("Node query returned no usable nodes in the requested scope")
            nodes = tuple(inventory.values())
            gpu_by_type = {}
            unavailable_gpu_by_type = {}
            for node in nodes:
                for model, count in node.gpu_by_type.items():
                    gpu_by_type[model] = gpu_by_type.get(model, 0) + count
                    if not node.schedulable:
                        unavailable_gpu_by_type[model] = unavailable_gpu_by_type.get(model, 0) + count
            return NodeSnapshot(
                total_gpus = sum(gpu_by_type.values()),
                cpu_total = sum(node.configured_cpu_total for node in nodes),
                mem_total_mb = sum(node.configured_mem_total_mb for node in nodes),
                node_count = len(nodes),
                gpu_by_type = gpu_by_type,
                unavailable_gpus = sum(unavailable_gpu_by_type.values()),
                unavailable_cpus = sum(node.configured_cpu_total - (node.cpu_total if node.schedulable else 0) for node in nodes),
                unavailable_mem_mb = sum(node.configured_mem_total_mb - (node.mem_total_mb if node.schedulable else 0) for node in nodes),
                unavailable_gpu_by_type = unavailable_gpu_by_type,
                nodes = nodes,
                observed_allocated_gpus = sum(node.allocated_gpus or 0 for node in nodes) if any(node.allocated_gpus is not None for node in nodes) else None,
                observed_allocated_cpus = sum(node.allocated_cpus or 0 for node in nodes) if any(node.allocated_cpus is not None for node in nodes) else None,
                observed_allocated_mem_mb = sum(node.allocated_mem_mb or 0 for node in nodes) if any(node.allocated_mem_mb is not None for node in nodes) else None,
            )
        except Exception as error:
            if _raise_on_error:
                raise
            logger.error(f"Error querying node snapshot: {error}")
            return NodeSnapshot(0, 0, 0)

    def get_partition_snapshot(
        self,
        partition: str,
        *,
        _raise_on_error: bool = False,
    ) -> NodeSnapshot:
        return self.get_node_snapshot(
            partitions = [partition],
            _raise_on_error = _raise_on_error,
        )

    def has_schedulable_node(self) -> bool:
        """Submission readiness, not a promise of immediately available capacity.

        Healthy reserved/allocated nodes can accept queued work. Query failure
        retains the submission path's fail-open policy; accounting uses strict
        queries and must never interpret that uncertainty as free resources.
        """
        try:
            result = self._transport.run(
                [
                    "scontrol",
                    "show",
                    "node",
                    "--oneliner",
                ],
                check = True,
                timeout = 30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning(f"has_schedulable_node fail-open: scontrol failed: {error}")
            return True

        for line in result.stdout.split('\n'):
            line = line.strip()
            if not line:
                continue
            for token in line.split():
                if not token.startswith("State="):
                    continue
                if _node_hardware_ready(token.split('=', 1)[1]):
                    return True
                break

        return False

    def get_cluster_free_gpus(self) -> int:
        cap, alloc = self._get_capacity_and_usage()
        return max(0, cap - alloc)

    def get_resource_snapshot(self) -> Dict:
        cap, alloc = self._get_capacity_and_usage()
        return {
            "total_gpus": cap,
            "slurm_used_gpus": alloc,
        }

    def check_job_status(
        self,
        slurm_job_id: str,
    ) -> str:
        """Prefer controller state, consulting accounting only after eviction."""
        if not isinstance(slurm_job_id, str) or not re.fullmatch(r"[0-9]+", slurm_job_id):
            return "UNKNOWN"
        deadline = time.monotonic() + 30
        command = [
            "env",
            "LC_ALL=C",
            "squeue",
            "-h",
            "--all",
            "--local",
            "--states=all",
            "-j",
            slurm_job_id,
            "-o",
            "%T",
        ]

        try:
            result = self._transport.run(
                command,
                timeout = max(0, deadline - time.monotonic()),
            )
            returncode = getattr(result, "returncode", 0)
            state = result.stdout.strip()
            error = getattr(result, "stderr", "").strip()
            evicted = (
                returncode == 1
                and not state
                and error == "slurm_load_jobs error: Invalid job id specified"
            )
            if (returncode != 0 or error) and not evicted:
                logger.warning(f"Failed to query Slurm job {slurm_job_id}: exit {returncode}")
                return "UNKNOWN"
            if state:
                states = set(state.splitlines())
                if len(states) != 1:
                    return "UNKNOWN"
                visible_state = states.pop()
                # PREEMPTED may still transition back to PENDING under REQUEUE.
                if visible_state in {"PREEMPTED", "PR"}:
                    return "UNKNOWN"
                return _slurm_lifecycle_state(visible_state)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "UNKNOWN"
            result = self._transport.run(
                [
                    "env",
                    "LC_ALL=C",
                    "SLURM_TIME_FORMAT=standard",
                    "sacct",
                    "--allocations",
                    "--allusers",
                    "--local",
                    "--duplicates",
                    "--noheader",
                    "--parsable2",
                    "--jobs",
                    slurm_job_id,
                    "--format=JobIDRaw,State%64,Submit,End,ExitCode",
                ],
                timeout = remaining,
            )
            returncode = getattr(result, "returncode", 0)
            error = getattr(result, "stderr", "").strip()
            if returncode == 1 and not result.stdout.strip() and error.lower() == "slurm accounting storage is disabled":
                return "COMPLETED"
            if returncode != 0 or error:
                logger.warning(f"Failed to query Slurm accounting for job {slurm_job_id}: exit {returncode}")
                return "UNKNOWN"
            status = _accounting_job_status(result.stdout, slurm_job_id)
            if status is not None:
                return status
            # Retain the successful-empty controller contract on legacy sites.
            # An invalid-ID error plus empty accounting can instead be DB lag.
            return "UNKNOWN" if evicted else "COMPLETED"

        except Exception as error:
            logger.error(f"Failed to check job status {slurm_job_id}: {error}")
            return "UNKNOWN"

    def get_all_running_tasks(
        self,
        partition: Optional[str] = None,
        *,
        node_names: Optional[List[str]] = None,
        _raise_on_error: bool = False,
    ) -> List[Dict]:
        """Read allocations still held by jobs, including suspension/cleanup.

        Query all states: flags can hide their base state, while controller
        caching can still return terminal jobs. Filter those records below.
        Node filtering includes competitors submitted through other partitions.
        Strict callers reject incomplete evidence instead of treating it as free.
        """
        default_gpu_model = _resolve_default_gpu_model(self.config)
        if node_names == []:
            return []
        command = [
            "squeue",
            "--states=all",
            "--all",
            "--local",
            "--json",
        ]
        if partition:
            command.append(f"--partition={partition}")
        if node_names:
            command.append(f"--nodelist={','.join(node_names)}")

        try:
            result = self._transport.run(
                command,
                check = True,
                timeout = 30,
            )
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError("squeue query failed")
            data = json.loads(result.stdout)
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), list) or data.get("errors"):
                raise ValueError("squeue returned an incomplete response")

            tasks = []
            seen_jobs = {}
            query_time = datetime.now(timezone.utc).isoformat()

            for job in data.get("jobs", []):
                try:
                    states = job.get("job_state", [])
                    if isinstance(states, str):
                        states = states.split("+")
                    if not isinstance(states, list) or not states or not all(isinstance(state, str) for state in states):
                        raise ValueError("Job query returned an unknown state representation")
                    if not set(_HELD_RESOURCE_STATES).intersection(states):
                        continue
                    job_state = next(state for state in _HELD_RESOURCE_STATES if state in states)

                    job_id = str(job.get("job_id"))
                    if job_id in {"None", ""}:
                        raise ValueError("Running job is missing its ID")
                    user = job.get("user_name")
                    name = job.get("name")

                    # SLURM 21.x JSON 给 int，24+ 给 {"set","infinite","number"}；
                    # _unwrap_slurm_int 兼容两种 schema。下面 cpu / mem / node_count
                    # 同款处理。
                    start_ts = _unwrap_slurm_int(job.get("start_time"))
                    # squeue 给的是 epoch 秒（绝对时刻）。按 UTC 还原成带偏移量的
                    # ISO 串，与 ORM 侧的 UtcDateTime 同一个时间基准；用本地时区还原
                    # 会得到一个不带偏移量的裸时间，消费方只能猜时区。
                    if start_ts:
                        start_time_str = datetime.fromtimestamp(start_ts, timezone.utc).isoformat()
                    else:
                        start_time_str = query_time

                    gpu_count, gpu_type = _job_gpu_allocation(
                        job = job,
                        default_model = default_gpu_model,
                    )

                    # CPU / Mem 占用走 squeue (job-level)，理由见 NodeSnapshot
                    # docstring：CG 期间这两个字段仍报 alloc，跟 GPU 行为对齐。
                    # ``cpus`` 是 job 已分配的逻辑 CPU 总数 (跨节点之和，hyperthread
                    # 已 round-up)。
                    allocated_tres = job.get("tres_alloc_str") or ""
                    cpu_count = _parse_tres_int(allocated_tres, "cpu")
                    cpu_source = "allocated_tres"
                    if cpu_count is None:
                        cpu_count = _slurm_int(job.get("cpus"))
                        cpu_source = "allocated_cpus" if cpu_count is not None else "unknown"

                    # 内存取已分配 TRES (tres_alloc_str 的 mem=)，而非请求值
                    # memory_per_node——后者在 --mem=0 (要走整机内存) 时为哨兵 0，
                    # 会让内存凭空消失、cluster/scheduler 的 free-mem 被高估，详见
                    # _parse_tres_mem_mb docstring。tres_alloc_str 缺失或无 mem=
                    # 时（旧版 SLURM）fallback 回 memory_per_node * node_count。
                    node_count = _parse_tres_int(allocated_tres, "node") or _slurm_int(job.get("node_count"))
                    memory_mb = _parse_tres_mem_mb(allocated_tres)
                    memory_source = "allocated_tres"
                    if memory_mb is None:
                        mem_per_cpu = _slurm_int(job.get("memory_per_cpu"))
                        mem_per_node = _slurm_int(job.get("memory_per_node"))
                        if mem_per_cpu and cpu_count:
                            memory_mb = mem_per_cpu * cpu_count
                            memory_source = "per_cpu_fallback"
                        elif mem_per_node and node_count:
                            memory_mb = mem_per_node * node_count
                            memory_source = "per_node_fallback"
                        else:
                            memory_source = "unknown"
                    gpu_entries = _parse_gpu_resources(allocated_tres)
                    gpu_source = "allocated_tres" if gpu_entries else "node_details" if job.get("gres_detail") else "per_node_fallback" if _parse_gpu_resources(job.get("tres_per_node")) else "none"
                    gpu_complete = _gpu_accounting_complete(job)
                    if gpu_source == "per_node_fallback" and not node_count:
                        gpu_complete = False
                    complete = cpu_count is not None and memory_mb is not None and gpu_complete
                    if _raise_on_error and not complete:
                        raise ValueError(f"Incomplete allocation accounting for Slurm job {job_id}")
                    allocated_nodes = _expand_node_names(job.get("nodes"))
                    scope_is_subset = node_names is None or bool(
                        allocated_nodes and set(allocated_nodes).issubset(node_names)
                    ) or node_count == 1

                    task = {
                        "id": job_id,
                        "user": user,
                        "name": name,
                        "start_time": start_time_str,
                        "gpu_count": gpu_count,
                        "gpu_type": gpu_type,
                        "cpu_count": cpu_count or 0,
                        "memory_mb": memory_mb or 0,
                        "node_count": node_count or None,
                        "partition": job.get("partition"),
                        "node_names": allocated_nodes,
                        "scope_is_subset": scope_is_subset,
                        "gpu_by_type": _job_gpu_breakdown(job, gpu_count, default_gpu_model),
                        "cpu_count_source": cpu_source,
                        "memory_source": memory_source,
                        "gpu_count_source": gpu_source,
                        "accounting_complete": complete,
                        "job_state": job_state,
                        # Suspend releases CPUs, but retains GPU/RAM. Keep the
                        # recorded CPU allocation as an explicit upper bound.
                        # https://slurm.schedmd.com/squeue.html#SECTION_JOB-STATE-CODES
                        "usage_is_upper_bound": job_state == "SUSPENDED",
                        "allocation_inferred": memory_source in {"per_cpu_fallback", "per_node_fallback"} or gpu_source == "per_node_fallback" or job_state == "SUSPENDED",
                    }
                    if job_id in seen_jobs:
                        if seen_jobs[job_id] != task:
                            raise ValueError(f"Conflicting allocation records for Slurm job {job_id}")
                        continue
                    seen_jobs[job_id] = task
                    tasks.append(task)

                except Exception as error:
                    if _raise_on_error:
                        raise
                    logger.warning(
                        f"Failed to parse job json: {error}\n"
                        f"Traceback:\n{traceback.format_exc()}"
                    )
                    continue

            return tasks

        except Exception as error:
            if _raise_on_error:
                raise
            logger.error(f"Failed to query running tasks (json): {error}")
            return []
