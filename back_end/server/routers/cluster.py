# back_end/server/routers/cluster.py
import random
from typing import List
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import database
from .. import models
from ..models import JobStatus, JobType
from ..schemas import ClusterStatsResponse, JobResponse, DashboardJobsResponse, UserInfo
from .._slurm_manager import SlurmManager
from .auth import get_current_user
from .._magnus_config import magnus_config


_node_name = magnus_config["cluster"]["name"]
_gpu_model = magnus_config["cluster"]["gpus"][0]["label"]


router = APIRouter()


def _scheduler_sort_key(job):
    """
    复刻调度器排序逻辑：优先级 (A1>A2>B1>B2) > 时间 (FIFO)
    保持此逻辑与 _scheduler.py 一致至关重要，否则前端展示顺序会误导用户
    """
    priority_map = {
        JobType.A1: 4,
        JobType.A2: 3,
        JobType.B1: 2,
        JobType.B2: 1,
    }
    p_score = priority_map.get(job.job_type, 0)
    return (p_score, -job.created_at.timestamp())


@router.get(
    "/cluster/stats",
    response_model=ClusterStatsResponse,
)
def get_cluster_stats(
    running_skip: int = 0,
    running_limit: int = 10,
    pending_skip: int = 0,
    pending_limit: int = 10,
    db: Session = Depends(database.get_db),
):
    # --- 1. 获取 Slurm 真实数据 ---
    # SlurmManager 涉及阻塞 Shell 命令，必须在线程池中运行 (def)
    slurm_manager = SlurmManager()
    all_slurm_tasks = slurm_manager.get_all_running_tasks()

    running_slurm_ids = [task["id"] for task in all_slurm_tasks]

    # --- 2. 数据库元数据匹配 ---
    magnus_jobs_orm = []
    if running_slurm_ids:
        magnus_jobs_orm = db.query(models.Job).filter(
            models.Job.slurm_job_id.in_(running_slurm_ids)
        ).all()

    magnus_job_map = {job.slurm_job_id: job for job in magnus_jobs_orm}

    # --- 3. 构造最终列表 ---
    all_running_jobs: List[JobResponse] = []

    for task in all_slurm_tasks:
        slurm_id = task["id"]

        if slurm_id in magnus_job_map:
            # Case A: Magnus 任务
            job_orm = magnus_job_map[slurm_id]
            job_resp = JobResponse.model_validate(job_orm)
            job_resp.status = JobStatus.RUNNING
            all_running_jobs.append(job_resp)

        else:
            # Case B: External 任务
            mock_user = UserInfo(
                id=f"slurm_{task['user']}",
                name=f"{task['user']} (slurm)",
                avatar_url="/images/slurm_avatar.png",
                email=None,
            )

            try:
                start_dt = datetime.fromisoformat(task["start_time"])
            except ValueError:
                start_dt = datetime.now()

            mock_job = JobResponse(
                task_name=task["name"],
                description="External slurm task",
                namespace="External",
                repo_name="N/A",
                branch="N/A",
                commit_sha="N/A",
                entry_command="N/A",
                container_image="N/A",
                gpu_type=task["gpu_type"].lower().replace(" ", ""),
                gpu_count=task["gpu_count"],
                job_type=JobType.EXTERNAL,
                id=f"{slurm_id} (slurm)",
                user_id=mock_user.id,
                status=JobStatus.RUNNING,
                slurm_job_id=slurm_id,
                start_time=start_dt,
                created_at=start_dt,
                user=mock_user,
            )
            all_running_jobs.append(mock_job)

    # --- 4. 排序 & 资源计算 (基于全量数据) ---
    magnus_group = [j for j in all_running_jobs if j.job_type != JobType.EXTERNAL]
    external_group = [j for j in all_running_jobs if j.job_type == JobType.EXTERNAL]

    magnus_group.sort(key=lambda x: x.start_time or datetime.min, reverse=True)
    external_group.sort(key=lambda x: x.start_time or datetime.min, reverse=True)

    sorted_all_running = magnus_group + external_group

    # 计算资源 (必须使用分页前的全量数据)
    n1_free = slurm_manager.get_cluster_free_gpus()
    n2_used = sum(job.gpu_count for job in sorted_all_running)
    display_total = n1_free + n2_used

    # --- 5. Running 列表分页切片 ---
    total_running = len(sorted_all_running)
    paginated_running = sorted_all_running[running_skip : running_skip + running_limit]

    # --- 6. Pending Jobs 处理与分页 ---
    # QUEUED 在前端显示为 Pending，PREPARING 单独显示
    # 排序：Pending/Queued/Paused 按调度优先级，Preparing 在最后
    pending_jobs_orm = db.query(models.Job).filter(
        models.Job.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PAUSED])
    ).all()
    preparing_jobs_orm = db.query(models.Job).filter(
        models.Job.status == JobStatus.PREPARING
    ).all()

    # Pending/Queued/Paused 按调度器排序
    pending_jobs_orm.sort(key=_scheduler_sort_key, reverse=True)
    # Preparing 按创建时间排序
    preparing_jobs_orm.sort(key=lambda x: x.created_at.timestamp(), reverse=True)

    # 合并：Pending 在前，Preparing 在后
    all_pending_jobs_orm = pending_jobs_orm + preparing_jobs_orm

    total_pending = len(all_pending_jobs_orm)
    paginated_pending_orm = all_pending_jobs_orm[pending_skip : pending_skip + pending_limit]

    paginated_pending = [JobResponse.model_validate(job) for job in paginated_pending_orm]

    return {
        "resources": {
            "node": _node_name,
            "gpu_model": _gpu_model,
            "total": display_total,
            "free": n1_free,
            "used": n2_used,
        },
        "running_jobs": paginated_running,
        "total_running": total_running,
        "pending_jobs": paginated_pending,
        "total_pending": total_pending,
    }


