# back_end/server/routers/jobs.py
import os
import json
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .. import database
from .. import models
from ..models import JobStatus
from ..schemas import JobResponse, JobSubmission, PagedJobResponse
from .._magnus_config import magnus_config
from .._scheduler import scheduler
from .auth import get_current_user


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
    注意：此接口只负责将任务写入数据库并标记为 PREPARING。
    后续的资源准备、调度决策和 sbatch 提交由后台 _scheduler.py 负责。
    """
    job_dict = job_data.model_dump()

    # 如果未提供 container_image，使用配置中的默认值
    if job_dict.get("container_image") is None:
        job_dict["container_image"] = magnus_config["cluster"]["default_container_image"]

    db_job = models.Job(
        **job_dict,
        user_id=current_user.id,
        status=JobStatus.PREPARING,
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
    
    MAX_RESULT_PREVIEW_SIZE = 1024 * 1024
    
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # [Magnus Patch] 
    if job.result == ".magnus_result":
        workspace = magnus_config['server']['root']
        result_path = f"{workspace}/workspace/jobs/{job.id}/.magnus_result"
        
        content: str = ""
        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(MAX_RESULT_PREVIEW_SIZE)
                if os.path.getsize(result_path) > MAX_RESULT_PREVIEW_SIZE:
                    content += "\n... (Content truncated, please download full file) ..."
            except Exception as e:
                content = f"<Error reading result: {str(e)}>"
        # 临时覆写 ORM 对象属性，不 commit 到 DB
        job.result = content

    return job


def _safe_utf8_truncate(data: bytes) -> bytes:
    """截断字节流时避免切断 UTF-8 多字节字符"""
    length = len(data)
    if length == 0:
        return data

    for i in range(min(4, length)):
        last_byte = data[length - 1 - i]
        if (last_byte & 0x80) == 0:
            return data
        if (last_byte & 0xC0) == 0xC0:
            return data[:length - 1 - i]
    return data


@router.get("/jobs/{job_id}/logs")
def get_job_logs_paginated(
    job_id: str,
    page: int = Query(default=-1, description="Page number, -1 for last page"),
    db: Session = Depends(database.get_db),
) -> Dict[str, Any]:
    PAGE_SIZE = 200 * 1024
    OVERLAP = 0.3

    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    root_path = magnus_config['server']['root']
    log_path = f"{root_path}/workspace/jobs/{job_id}/slurm/output.txt"
    try:
        if not os.path.exists(log_path):
            msg = ""
            if job.status == JobStatus.FAILED:
                msg = "Job failed. Log file missing.\n"
            elif job.status in [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]:
                msg = "Waiting for output stream...\n"
            return {"logs": msg, "page": 0, "total_pages": 1}

        file_size = os.path.getsize(log_path)

        if file_size == 0:
            return {"logs": "", "page": 0, "total_pages": 1}

        if file_size <= PAGE_SIZE:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                return {"logs": f.read(), "page": 0, "total_pages": 1}

        step = int(PAGE_SIZE * (1 - OVERLAP))
        total_pages = max(1, (file_size - PAGE_SIZE) // step + 2)

        if page < 0:
            page = total_pages - 1
        page = max(0, min(page, total_pages - 1))

        offset = page * step
        read_size = min(PAGE_SIZE, file_size - offset)

        with open(log_path, "rb") as f:
            f.seek(offset)
            chunk = f.read(read_size)
            if offset + len(chunk) < file_size:
                chunk = _safe_utf8_truncate(chunk)

        content = chunk.decode("utf-8", errors="replace")
        return {"logs": content, "page": page, "total_pages": total_pages}

    except Exception as e:
        logger.exception(f"[logs] Error for job={job_id}")
        return {"logs": f"Error reading logs: {str(e)}", "page": 0, "total_pages": 1}


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