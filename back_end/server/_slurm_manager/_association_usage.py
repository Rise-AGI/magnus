"""Parse the controller's association counters without evaluating Slurm policy."""

import re
from dataclasses import dataclass

from ..schemas._scheduling import SchedulerAssociationUsage, SchedulerLimitUsage


_NAME = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.@+-]{0,127}\Z",
)
_IDENTITY = re.compile(
    r"([A-Za-z0-9_][A-Za-z0-9_.@+-]{0,127})(?:\(([0-9]+)\))?\Z",
)
_COUNTER = re.compile(
    r"(N|[0-9]{1,20})(?:\(([0-9]{1,20})\))?\Z",
)
_TRES = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_./:+-]{0,127}\Z",
)
_JOB_LIMITS = (
    "GrpJobs",
    "GrpSubmitJobs",
    "GrpJobsAccrue",
    "MaxJobs",
    "MaxSubmitJobs",
    "MaxJobsAccrue",
)


def _identity(
    text: str,
) -> tuple[str | None, int | None]:
    if not text:
        return None, None
    matched = _IDENTITY.fullmatch(text)
    if matched is None:
        raise ValueError("invalid_association_identity")
    return matched[1], int(matched[2]) if matched[2] is not None else None


def _counter(
    text: str,
) -> SchedulerLimitUsage:
    if not text:
        return SchedulerLimitUsage()
    matched = _COUNTER.fullmatch(text)
    if matched is None:
        raise ValueError("invalid_association_counter")
    return SchedulerLimitUsage(
        limit=None if matched[1] == "N" else int(matched[1]),
        used=int(matched[2]) if matched[2] is not None else None,
    )


def parse_association_record(
    output: str,
) -> SchedulerAssociationUsage:
    # Comment is unescaped free text, including newlines that can resemble a
    # second record. A scoped query consumes only the first record before it.
    prefix = re.split(
        r"(?:^|\s)Comment=",
        output,
        maxsplit=1,
    )[0]
    starts = list(re.finditer(
        r"(?m)^ClusterName=",
        prefix,
    ))
    if len(starts) != 1:
        raise ValueError("ambiguous_association_output")
    block = prefix[starts[0].start():]
    if len(block) > 65536:
        raise ValueError("association_output_limit")
    fields = {}
    for key, value in re.findall(
        r"(?:^|\s)([A-Za-z][A-Za-z0-9]*)=([^\s]*)",
        block,
    ):
        if key in fields:
            raise ValueError("duplicate_association_field")
        fields[key] = value
    if not all(key in fields for key in ("ClusterName", "Account", "UserName", "Partition", "ID")):
        raise ValueError("invalid_association_identity")
    cluster, account, partition = (fields[key] for key in ("ClusterName", "Account", "Partition"))
    if (
        not _NAME.fullmatch(cluster)
        or not _NAME.fullmatch(account)
        or (partition and not _NAME.fullmatch(partition))
        or not re.fullmatch(
            r"[1-9][0-9]{0,19}",
            fields["ID"],
        )
    ):
        raise ValueError("invalid_association_identity")
    identifier = int(fields["ID"])
    user, _ = _identity(
        fields["UserName"],
    )
    parent, parent_id = _identity(
        fields.get("ParentAccount", ""),
    )
    missing = [key for key in ("ParentAccount", "GrpTRES", "GrpJobs", "GrpSubmitJobs") if key not in fields]
    tres = {}
    raw_tres = fields.get("GrpTRES", "")
    for entry in raw_tres.split(",") if raw_tres else ():
        name, separator, value = entry.partition("=")
        if not separator or not _TRES.fullmatch(name) or name in tres:
            raise ValueError("invalid_association_tres")
        tres[name] = _counter(
            value,
        )
    if not tres or any(counter.used is None for counter in tres.values()):
        missing.append("GrpTRES.usage")
    jobs = {
        key: _counter(
            fields[key],
        )
        for key in _JOB_LIMITS
        if key in fields
    }
    for key in ("GrpJobs", "GrpSubmitJobs"):
        if key in jobs and jobs[key].used is None:
            missing.append(key + ".usage")
    return SchedulerAssociationUsage(
        cluster=cluster,
        association_id=identifier,
        account=account,
        user=user,
        partition=partition or None,
        parent_account=parent,
        parent_id=parent_id,
        group_tres=tres,
        job_limits=jobs,
        unavailable_fields=missing,
    )


@dataclass
class AssociationChain:
    rows: list[SchedulerAssociationUsage]
    missing_accounts: set[str]
    error: str | None = None


def select_association_chain(
    records: list[SchedulerAssociationUsage],
    accounts: set[str],
) -> AssociationChain:
    selected = {}
    roots = [row for row in records if row.account in accounts and row.user is None]
    missing = accounts - {row.account for row in roots}
    error = None
    for root in roots:
        row = root
        visited = set()
        while row is not None:
            key = row.cluster, row.association_id
            if key in visited:
                error = "association_cycle"
                break
            visited.add(key)
            selected[key] = row
            if row.unavailable_fields and error is None:
                error = "association_fields_unavailable"
            parent = row.parent_account
            if parent is None:
                if row.account != "root":
                    error = "parent_identity_unverified"
                break
            matches = [
                candidate for candidate in records
                if candidate.cluster == row.cluster and candidate.account == parent
                and candidate.user is None and candidate.partition is None
                and (row.parent_id is None or candidate.association_id == row.parent_id)
            ]
            if not matches:
                missing.add(parent)
                break
            if len(matches) != 1:
                error = "ambiguous_parent_association"
                break
            row = matches[0]
    return AssociationChain(
        rows=list(selected.values()),
        missing_accounts=missing,
        error=error,
    )
