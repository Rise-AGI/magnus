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
from .._magnus_config import magnus_config, is_admin_user
from .._resource_manager import _parse_size_string
from .._scheduler import scheduler
from .._shared_file_manager import shared_file_manager, SharedFileValidationError, SharedFileNotFoundError, SharedFileInvalidatedError
from .auth import get_current_user


logger = logging.getLogger(__name__)
router = APIRouter()


def create_job(
    job_dict: Dict[str, Any],
    user_id: str,
    db: Session,
)-> models.Job:
    """
    共享的 Job 创建逻辑。
    SDK /jobs/submit 和 Blueprint /run 都收敛到这里。
    负责：填充集群默认值、校验资源上限、创建 ORM 对象、写入数据库。
    """
    cluster = magnus_config["cluster"]
    if job_dict.get("cpu_count") is None or job_dict["cpu_count"] == 0:
        job_dict["cpu_count"] = cluster["default_cpu_count"]
    if job_dict.get("memory_demand") is None:
        job_dict["memory_demand"] = cluster["default_memory_demand"]

    # CPU 上限校验
    max_cpu = cluster["max_cpu_count"]
    if job_dict["cpu_count"] > max_cpu:
        raise ValueError(f"cpu_count={job_dict['cpu_count']} exceeds cluster limit ({max_cpu})")

    # 内存上限校验
    max_mem_str = cluster["max_memory_demand"]
    requested_mem = _parse_size_string(job_dict["memory_demand"])
    max_mem = _parse_size_string(max_mem_str)
    if requested_mem > max_mem:
        raise ValueError(f"memory_demand={job_dict['memory_demand']} exceeds cluster limit ({max_mem_str})")
    if job_dict.get("ephemeral_storage") is None:
        job_dict["ephemeral_storage"] = cluster["default_ephemeral_storage"]
    if job_dict.get("runner") is None:
        job_dict["runner"] = cluster["default_runner"]
    if job_dict.get("container_image") is None:
        job_dict["container_image"] = cluster["default_container_image"]
    if job_dict.get("system_entry_command") is None:
        job_dict["system_entry_command"] = cluster["default_system_entry_command"]

    # 兼容空仓库模式：数据库中 namespace/repo_name 仍为 NOT NULL
    # 空仓库时前端/SDK 会传 None，这里统一落库为空字符串
    if job_dict.get("namespace") is None:
        job_dict["namespace"] = ""
    if job_dict.get("repo_name") is None:
        job_dict["repo_name"] = ""

    try:
        normalized_shared_files = shared_file_manager.normalize_shared_files(job_dict.get("shared_files"))
    except SharedFileValidationError as error:
        raise ValueError(str(error))
    except SharedFileNotFoundError as error:
        raise FileNotFoundError(str(error))
    except SharedFileInvalidatedError as error:
        raise RuntimeError(str(error))

    job_dict["shared_files"] = json.dumps(normalized_shared_files, ensure_ascii=True)

    db_job = models.Job(
        **job_dict,
        user_id = user_id,
        status = JobStatus.PREPARING,
    )

    db.add(db_job)
    db.commit()
    db.refresh(db_job)

    return db_job


def _read_marker_file(path: str, max_size: int)-> str:
    """Read a marker file (.magnus_result / .magnus_action), return content or empty string."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_size)
        if os.path.getsize(path) > max_size:
            content += "\n... (Content truncated, please download full file) ..."
        return content
    except Exception as e:
        return f"<Error reading file: {str(e)}>"


@router.post(
    "/jobs/submit",
    response_model =JobResponse,
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
    try:
        db_job = create_job(job_data.model_dump(), current_user.id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return db_job


@router.get(
    "/jobs",
    response_model =PagedJobResponse,
)
def get_jobs(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    creator_id: Optional[str] = None,
    all_users: bool = False,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Job)

    if search:
        safe = search.replace("%", r"\%").replace("_", r"\_")
        search_pattern = f"%{safe}%"
        query = query.filter(
            or_(
                models.Job.task_name.ilike(search_pattern, escape="\\"),
                models.Job.id.ilike(search_pattern, escape="\\"),
            )
        )

    if all_users:
        if creator_id:
            query = query.filter(models.Job.user_id == creator_id)
    else:
        query = query.filter(models.Job.user_id == current_user.id)

    total = query.count()

    jobs = query.order_by(models.Job.created_at.desc())\
            .offset(skip).limit(limit).all()

    return {"total": total, "items": jobs}


@router.get(
    "/jobs/{job_id}",
    response_model =JobResponse,
)
def get_job_detail(
    job_id: str,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):

    MAX_RESULT_PREVIEW_SIZE = 1024 * 1024

    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    workspace = magnus_config['server']['root']

    # Lazy-read result
    if job.result == ".magnus_result":
        result_path = f"{workspace}/workspace/jobs/{job.id}/.magnus_result"
        job.result = _read_marker_file(result_path, MAX_RESULT_PREVIEW_SIZE)

    # Lazy-read action
    if job.action == ".magnus_action":
        action_path = f"{workspace}/workspace/jobs/{job.id}/.magnus_action"
        job.action = _read_marker_file(action_path, MAX_RESULT_PREVIEW_SIZE)

    return job


@router.get("/jobs/{job_id}/result")
def get_job_result(
    job_id: str,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
)-> Optional[str]:
    """文件不存在返回 null，文件存在但为空返回空字符串。"""
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.result is None:
        return None
    if job.result == ".magnus_result":
        workspace = magnus_config['server']['root']
        path = f"{workspace}/workspace/jobs/{job.id}/.magnus_result"
        if not os.path.exists(path):
            return None
        return _read_marker_file(path, 1024 * 1024)
    return job.result


@router.get("/jobs/{job_id}/action")
def get_job_action(
    job_id: str,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
)-> Optional[str]:
    """文件不存在返回 null，文件存在但为空返回空字符串。"""
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.action is None:
        return None
    if job.action == ".magnus_action":
        workspace = magnus_config['server']['root']
        path = f"{workspace}/workspace/jobs/{job.id}/.magnus_action"
        if not os.path.exists(path):
            return None
        return _read_marker_file(path, 1024 * 1024)
    return job.action


def _safe_utf8_truncate(data: bytes)-> bytes:
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
    _: models.User = Depends(get_current_user),
)-> Dict[str, Any]:
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
                if job.result and job.result != ".magnus_result":
                    msg = f"[System] {job.result}\n"
                else:
                    msg = "Job failed. No output was produced.\n"
            elif job.status in [JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING]:
                msg = "Waiting for output stream...\n"
            elif job.status == JobStatus.PREPARING:
                msg = "Preparing resources...\n"
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

    if job.user_id != current_user.id and not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Not authorized to terminate this job")

    try:
        scheduler.terminate_job(db, job)
    except Exception as e:
        logger.error(f"Error terminating job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to terminate job")

    db.refresh(job)

    return {"message": "Job terminated", "status": job.status}
