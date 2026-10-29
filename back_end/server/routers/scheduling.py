"""Authenticated scheduler observations; never drive execution reconciliation."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from .. import database, models
from .._execution_targets import (
    ExecutionTarget,
    requested_execution_target,
    select_execution_target,
)
from .._magnus_config import configured_execution_targets, magnus_config
from .._slurm_manager import SlurmManager
from .._slurm_manager._query_cache import QueryBusy, SchedulerQueryCache
from .._slurm_manager._scheduling import (
    QueryFailure,
    read_cluster_scheduling,
    read_job_scheduling,
)
from ..schemas._scheduling import ClusterSchedulingResponse, JobSchedulingResponse
from .auth import get_current_user

router = APIRouter()
_queries = SchedulerQueryCache()


def _submission_partitions(
    config: dict,
) -> list[str]:
    candidates = [config["execution"]["slurm"].get("partition")]
    candidates.extend(
        gpu.get("partition")
        for gpu in config["cluster"].get(
            "gpus",
            [],
        )
    )
    return list(
        dict.fromkeys(
            name.strip()
            for partitions in candidates
            if partitions
            for name in partitions.split(",")
            if name.strip()
        ),
    )


def _observe[T](
    target: ExecutionTarget,
    key: tuple,
    producer: Callable[[], T],
) -> T:
    try:
        return _queries.get(
            (target.id, *key),
            target.slurm_connection_key,
            producer,
        )
    except QueryBusy:
        raise HTTPException(
            status_code=503,
            detail="Scheduler observation in progress; retry shortly",
            headers={"Retry-After": "2"},
        ) from None
    except QueryFailure:
        raise HTTPException(
            status_code=503,
            detail="Scheduler observation is unavailable",
            headers={"Retry-After": "20"},
        ) from None


@router.get(
    "/jobs/{job_id}/scheduling",
    response_model=JobSchedulingResponse,
)
def get_job_scheduling(
    job_id: str,
    response: Response,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
) -> JobSchedulingResponse:
    response.headers["Cache-Control"] = "private, no-store"
    job = (
        db.query(
            models.Job.id,
            models.Job.execution_target,
            models.Job.slurm_job_id,
            models.Job.status,
        )
        .filter(
            models.Job.id == job_id,
        )
        .first()
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )
    backend = magnus_config["execution"]["backend"]
    base = {
        "job_id": job.id,
        "execution_target": job.execution_target
        or next(target.id for target in configured_execution_targets.values() if target.is_default),
        "backend": backend,
        "magnus_status": job.status.value,
        "slurm_job_id": job.slurm_job_id,
    }
    if backend != "slurm":
        return JobSchedulingResponse(
            **base,
            availability="unsupported",
        )
    try:
        target = select_execution_target(
            configured_execution_targets,
            job.execution_target,
        )
    except ValueError:
        return JobSchedulingResponse(
            **base,
            availability="target_retired",
        )
    if not job.slurm_job_id:
        return JobSchedulingResponse(
            **base,
            availability="no_scheduler_id",
        )
    observation = _observe(
        target,
        ("job", job.id, job.slurm_job_id),
        lambda: read_job_scheduling(
            SlurmManager(config=target.config).transport,
            job.id,
            job.slurm_job_id,
        ),
    )
    return JobSchedulingResponse(
        **base,
        **observation,
    )


@router.get(
    "/cluster/scheduling",
    response_model=ClusterSchedulingResponse,
)
def get_cluster_scheduling(
    response: Response,
    execution_target: str | None = None,
    _: models.User = Depends(get_current_user),
    default_target: Annotated[str | None, Header(alias="X-Magnus-Default-Execution-Target")] = None,
) -> ClusterSchedulingResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        target = select_execution_target(
            configured_execution_targets,
            requested_execution_target(
                execution_target,
                default_target,
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from None
    config = target.config
    backend = config["execution"]["backend"]
    if backend != "slurm":
        return ClusterSchedulingResponse(
            execution_target=target.id,
            backend=backend,
            availability="unsupported",
        )
    transport_config = config["transport"]
    user = transport_config["ssh"]["user"] if transport_config["mode"] == "ssh" else None
    slurm = config["execution"]["slurm"]
    return _observe(
        target,
        ("account",),
        lambda: read_cluster_scheduling(
            SlurmManager(config=config).transport,
            target.id,
            user,
            slurm.get("account"),
            slurm.get("qos"),
            _submission_partitions(config),
        ),
    )
