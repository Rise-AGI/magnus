"""Read-only observations, separate from Magnus execution state transitions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobSchedulingResponse(BaseModel):
    job_id: str
    execution_target: str
    backend: str
    magnus_status: str
    slurm_job_id: str | None = None
    availability: Literal[
        "ready",
        "no_scheduler_id",
        "not_in_queue",
        "unsupported",
        "unavailable",
        "identity_mismatch",
        "identity_unverified",
        "target_retired",
    ]
    observed_at: datetime | None = None
    reason: str | None = None
    scheduler_state: str | None = None
    expected_start_time: datetime | None = None
    actual_start_time: datetime | None = None
    submitted_at: datetime | None = None
    account: str | None = None
    qos: str | None = None
    partition: str | None = None
    error: str | None = None


class SchedulerPolicySection(BaseModel):
    availability: Literal["ready", "unavailable", "not_configured"]
    observed_at: datetime | None = None
    # Slurm policy fields include TRES and time expressions. Keep their units
    # and inheritance semantics instead of inventing a second policy evaluator.
    rows: list[dict[str, str | None]] = Field(default_factory=list)
    error: str | None = None
    unavailable_fields: list[str] = Field(default_factory=list)


class SchedulerUsage(BaseModel):
    availability: Literal["ready", "unavailable"]
    observed_at: datetime | None = None
    scope: Literal["slurm_user", "slurm_account"] = "slurm_user"
    allocated_memory_mb: int | None = None
    allocated_gpus: int | None = None
    pending_memory_mb: int | None = None
    pending_gpus: int | None = None
    includes_suspended_allocations: bool = False
    allocated_records: int | None = None
    allocated_cpus: int | None = None
    allocated_node_slots: int | None = None
    pending_records: int | None = None
    pending_cpus: int | None = None
    pending_node_slots: int | None = None
    error: str | None = None


class SchedulerLimitUsage(BaseModel):
    limit: int | None = None
    used: int | None = None


class SchedulerAssociationUsage(BaseModel):
    cluster: str
    association_id: int
    account: str
    user: str | None = None
    partition: str | None = None
    parent_account: str | None = None
    parent_id: int | None = None
    group_tres: dict[str, SchedulerLimitUsage] = Field(default_factory=dict)
    job_limits: dict[str, SchedulerLimitUsage] = Field(default_factory=dict)
    unavailable_fields: list[str] = Field(default_factory=list)


class SchedulerAssociationUsageSection(BaseModel):
    availability: Literal["ready", "partial", "unavailable"]
    source: Literal["scontrol_assoc_mgr"] = "scontrol_assoc_mgr"
    observed_at: datetime | None = None
    rows: list[SchedulerAssociationUsage] = Field(default_factory=list)
    missing_accounts: list[str] = Field(default_factory=list)
    error: str | None = None


class ClusterSchedulingResponse(BaseModel):
    execution_target: str
    backend: str
    availability: Literal["ready", "partial", "unavailable", "unsupported"]
    observed_at: datetime | None = None
    scheduler_user: str | None = None
    account: str | None = None
    qos: str | None = None
    partitions: list[str] = Field(default_factory=list)
    associations: SchedulerPolicySection | None = None
    qos_limits: SchedulerPolicySection | None = None
    partition_limits: SchedulerPolicySection | None = None
    usage: SchedulerUsage | None = None
    account_usage: SchedulerUsage | None = None
    association_usage: SchedulerAssociationUsageSection | None = None
    # Neither a visible association nor empty limits proves unrestricted access.
    # Parent/account usage and provider billing can be hidden from this login.
    remaining_resources: None = None
