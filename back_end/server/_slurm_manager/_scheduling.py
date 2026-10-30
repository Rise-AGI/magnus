"""Bounded Slurm observations; no scheduling, submission, or accounting writes."""

import re
import subprocess
import time
from datetime import datetime, timezone

from ..schemas._scheduling import (
    ClusterSchedulingResponse,
    SchedulerPolicySection,
    SchedulerAssociationUsageSection,
    SchedulerUsage,
)
from ._resource_query import _parse_tres_int, _parse_tres_mem_mb
from ._transport import _Transport
from ._association_usage import parse_association_record, select_association_chain

_ASSOCIATION_FIELDS = (
    "Cluster",
    "Account",
    "User",
    "Partition",
    "ParentName",
    "DefaultQOS",
    "QOS",
    "GrpTRES",
    "MaxTRES",
    "GrpJobs",
    "MaxJobs",
    "GrpSubmitJobs",
    "MaxSubmitJobs",
    "MaxWall",
)
_QOS_FIELDS = (
    "Name",
    "Flags",
    "GrpTRES",
    "MaxTRESPU",
    "MaxTRESPA",
    "MaxTRES",
    "MaxJobsPU",
    "MaxSubmitJobsPU",
    "MaxWall",
)
_QOS_ACCOUNT_FIELDS = ("MaxJobsPA", "MaxSubmitJobsPA")
_PARTITION_FIELDS = frozenset(
    {
        "PartitionName",
        "State",
        "AllowAccounts",
        "DenyAccounts",
        "AllowQos",
        "DenyQos",
        "QoS",
        "Default",
        "DefaultTime",
        "MaxTime",
        "MaxNodes",
        "MinNodes",
        "MaxCPUsPerNode",
        "MaxMemPerCPU",
        "MaxMemPerNode",
    }
)
_EMPTY = {"", "None", "N/A", "Unknown", "UnknownTime", "(null)"}
_PENDING = {"PENDING", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD", "RESV_DEL_HOLD"}
_ALLOCATED = {
    "RUNNING",
    "COMPLETING",
    "CONFIGURING",
    "SUSPENDED",
    "STOPPED",
    "RESIZING",
    "STAGE_OUT",
    "SIGNALING",
    "POWER_UP_NODE",
}
_TERMINAL = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "BOOT_FAIL",
    "OUT_OF_MEMORY",
    "DEADLINE",
}
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@+-]{0,127}\Z")
_RECORD_ID = re.compile(r"[1-9][0-9]*(?:_[0-9]+)?(?:\+[0-9]+)?\Z")


class QueryFailure(RuntimeError):
    """A public error code, never a command, credential, or raw remote output."""


def _name(
    value: str,
) -> str:
    if not isinstance(
        value,
        str,
    ) or not _NAME.fullmatch(value):
        raise QueryFailure("invalid_scheduler_identity")
    return value


def _run(
    transport: _Transport,
    command: list[str],
    deadline: float,
) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise QueryFailure("timeout")
    try:
        result = transport.run(
            ["env", "LC_ALL=C", "TZ=UTC", "SLURM_TIME_FORMAT=standard", *command],
            timeout=min(
                6,
                remaining,
            ),
            check=False,
        )
    except (TimeoutError, subprocess.TimeoutExpired):
        raise QueryFailure("timeout") from None
    except (OSError, subprocess.SubprocessError, RuntimeError):
        raise QueryFailure("transport_error") from None
    if result.returncode:
        raise QueryFailure("command_failed")
    if (
        not isinstance(
            result.stdout,
            str,
        )
        or len(result.stdout) > 2 * 1024 * 1024
    ):
        raise QueryFailure("invalid_output")
    return result.stdout


def _rows(
    output: str,
    width: int,
) -> list[list[str]]:
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) > 4096:
        raise QueryFailure("output_limit")
    rows = [[field.strip() for field in line.split("|")] for line in lines]
    if any(len(row) != width or any(len(field) > 4096 for field in row) for row in rows):
        raise QueryFailure("invalid_output")
    return rows


def _time(
    value: str,
) -> datetime | None:
    if value in _EMPTY:
        return None
    try:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%S",
        )
    except ValueError:
        raise QueryFailure("invalid_scheduler_time") from None
    return parsed.replace(tzinfo=timezone.utc)