@router.get(
    "/dashboard/my-active-jobs",
    response_model=DashboardJobsResponse,
)
def get_my_active_jobs(
    skip: int = 0,
    limit: int = 5,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Dashboard 专用：获取当前用户“活跃”的任务
    """
    # 获取 Running 任务
    running_orm = db.query(models.Job).filter(
        models.Job.user_id == current_user.id,
        models.Job.status == JobStatus.RUNNING,
    ).order_by(models.Job.start_time.desc()).all()

    # 获取排队任务 (QUEUED 在前端显示为 Pending，PREPARING 单独显示)
    queued_orm = db.query(models.Job).filter(
        models.Job.user_id == current_user.id,
        models.Job.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PAUSED]),
    ).all()
    preparing_orm = db.query(models.Job).filter(
        models.Job.user_id == current_user.id,
        models.Job.status == JobStatus.PREPARING,
    ).all()

    # 对排队任务应用调度排序
    queued_orm.sort(key=_scheduler_sort_key, reverse=True)
    # Preparing 按创建时间排序
    preparing_orm.sort(key=lambda x: x.created_at.timestamp(), reverse=True)

    # 合并全量列表：Running > Pending/Queued/Paused > Preparing
    all_jobs_orm = running_orm + queued_orm + preparing_orm

    # 计算总数与切片 (Pagination)
    total_count = len(all_jobs_orm)
    paginated_orm = all_jobs_orm[skip : skip + limit]

    return {
        "items": [JobResponse.model_validate(job) for job in paginated_orm],
        "total": total_count,
    }


@router.get("/dashboard/stats")
def get_dashboard_stats(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    """
    Dashboard 统计接口:
    - Occupancy (占有率): 真实数据 (基于 ClusterSnapshot 历史平均)
    - Utilization (利用率): Mock 数据 (因为没有 DCGM 监控)
    """
    now = datetime.now(timezone.utc)

    # --- 逻辑 A: 计算真实的 Occupancy (Allocation) ---
    def get_real_occupancy(hours: int) -> float:
        start_time = now - timedelta(hours=hours)
        snapshots = db.query(models.ClusterSnapshot).filter(
            models.ClusterSnapshot.timestamp >= start_time
        ).all()

        if not snapshots:
            return 0.0

        total_cap = sum(s.total_gpus for s in snapshots)
        total_used = sum(s.slurm_used_gpus for s in snapshots)

        return (total_used / total_cap) if total_cap > 0 else 0.0

    # --- 逻辑 B: Mock 利用率 ---
    # 模拟一个 30% ~ 60% 之间的随机负载
    mock_util_24h = 0.45 + random.uniform(-0.05, 0.05)
    mock_util_7d = 0.38

    return {
        "total_occupancy_24h": get_real_occupancy(24),
        "total_occupancy_7d": get_real_occupancy(24 * 7),
        "magnus_utilization_24h": mock_util_24h,
        "magnus_utilization_7d": mock_util_7d,
    }