# back_end/server/schemas/_cluster.py
"""Cluster resources / stats schemas."""
from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict

from ._job import JobListItem


class ClusterResources(BaseModel):
    node: str
    gpu_model: str
    total: int
    free: int
    used: int
    cpu_total: int
    cpu_free: int
    mem_total_mb: int
    mem_free_mb: int
    gpu_used: Optional[int] = None
    cpu_used: Optional[int] = None
    mem_used_mb: Optional[int] = None
    observed_at: Optional[datetime] = None
    is_stale: bool = False
    free_is_lower_bound: bool = False
    usage_is_upper_bound: bool = False
    allocation_tracked: bool = True
    allocation_inferred: bool = False
    accounting_scope: Literal["partition", "cluster", "local"] = "cluster"
    unavailable_gpus: int = 0
    unavailable_cpus: int = 0
    unavailable_mem_mb: int = 0
    node_count: Optional[int] = None
    model_config = ConfigDict(
        from_attributes=True,
    )


class ClusterStatsResponse(BaseModel):
    resources: ClusterResources
    running_jobs: List[JobListItem]
    total_running: int
    pending_jobs: List[JobListItem]
    total_pending: int
    model_config = ConfigDict(
        from_attributes=True,
    )