def read_job_scheduling(
    transport: _Transport,
    job_id: str,
    slurm_job_id: str,
) -> dict[str, str | datetime | None]:
    observed_at = datetime.now(timezone.utc)
    try:
        if not re.fullmatch(
            r"[1-9][0-9]*",
            slurm_job_id,
        ):
            raise QueryFailure("invalid_slurm_job_id")
        output = _run(
            transport,
            [
                "squeue",
                "--noheader",
                "--all",
                "--local",
                "--states=all",
                f"--jobs={slurm_job_id}",
                "--format=%i|%T|%r|%S|%V|%a|%q|%P|%k",
            ],
            time.monotonic() + 8,
        )
        rows = _rows(
            output,
            9,
        )
        if not rows:
            return {"availability": "not_in_queue", "observed_at": observed_at}
        if len(rows) != 1 or rows[0][0] != slurm_job_id:
            raise QueryFailure("ambiguous_job")
        _, state, reason, start, submitted, account, qos, partition, comment = rows[0]
        submitted_at = _time(
            submitted,
        )
        start_time = _time(
            start,
        )
        if not re.fullmatch(
            r"[A-Z_+]+",
            state,
        ):
            raise QueryFailure("invalid_scheduler_state")
        # A numeric allocation ID can be recycled. Modern submissions carry an
        # exact Magnus identity; a later SubmitTime cannot prove an old binding.
        if comment in _EMPTY:
            return {"availability": "identity_unverified", "observed_at": observed_at}
        matches = re.fullmatch(
            r"magnus:([0-9a-f]{16}):[0-9a-f]{32}",
            comment,
        )
        identity_matches = matches is not None and matches[1] == job_id
        if not identity_matches:
            return {"availability": "identity_mismatch", "observed_at": observed_at}
        return {
            "availability": "ready",
            "observed_at": observed_at,
            "scheduler_state": state,
            "reason": None if reason in _EMPTY else reason,
            "expected_start_time": start_time if state in _PENDING else None,
            "actual_start_time": start_time if state not in _PENDING else None,
            "submitted_at": submitted_at,
            "account": None if account in _EMPTY else account,
            "qos": None if qos in _EMPTY else qos,
            "partition": None if partition in _EMPTY else partition,
        }
    except QueryFailure as error:
        return {"availability": "unavailable", "observed_at": observed_at, "error": str(error)}


def _policy(
    transport: _Transport,
    entity: str,
    filters: list[str],
    fields: tuple[str, ...],
    deadline: float,
    optional_fields: tuple[str, ...] = (),
) -> SchedulerPolicySection:
    observed_at = datetime.now(timezone.utc)
    try:
        columns = fields + optional_fields
        unavailable_fields = []
        try:
            output = _run(
                transport,
                [
                    "sacctmgr",
                    "--noheader",
                    "--parsable2",
                    "show",
                    entity,
                    *filters,
                    "format=" + ",".join(columns),
                ],
                deadline,
            )
        except QueryFailure as error:
            if not optional_fields or str(error) != "command_failed":
                raise
            columns = fields
            unavailable_fields = list(optional_fields)
            output = _run(
                transport,
                [
                    "sacctmgr",
                    "--noheader",
                    "--parsable2",
                    "show",
                    entity,
                    *filters,
                    "format=" + ",".join(columns),
                ],
                deadline,
            )
        rows = [
            dict(
                zip(
                    columns,
                    (None if value in _EMPTY else value for value in row),
                    strict=True,
                )
            )
            for row in _rows(
                output,
                len(columns),
            )
        ]
        return SchedulerPolicySection(
            availability="ready",
            observed_at=observed_at,
            rows=rows,
            unavailable_fields=unavailable_fields,
        )
    except QueryFailure as error:
        return SchedulerPolicySection(
            availability="unavailable",
            observed_at=observed_at,
            error=str(error),
        )


def _partitions(
    transport: _Transport,
    partitions: list[str],
    deadline: float,
) -> SchedulerPolicySection:
    observed_at = datetime.now(timezone.utc)
    try:
        command = ["scontrol", "--all", "--oneliner", "show", "partition"]
        output = _run(
            transport,
            command,
            deadline,
        )
        rows = []
        for line in output.splitlines():
            if not line.strip():
                continue
            values = dict(
                re.findall(
                    r"(?:^|\s)([A-Za-z][A-Za-z0-9]*)=([^\s]*)",
                    line,
                )
            )
            if "PartitionName" not in values or "State" not in values:
                raise QueryFailure("invalid_output")
            if partitions and values["PartitionName"] not in partitions:
                continue
            rows.append({key: value for key, value in values.items() if key in _PARTITION_FIELDS})
        if not rows or len(rows) > 256:
            raise QueryFailure("invalid_output")
        if partitions and {row["PartitionName"] for row in rows} != set(partitions):
            raise QueryFailure("configured_partition_not_visible")
        return SchedulerPolicySection(
            availability="ready",
            observed_at=observed_at,
            rows=rows,
        )
    except QueryFailure as error:
        return SchedulerPolicySection(
            availability="unavailable",
            observed_at=observed_at,
            error=str(error),
        )


