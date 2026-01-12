# back_end/server/routers/jobs.py
import os
import json
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .. import database
from .. import models
from ..models import JobStatus
from ..schemas import JobResponse, JobSubmission, PagedJobResponse
from .._magnus_config import magnus_config
from .._scheduler import scheduler
from .auth import get_current_user


_node_name = magnus_config["cluster"]["name"]


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/jobs/submit",
    response_model=JobResponse,
)
def submit_job(
    job_data: JobSubmission,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    提交新任务。
    注意：此接口只负责将任务写入数据库并标记为 PENDING。
    后续的资源检查、抢占决策和 sbatch 提交由后台 _scheduler.py 负责。
    """
    job_dict = job_data.model_dump()

    db_job = models.Job(
        **job_dict,
        user_id=current_user.id,
        status=JobStatus.PENDING,
    )

    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    return db_job


@router.get(
    "/jobs",
    response_model=PagedJobResponse,
)
def get_jobs(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    db: Session = Depends(database.get_db),
):
    query = db.query(models.Job)

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                models.Job.task_name.ilike(search_pattern),
                models.Job.id.ilike(search_pattern),
            )
        )

    if creator_id and creator_id != "all":
        query = query.filter(models.Job.user_id == creator_id)

    total = query.count()

    jobs = query.order_by(models.Job.created_at.desc())\
            .offset(skip).limit(limit).all()

    return {"total": total, "items": jobs}


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
)
def get_job_detail(
    job_id: str,
    db: Session = Depends(database.get_db),
):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get(
    "/jobs/{job_id}/logs",
)
def get_job_logs(
    job_id: str,
    db: Session = Depends(database.get_db),
):
    """
    获取任务实时日志。
    为了防止内存溢出，限制最大读取 1MB。
    """
    MAX_LOG_SIZE = 1 * 1024 * 1024

    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    root_path = magnus_config['server']['root']
    log_path = f"{root_path}/workspace/jobs/{job_id}/slurm/output.txt"

    if not os.path.exists(log_path):
        if job.status == JobStatus.FAILED:
            effective_user = job.runner if job.runner is not None else "magnus"
            return {
                "logs": (
                    "Job has failed for systematic reasons. "
                    f"Please check if you have access to user {effective_user} on {_node_name}."
                )
            }
        return {"logs": "Waiting for output stream... (Job might be PENDING or Initializing)"}

    try:
        file_size = os.path.getsize(log_path)

        # 情况 A: 文件过大，只读最后 1MB
        if file_size > MAX_LOG_SIZE:
            with open(log_path, "rb") as f:
                f.seek(-MAX_LOG_SIZE, os.SEEK_END)
                content_bytes = f.read()
                content = content_bytes.decode("utf-8", errors="replace")

                header = f"[Magnus Warning] Log file is too large ({file_size/1024/1024:.2f}MB). Showing last 1MB only...\n\n"
                return {"logs": header + content}

        # 情况 B: 文件较小，直接全读
        else:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                return {"logs": f.read()}

    except Exception as e:
        logger.error(f"Error reading log file for {job_id}: {e}")
        return {"logs": f"Error reading logs: {str(e)}"}


@router.get(
    "/jobs/{job_id}/metrics",
)
def get_job_metrics(
    job_id: str,
    db: Session = Depends(database.get_db),
) -> List[Dict[str, Any]]:
    latest_metric: Optional[models.JobMetric] = db.query(models.JobMetric)\
        .filter(models.JobMetric.job_id == job_id)\
        .order_by(models.JobMetric.timestamp.desc())\
        .first()

    if not latest_metric:
        return []

    try:
        metrics: List[Dict[str, Any]] = json.loads(latest_metric.status_json)
        formatted_metrics = [
            {
                "index": m.get("index"),
                "utilization": m.get("utilization_gpu", 0),
            }
            for m in metrics
        ]
        return formatted_metrics

    except Exception as e:
        logger.error(f"Failed to parse metrics for job {job_id}: {e}")
        return []


@router.post("/jobs/{job_id}/terminate")
def terminate_job(
    job_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    用户主动终止任务。
    调用 Scheduler 的 terminate_job 方法以确保资源清理和状态更新的一致性。
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to terminate this job")

    try:
        scheduler.terminate_job(db, job)
    except Exception as e:
        logger.error(f"Error terminating job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to terminate job")

    db.refresh(job)

    return {"message": "Job terminated", "status": job.status}