# back_end/server/schemas/_cluster.py
"""Cluster resources / stats schemas."""
from typing import List
from pydantic import BaseModel

from ._job import JobResponse


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
    class Config: from_attributes = True


class ClusterStatsResponse(BaseModel):
    resources: ClusterResources
    running_jobs: List[JobResponse]
    total_running: int
    pending_jobs: List[JobResponse]
    total_pending: int
    class Config: from_attributes = True