def _usage(
    transport: _Transport,
    identity: str,
    deadline: float,
    *,
    account_scope: bool = False,
) -> SchedulerUsage:
    observed_at = datetime.now(timezone.utc)
    scope = "slurm_account" if account_scope else "slurm_user"
    try:
        if account_scope:
            configuration = _run(
                transport,
                ["scontrol", "show", "config"],
                deadline,
            )
            visibility = re.findall(
                r"(?m)^PrivateData\s*=\s*(.*)$",
                configuration,
            )
            if len(visibility) != 1:
                raise QueryFailure("account_visibility_unverified")
            flags = set(visibility[0].strip().lower().split(","))
            if "jobs" in flags or "all" in flags:
                raise QueryFailure("account_visibility_restricted")
            if not flags <= {
                "none",
                "accounts",
                "events",
                "nodes",
                "partitions",
                "reservations",
                "usage",
                "users",
            }:
                raise QueryFailure("account_visibility_unverified")
        selector = "account" if account_scope else "user"
        # Pending array elements can share JobID before separate allocations
        # exist. JobArrayID includes the element index with --array.
        output = _run(
            transport,
            [
                "squeue",
                "--noheader",
                "--all",
                "--local",
                "--array",
                "--states=all",
                f"--{selector}={identity}",
                "--Format=JobArrayID:0|,State:0|,tres-alloc:0",
            ],
            deadline,
        )
        counters = {
            key: 0
            for key in (
                "allocated_records",
                "allocated_cpus",
                "allocated_node_slots",
                "allocated_memory_mb",
                "allocated_gpus",
                "pending_records",
                "pending_cpus",
                "pending_node_slots",
                "pending_memory_mb",
                "pending_gpus",
            )
        }
        seen = set()
        suspended = False
        for identifier, state, tres in _rows(
            output,
            3,
        ):
            if not _RECORD_ID.fullmatch(identifier) or identifier in seen:
                raise QueryFailure("ambiguous_job")
            seen.add(identifier)
            if state in _TERMINAL:
                continue
            if state in _PENDING:
                prefix = "pending"
            elif state in _ALLOCATED:
                prefix = "allocated"
            else:
                raise QueryFailure("unrecognized_allocation_state")
            suspended = suspended or state == "SUSPENDED"
            components = {}
            for item in tres.split(",") if tres not in _EMPTY else ():
                key, separator, value = item.partition("=")
                if not separator or key in components:
                    raise QueryFailure("invalid_resource_count")
                components[key] = value
            gpu_values = {
                key: value
                for key, value in components.items()
                if key == "gres/gpu" or key.startswith("gres/gpu:")
            }
            if any(not value.isdecimal() for value in gpu_values.values()):
                raise QueryFailure("invalid_resource_count")
            gpu_count = (
                int(gpu_values["gres/gpu"])
                if "gres/gpu" in gpu_values
                else sum(int(value) for value in gpu_values.values())
            )
            if "gres/gpu" in gpu_values and gpu_count < sum(
                int(value) for key, value in gpu_values.items() if key != "gres/gpu"
            ):
                raise QueryFailure("invalid_resource_count")
            dimensions = {
                "cpus": _parse_tres_int(
                    tres,
                    "cpu",
                ),
                "node_slots": _parse_tres_int(
                    tres,
                    "node",
                ),
                "memory_mb": _parse_tres_mem_mb(tres),
                "gpus": gpu_count if components else None,
            }
            counters[prefix + "_records"] += 1
            for key, value in dimensions.items():
                name = prefix + "_" + key
                counters[name] = (
                    counters[name] + value
                    if counters[name] is not None and value is not None
                    else None
                )
        return SchedulerUsage(
            availability="ready",
            observed_at=observed_at,
            scope=scope,
            includes_suspended_allocations=suspended,
            **counters,
        )
    except QueryFailure as error:
        return SchedulerUsage(
            availability="unavailable",
            observed_at=observed_at,
            scope=scope,
            error=str(error),
        )


