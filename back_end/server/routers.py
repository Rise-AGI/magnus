# back_end/server/routers.py
import os
import jwt
import logging
import asyncio
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import or_

from library import *
from . import database
from . import models
from .models import JobStatus, JobType
from .schemas import *
from ._github_client import github_client
from ._jwt_signer import jwt_signer
from ._feishu_client import feishu_client
from ._magnus_config import magnus_config
from ._scheduler import scheduler
from ._slurm_manager import SlurmManager


__all__ = [
    "router",
]


logger = logging.getLogger(__name__)
router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/feishu/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: Session = Depends(database.get_db)
) -> models.User:
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(
            token, 
            magnus_config["server"]["jwt_signer"]["secret_key"], 
            algorithms=[magnus_config["server"]["jwt_signer"]["algorithm"]]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
        
    return user


# ================= GitHub Routes =================


@router.get("/github/{ns}/{repo}/branches")
async def get_branches(ns: str, repo: str):
    branches = await github_client.fetch_branches(ns, repo)
    if not branches:
        raise HTTPException(
            status_code=404, 
            detail = "Repo not found or empty",
        )
    return branches


@router.get("/github/{ns}/{repo}/commits")
async def get_commits(
    ns: str, 
    repo: str, 
    branch: str,
):
    return await github_client.fetch_commits(ns, repo, branch)


# ================= Job Routes =================


@router.post(
    "/jobs/submit", 
    response_model = JobResponse,
)
async def submit_job(
    job_data: JobSubmission, 
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
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
        status=JobStatus.PENDING 
    )
    
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    return db_job


@router.get(
    "/jobs", 
    response_model = PagedJobResponse,
)
async def get_jobs(
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
                models.Job.id.ilike(search_pattern)
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
    response_model = JobResponse,
)
async def get_job_detail(
    job_id: str,
    db: Session = Depends(database.get_db),
):
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get(
    "/jobs/{job_id}/logs"
)
async def get_job_logs(
    job_id: str,
    db: Session = Depends(database.get_db),
):
    """
    获取任务实时日志。
    直接读取文件系统中 Slurm 生成的 output.txt，而非查库。
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    root_path = magnus_config['server']['root']
    log_path = f"{root_path}/workspace/jobs/{job_id}/slurm/output.txt"

    if not os.path.exists(log_path):
        return {"logs": "Waiting for output stream... (Job might be PENDING or Initializing)"}

    try:
        # 使用 errors="replace" 防止二进制数据或编码错误导致 API 崩溃
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"logs": content}
        
    except Exception as e:
        logger.error(f"Error reading log file for {job_id}: {e}")
        return {"logs": f"Error reading logs: {str(e)}"}


@router.post("/jobs/{job_id}/terminate")
async def terminate_job(
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
        await asyncio.to_thread(
            scheduler.terminate_job,
            db, job,
        )
    except Exception as e:
        logger.error(f"Error terminating job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to terminate job")

    db.refresh(job)
    
    return {"message": "Job terminated", "status": job.status}


# ================= Cluster & Dashboard Routes =================


def _scheduler_sort_key(job):
    """
    复刻调度器排序逻辑：优先级 (A1>A2>B1>B2) > 时间 (FIFO)
    保持此逻辑与 _scheduler.py 一致至关重要，否则前端展示顺序会误导用户
    """
    priority_map = {
        JobType.A1: 4, JobType.A2: 3,
        JobType.B1: 2, JobType.B2: 1,
    }
    p_score = priority_map.get(job.job_type, 0)
    return (p_score, -job.created_at.timestamp())


@router.get(
    "/cluster/stats",
    response_model = ClusterStatsResponse,
)
async def get_cluster_stats(
    db: Session = Depends(database.get_db),
):
    
    # --- 1. 获取 Slurm 真理 (Absolute Truth) ---
    # 核心原则：Running 列表必须完全由 Slurm 决定，解决手动 scancel 后数据库状态滞后的 Bug
    slurm_manager = SlurmManager()
    all_slurm_tasks = slurm_manager.get_all_running_tasks()
    
    running_slurm_ids = [task["id"] for task in all_slurm_tasks]

    # --- 2. 数据库元数据匹配 ---
    # 拿着 Slurm 的名单去数据库里"认领"任务 (Enrichment)
    magnus_jobs_orm = []
    if running_slurm_ids:
        magnus_jobs_orm = db.query(models.Job).filter(
            models.Job.slurm_job_id.in_(running_slurm_ids)
        ).all()
    
    magnus_job_map = {job.slurm_job_id: job for job in magnus_jobs_orm}

    # --- 3. 构造最终列表 ---
    final_running_jobs: List[JobResponse] = []
    
    for task in all_slurm_tasks:
        slurm_id = task["id"]
        
        if slurm_id in magnus_job_map:
            # Case A: Magnus 任务
            # 使用数据库元数据，但强制状态为 Running (以 Slurm 为准)
            job_orm = magnus_job_map[slurm_id]
            job_resp = JobResponse.model_validate(job_orm)
            job_resp.status = JobStatus.RUNNING
            final_running_jobs.append(job_resp)
            
        else:
            # Case B: External 任务
            # 现场 Mock 对象，绝不入库
            mock_user = UserInfo(
                id = f"slurm_{task['user']}",
                name = f"{task['user']} (slurm)",
                avatar_url = "/images/slurm_avatar.png",
                email = None,
            )
            
            try:
                start_dt = datetime.fromisoformat(task["start_time"])
            except ValueError:
                start_dt = datetime.now()

            mock_job = JobResponse(
                task_name = task["name"],
                description = "External slurm task",
                namespace = "External",
                repo_name = "N/A",
                branch = "N/A",
                commit_sha = "N/A",
                entry_command = "<binary execution>",
                gpu_type = task["gpu_type"].lower().replace(" ", ""),
                gpu_count = task["gpu_count"],
                job_type = JobType.EXTERNAL,
                
                id = f"{slurm_id} (slurm)",
                user_id = mock_user.id,
                status = JobStatus.RUNNING,
                slurm_job_id = slurm_id,
                start_time = start_dt,
                created_at = start_dt,
                user = mock_user,
            )
            final_running_jobs.append(mock_job)

    # --- 4. 排序 ---
    # 规则: Magnus 任务在上，External 任务在下；各组内按时间倒序
    magnus_group = [j for j in final_running_jobs if j.job_type != JobType.EXTERNAL]
    external_group = [j for j in final_running_jobs if j.job_type == JobType.EXTERNAL]
    
    magnus_group.sort(key = lambda x: x.start_time or datetime.min, reverse = True)
    external_group.sort(key = lambda x: x.start_time or datetime.min, reverse = True)
    
    sorted_running_jobs = magnus_group + external_group

    # --- 5. Pending Jobs ---
    # Pending 状态依然以数据库为准，配合 Scheduler 的调度逻辑
    pending_jobs_orm = db.query(models.Job).filter(
        models.Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
    ).all()
    
    pending_jobs_orm.sort(key = _scheduler_sort_key, reverse = True)
    pending_jobs = [JobResponse.model_validate(job) for job in pending_jobs_orm]

    # --- 6. 资源计算 ---
    n1_free = slurm_manager.get_cluster_free_gpus()
    n2_used = sum(job.gpu_count for job in sorted_running_jobs)
    display_total = n1_free + n2_used

    return {
        "resources": {
            "node": "liustation",
            "gpu_model": "RTX 5090",
            "total": display_total,
            "free": n1_free,
            "used": n2_used,
        },
        "running_jobs": sorted_running_jobs,
        "pending_jobs": pending_jobs,
    }


@router.get("/dashboard/my-active-jobs", response_model=List[JobResponse])
async def get_my_active_jobs(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Dashboard 专用：获取当前用户“活跃”的任务
    逻辑：Running 在顶端(按开始时间)，Pending/Paused 在下方(按调度优先级)
    """
    
    # 1. 获取 Running 任务
    running_orm = db.query(models.Job).filter(
        models.Job.user_id == current_user.id,
        models.Job.status == JobStatus.RUNNING
    ).order_by(models.Job.start_time.desc()).all()
    
    # 2. 获取排队任务
    queued_orm = db.query(models.Job).filter(
        models.Job.user_id == current_user.id,
        models.Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
    ).all()
    
    # 3. 对排队任务应用调度排序
    queued_orm.sort(key=_scheduler_sort_key, reverse=True)
    
    # 4. 合并并强制序列化 (User 懒加载)
    all_jobs_orm = running_orm + queued_orm
    
    return [JobResponse.model_validate(job) for job in all_jobs_orm]


@router.get("/dashboard/stats")
async def get_dashboard_stats(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """
    Dashboard 统计接口:
    - Occupancy (占有率): 真实数据 (基于 ClusterSnapshot 历史平均)
    - Utilization (利用率): Mock 数据 (因为没有 DCGM 监控)
    """
    now = datetime.utcnow()
    
    # --- 逻辑 A: 计算真实的 Occupancy (Allocation) ---
    def get_real_occupancy(hours: int) -> float:
        start_time = now - timedelta(hours=hours)
        snapshots = db.query(models.ClusterSnapshot).filter(
            models.ClusterSnapshot.timestamp >= start_time
        ).all()
        
        if not snapshots: return 0.0
        
        total_cap = sum(s.total_gpus for s in snapshots)
        total_used = sum(s.slurm_used_gpus for s in snapshots)
        
        return (total_used / total_cap) if total_cap > 0 else 0.0

    # --- 逻辑 B: Mock 利用率 ---
    # 模拟一个 30% ~ 60% 之间的随机负载
    mock_util_24h = 0.45 + random.uniform(-0.05, 0.05)
    mock_util_7d  = 0.38
    
    return {
        "total_occupancy_24h": get_real_occupancy(24),
        "total_occupancy_7d": get_real_occupancy(24 * 7),
        
        "magnus_utilization_24h": mock_util_24h,
        "magnus_utilization_7d": mock_util_7d,
    }


# ================= Auth Routes =================


@router.post(
    "/auth/feishu/login",
    response_model=LoginResponse,
)
async def feishu_login(
    req: FeishuLoginRequest, 
    db: Session = Depends(database.get_db),
):
    try:
        feishu_user = await feishu_client.get_feishu_user(req.code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    open_id = feishu_user.get("open_id") or feishu_user.get("union_id")
    if not open_id:
        raise HTTPException(status_code=400, detail="Missing OpenID")

    db_user = db.query(models.User).filter(models.User.feishu_open_id == open_id).first()

    if not db_user:
        db_user = models.User(
            feishu_open_id=open_id,
            name=feishu_user.get("name", "Unknown"),
            avatar_url=feishu_user.get("avatar_url"),
            email=feishu_user.get("email")
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    else:
        # 更新用户信息
        db_user.name = feishu_user.get("name", db_user.name)
        db_user.avatar_url = feishu_user.get("avatar_url", db_user.avatar_url)
        db.commit()
        db.refresh(db_user)

    access_token = jwt_signer.create_access_token(payload={"sub": db_user.id})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": db_user,
    }


@router.get(
    "/users",
    response_model=List[UserInfo],
)
async def get_users(
    db: Session = Depends(database.get_db),
):
    users = db.query(models.User).order_by(models.User.name).all()
    return users