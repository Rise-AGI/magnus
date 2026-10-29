"""Whole-job accounting from Slurm's running step statistics."""
from decimal import Decimal, InvalidOperation
import math
import re
import subprocess
from subprocess import CalledProcessError, TimeoutExpired
from typing import Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence


_JOB_ID = re.compile(r"[1-9][0-9]*")
_STEP_ID = re.compile(r"(?P<job>[1-9][0-9]*)\.(?:[0-9]+|batch|extern)")
_UNSET_INTEGERS = {2**32 - 2, 2**32 - 1, 2**64 - 2, 2**64 - 1}
_UNSET_CPU_SECONDS = _UNSET_INTEGERS | {(2**64 - 1) // 1000}


class StepMetrics(NamedTuple):
    step_id: str
    cpu_seconds: Optional[float]
    memory_bytes: Optional[int]
    task_count: Optional[int]


class JobMetrics:
    def __init__(
        self,
        job_id: str,
        steps: Optional[Dict[str, StepMetrics]] = None,
    ) -> None:
        self.job_id = job_id
        self.steps = {} if steps is None else steps

    @property
    def cpu_complete(self) -> bool:
        return bool(self.steps) and all(
            step.cpu_seconds is not None for step in self.steps.values()
        )

    @property
    def memory_complete(self) -> bool:
        return bool(self.steps) and all(
            step.memory_bytes is not None for step in self.steps.values()
        )

    @property
    def memory_bytes(self) -> Optional[int]:
        if not self.memory_complete:
            return None
        return sum(step.memory_bytes for step in self.steps.values())


class SlurmAccountingHealth:
    """Report sustained gaps without treating warm-up or a first CPU baseline as failure."""

    def __init__(
        self,
    ) -> None:
        self._unavailable_since: Optional[float] = None
        self._last_report: Optional[float] = None

    def observe(
        self,
        snapshot: JobMetrics,
        allocated_cpus: Optional[int],
        now: float,
    ) -> Optional[str]:
        missing = []
        if not snapshot.steps:
            missing.append("steps")
        else:
            if not snapshot.cpu_complete:
                missing.append("cpu_counters")
            if not snapshot.memory_complete:
                missing.append("memory_counters")
            if any(
                step.task_count is None for step in snapshot.steps.values()
            ):
                missing.append("task_counts")
        if allocated_cpus is None:
            missing.append("cpu_allocation")
        return self._report(
            "missing_" + ",".join(missing) if missing else None,
            now,
        )

    def query_failed(
        self,
        error: Exception,
        now: float,
    ) -> Optional[str]:
        # Command arguments and stderr may contain credentials or user data.
        if isinstance(
            error,
            TimeoutExpired,
        ):
            reason = "query_timeout"
        elif isinstance(
            error,
            CalledProcessError,
        ):
            reason = "query_nonzero_exit"
        elif isinstance(
            error,
            OSError,
        ):
            reason = "query_os_error"
        elif isinstance(
            error,
            ValueError,
        ):
            reason = "invalid_accounting_response"
        else:
            reason = "query_incomplete_or_failed"
        return self._report(
            reason,
            now,
        )

    def _report(
        self,
        reason: Optional[str],
        now: float,
    ) -> Optional[str]:
        if reason is None:
            reported = self._last_report is not None
            self._unavailable_since = None
            self._last_report = None
            return "[Magnus metrics] Slurm accounting recovered" if reported else None
        if self._unavailable_since is None:
            self._unavailable_since = now
        if now - self._unavailable_since < 60.0:
            return None
        if self._last_report is not None and now - self._last_report < 300.0:
            return None
        self._last_report = now
        return "[Magnus metrics] Slurm accounting unavailable: " + reason


def _nonnegative_integer(
    value: str,
) -> Optional[int]:
    if not re.fullmatch(r"[0-9]+", value):
        return None
    number = int(value)
    if number in _UNSET_INTEGERS or number >= 2**64:
        return None
    return number


def _cpu_seconds(
    value: str,
) -> Optional[float]:
    match = re.fullmatch(
        r"(?:(?P<days>[0-9]+)-)?(?P<clock>[0-9]+(?::[0-9]+){0,2}(?:\.[0-9]+)?)",
        value,
    )
    if match is None:
        return None
    components = match["clock"].split(":")
    days = match["days"]
    if days is not None and len(components) != 3:
        return None
    try:
        seconds = Decimal(components[-1])
        if len(components) > 1 and seconds >= 60:
            return None
        if len(components) >= 2:
            minutes = int(components[-2])
            if len(components) == 3 and minutes >= 60:
                return None
            seconds += minutes * 60
        if len(components) == 3:
            hours = int(components[0])
            if days is not None and hours >= 24:
                return None
            seconds += hours * 3600
        if days is not None:
            seconds += int(days) * 86400
    except (InvalidOperation, ValueError):
        return None
    # Older sstat can format an unset millisecond counter as a huge duration.
    if seconds in _UNSET_CPU_SECONDS or seconds >= 2**63:
        return None
    return float(seconds)


def _parse_tres(
    value: str,
) -> Dict[str, str]:
    if not value:
        return {}
    result = {}
    for token in value.split(","):
        key, separator, number = token.partition("=")
        if not separator or not key or key in result:
            raise ValueError("Slurm returned malformed or duplicate accounting TRES")
        result[key] = number.strip()
    return result


def parse_job_metrics(
    output: str,
    job_ids: Sequence[str],
) -> Dict[str, JobMetrics]:
    requested = _validate_job_ids(job_ids)
    jobs = {job_id: JobMetrics(job_id=job_id) for job_id in requested}
    seen = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.strip().split("|")
        if len(fields) != 3:
            raise ValueError("Slurm returned an incomplete step accounting row")
        step_id, tasks_text, tres_text = (field.strip() for field in fields)
        match = _STEP_ID.fullmatch(step_id)
        if match is None or match["job"] not in jobs:
            raise ValueError("Slurm returned an unexpected job or step identity")
        if step_id in seen:
            if seen[step_id] != fields:
                raise ValueError("Slurm returned conflicting step accounting rows")
            continue
        seen[step_id] = fields
        tres = _parse_tres(tres_text)
        # Empty extern steps can expose only node energy, with an invalid
        # AveCPU sentinel. They have no process-accounting sample to add.
        if step_id.endswith(".extern") and set(tres).issubset({"energy"}):
            continue
        jobs[match["job"]].steps[step_id] = StepMetrics(
            step_id=step_id,
            cpu_seconds=_cpu_seconds(tres.get("cpu", "")),
            memory_bytes=_nonnegative_integer(tres.get("mem", "")),
            task_count=_nonnegative_integer(tasks_text),
        )
    return jobs


def _validate_job_ids(
    job_ids: Sequence[str],
) -> List[str]:
    if isinstance(job_ids, (str, bytes)):
        raise ValueError("Job accounting requires a sequence of Slurm job IDs")
    result = []
    for job_id in job_ids:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ValueError("Job accounting requires exact numeric Slurm job IDs")
        if job_id not in result:
            result.append(job_id)
    return result


class SlurmJobMetricsReader:
    def __init__(
        self,
        run: Optional[Callable] = None,
    ) -> None:
        self._run = subprocess.run if run is None else run

    def read(
        self,
        job_id: str,
        *,
        timeout: float = 10.0,
    ) -> JobMetrics:
        requested = _validate_job_ids([job_id])
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Job accounting requires a positive finite timeout")
        result = self._run(
            [
                "sstat",
                "--allsteps",
                f"--jobs={','.join(requested)}",
                "--noheader",
                "--parsable2",
                "--noconvert",
                "--format=JobID,NTasks,TRESUsageInTot",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=True,
            timeout=timeout,
        )
        if result.returncode != 0 or result.stderr.strip():
            # sstat can emit some successful rows even when a node or step
            # cannot be queried. Such a response cannot establish a job total.
            raise RuntimeError("Slurm job accounting query was incomplete")
        return parse_job_metrics(result.stdout, requested)[job_id]


def allocation_cpus(
    environment: Mapping[str, str],
) -> Optional[int]:
    node_counts = []
    for name in ("SLURM_JOB_NUM_NODES", "SLURM_NNODES"):
        if name not in environment:
            continue
        count = _nonnegative_integer(environment[name])
        if count is None or count <= 0:
            return None
        node_counts.append(count)
    if len(set(node_counts)) > 1:
        return None
    expected_nodes = node_counts[0] if node_counts else None
    if "SLURM_JOB_CPUS_PER_NODE" in environment:
        total_cpus = 0
        total_nodes = 0
        for token in environment["SLURM_JOB_CPUS_PER_NODE"].split(","):
            match = re.fullmatch(r"([1-9][0-9]*)(?:\(x([1-9][0-9]*)\))?", token)
            if match is None:
                return None
            cpus = _nonnegative_integer(match[1])
            nodes = _nonnegative_integer(match[2] or "1")
            if cpus is None or nodes is None:
                return None
            total_cpus += cpus * nodes
            total_nodes += nodes
        if expected_nodes is not None and total_nodes != expected_nodes:
            return None
        return total_cpus if total_cpus < 2**64 else None
    # CPUS_ON_NODE can describe only the batch step, even on a single-node job.
    return None


class SlurmJobMetricsSampler:
    def __init__(
        self,
        allocated_cpus: Optional[int],
    ) -> None:
        if allocated_cpus is not None and (
            not isinstance(allocated_cpus, int)
            or isinstance(allocated_cpus, bool)
            or allocated_cpus <= 0
        ):
            raise ValueError("Allocated CPU count must be a positive integer")
        self._allocated_cpus = allocated_cpus
        self.reset()

    def reset(self) -> None:
        self._previous = None

    def sample(
        self,
        snapshot: JobMetrics,
        monotonic_seconds: float,
        time_unix_ms: int,
    ) -> List[Dict]:
        labels = {
            "scope": "job",
            "coverage": "all_steps",
            "source": "slurm_jobacct",
        }
        points = []
        memory = snapshot.memory_bytes
        if memory is not None:
            points.append({
                "name": "system.memory.used_bytes",
                "kind": "gauge",
                "value": memory,
                "time_unix_ms": time_unix_ms,
                "unit": "bytes",
                "labels": dict(labels, memory_basis="slurm_tres_mem"),
            })

        if not math.isfinite(monotonic_seconds) or not snapshot.cpu_complete or any(
            step.task_count is None for step in snapshot.steps.values()
        ):
            self.reset()
            return points

        counters = {
            step_id: (step.cpu_seconds, step.task_count)
            for step_id, step in snapshot.steps.items()
        }
        previous = self._previous
        self._previous = (snapshot.job_id, monotonic_seconds, counters)
        if self._allocated_cpus is None or previous is None:
            return points
        previous_job, previous_time, previous_counters = previous
        elapsed = monotonic_seconds - previous_time
        if previous_job != snapshot.job_id or elapsed <= 0 or counters.keys() != previous_counters.keys():
            return points
        deltas = []
        for step_id, (seconds, tasks) in counters.items():
            previous_seconds, previous_tasks = previous_counters[step_id]
            if tasks != previous_tasks or seconds < previous_seconds:
                return points
            deltas.append(seconds - previous_seconds)
        # Step totals are independent. Differencing their changing sum would
        # turn a completed step into negative CPU use or hide a new step.
        utilization = sum(deltas) / elapsed / self._allocated_cpus * 100
        points.append({
            "name": "system.cpu.utilization",
            "kind": "gauge",
            "value": round(utilization, 4),
            "time_unix_ms": time_unix_ms,
            "unit": "percent",
            "labels": dict(
                labels,
                normalized_by="allocated_cpus",
                normalization_cpus=str(self._allocated_cpus),
            ),
        })
        return points