def _association_usage(
    transport: _Transport,
    user: str,
    account: str | None,
    associations: SchedulerPolicySection,
    deadline: float,
) -> SchedulerAssociationUsageSection:
    observed_at = datetime.now(timezone.utc)
    accounts = {account} if account is not None else {
        row["Account"] for row in associations.rows
        if row["User"] == user and row["Account"] is not None
    }
    if not accounts or len(accounts) > 16:
        return SchedulerAssociationUsageSection(
            availability="unavailable",
            observed_at=observed_at,
            error="account_scope_unverified",
        )
    records = {}
    fetched = set()
    chain = select_association_chain(
        [],
        accounts,
    )
    failure = None
    for _ in range(8):
        needed = chain.missing_accounts - fetched
        if not needed:
            break
        requested = sorted(needed)[0]
        try:
            output = _run(
                transport,
                [
                    "scontrol",
                    "--oneliner",
                    "show",
                    "assoc_mgr",
                    "flags=assoc",
                    f"users={_name(user)}",
                    f"accounts={_name(requested)}",
                ],
                deadline,
            )
            row = parse_association_record(
                output,
            )
            fetched.add(requested)
            if row.account != requested or row.user is not None or row.partition is not None:
                raise QueryFailure("account_association_unverified")
            previous = records.get((row.cluster, row.association_id))
            if previous is not None and previous.account != row.account:
                raise ValueError("conflicting_association_identity")
            records[row.cluster, row.association_id] = row
            chain = select_association_chain(
                list(records.values()),
                accounts,
            )
        except QueryFailure as error:
            failure = str(error)
            break
        except ValueError:
            failure = "invalid_association_output"
            break
    if chain.missing_accounts and failure is None:
        failure = "parent_association_unverified"
    failure = failure or chain.error
    return SchedulerAssociationUsageSection(
        availability="ready" if not failure else "partial" if chain.rows else "unavailable",
        observed_at=observed_at,
        rows=chain.rows,
        missing_accounts=sorted(chain.missing_accounts),
        error=failure,
    )


def read_cluster_scheduling(
    transport: _Transport,
    target_id: str,
    user: str | None,
    account: str | None,
    qos: str | None,
    partitions: list[str],
) -> ClusterSchedulingResponse:
    observed_at = datetime.now(timezone.utc)
    deadline = time.monotonic() + 30
    user = _name(
        user
        if user is not None
        else _run(
            transport,
            ["id", "-un"],
            deadline,
        ).strip(),
    )
    filters = [f"account={_name(account)}"] if account is not None else [f"user={user}"]
    usage = _usage(
        transport,
        user,
        deadline,
    )
    account_usage = (
        _usage(
            transport,
            account,
            deadline,
            account_scope=True,
        )
        if account is not None
        else None
    )
    associations = _policy(
        transport,
        "assoc",
        filters,
        _ASSOCIATION_FIELDS,
        deadline,
    )
    # Providers can expose only the user's association and hide account/root
    # limits. Preserve this view without claiming an effective remaining quota.
    associations.rows = [
        row
        for row in associations.rows
        if row["User"] in (user, None) and (account is None or row["Account"] == account)
    ]
    if qos is None:
        defaults = {row["DefaultQOS"] for row in associations.rows if row["DefaultQOS"]}
        if len(defaults) == 1:
            qos = next(iter(defaults))
    partition_limits = _partitions(
        transport,
        partitions,
        deadline,
    )
    qos_names = {qos} if qos is not None else set()
    qos_names.update(
        row["QoS"]
        for row in partition_limits.rows
        if row.get("QoS") not in _EMPTY and row.get("QoS") is not None
    )
    qos_limits = (
        _policy(
            transport,
            "qos",
            ["name=" + ",".join(_name(name) for name in sorted(qos_names))],
            _QOS_FIELDS,
            deadline,
            optional_fields=_QOS_ACCOUNT_FIELDS,
        )
        if qos_names
        else SchedulerPolicySection(availability="not_configured")
    )
    qos_limits.rows = [row for row in qos_limits.rows if row["Name"] in qos_names]
    association_usage = _association_usage(
        transport,
        user,
        account,
        associations,
        deadline,
    )
    sections = (associations, qos_limits, partition_limits, usage, association_usage)
    if account_usage is not None:
        sections = (*sections, account_usage)
    available = any(section.availability in ("ready", "partial") for section in sections)
    return ClusterSchedulingResponse(
        execution_target=target_id,
        backend="slurm",
        availability="ready"
        if all(section.availability == "ready" for section in sections) and not qos_limits.unavailable_fields
        else "partial"
        if available
        else "unavailable",
        observed_at=observed_at,
        scheduler_user=user,
        account=account,
        qos=qos,
        partitions=partitions,
        associations=associations,
        qos_limits=qos_limits,
        partition_limits=partition_limits,
        usage=usage,
        account_usage=account_usage,
        association_usage=association_usage,
    )
