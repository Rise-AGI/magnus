# back_end/server/routers/cluster.py
from typing import List
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import database
from .. import models
from ..models import JobStatus, JobType
from ..schemas import ClusterStatsResponse, JobResponse, PagedJobResponse, UserInfo
from .auth import get_current_user
from .._magnus_config import magnus_config, is_local_mode
from .._slurm_manager import SlurmManager


_node_name = magnus_config["cluster"]["name"]
_gpu_model = magnus_config["cluster"]["gpus"][0]["label"] if magnus_config["cluster"]["gpus"] else "N/A"


router = APIRouter()


def _get_cluster_stats_local(db: Session, running_skip: int, running_limit: int, pending_skip: int, pending_limit: int):
    """Local 模式的集群统计：只看 Magnus 数据库中的任务，资源取宿主机实际值"""
    import os

    running_jobs_orm = db.query(models.Job).filter(
        models.Job.status.in_([JobStatus.RUNNING, JobStatus.QUEUED])
    ).order_by(models.Job.start_time.desc()).all()

    pending_jobs_orm = db.query(models.Job).filter(
        models.Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
    ).all()
    preparing_jobs_orm = db.query(models.Job).filter(
        models.Job.status == JobStatus.PREPARING
    ).all()

    pending_jobs_orm.sort(key=_scheduler_sort_key, reverse=True)
    preparing_jobs_orm.sort(key=lambda x: x.created_at.timestamp(), reverse=True)
    all_pending = pending_jobs_orm + preparing_jobs_orm

    running_responses = [JobResponse.model_validate(j) for j in running_jobs_orm]
    total_running = len(running_responses)
    paginated_running = running_responses[running_skip:running_skip + running_limit]

    total_pending = len(all_pending)
    paginated_pending = [JobResponse.model_validate(j) for j in all_pending[pending_skip:pending_skip + pending_limit]]

    # 宿主机实际资源（local 模式不做细粒度资源追踪，显示 total = free）
    cpu_total = os.cpu_count() or 0
    try:
        # os.sysconf works on Linux and macOS
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        mem_total_mb = (page_size * total_pages) // (1024 * 1024)
    except (ValueError, AttributeError, OSError):
        # Windows: os.sysconf doesn't exist
        mem_total_mb = 0

    return {
        "resources": {
            "node": _node_name,
            "gpu_model": "Local (Docker)",
            "total": 0,
            "free": 0,
            "used": 0,
            "cpu_total": cpu_total,
            "cpu_free": cpu_total,
            "mem_total_mb": mem_total_mb,
            "mem_free_mb": mem_total_mb,
        },
        "running_jobs": paginated_running,
        "total_running": total_running,
        "pending_jobs": paginated_pending,
        "total_pending": total_pending,
    }


def _scheduler_sort_key(job):
    """
    复刻调度器排序逻辑：优先级 (A1>A2>B1>B2) > 时间 (FIFO)
    保持此逻辑与 _scheduler/_decisions.py 一致至关重要，否则前端展示顺序会误导用户
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
    response_model =ClusterStatsResponse,
)
def get_cluster_stats(
    running_skip: int = 0,
    running_limit: int = 10,
    pending_skip: int = 0,
    pending_limit: int = 10,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
):
    if is_local_mode:
        return _get_cluster_stats_local(db, running_skip, running_limit, pending_skip, pending_limit)

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
            # Case A: Magnus 任务。直接用 DB 真实 status，不强制改写为 RUNNING：
            # slurm_job_id 持有 ⇔ SLURM 端仍占资源 (见 models/_job.py docstring)，
            # 而 SLURM 端占资源 ≠ "用户视角下还在跑"——TERMINATED / PAUSED 状态
            # 的 job 在 SLURM CG (COMPLETING) 阶段也持有 slurm_job_id，强制改成
            # RUNNING 会让用户看到"我刚 cancel 的 job 仍在 RUNNING 30-60s"，比
            # 老 bug "显示成 external" 体感更怪。让真实 status 透传，前端可以
            # 据此呈现 "Terminated (releasing)" 等复合语义。
            job_orm = magnus_job_map[slurm_id]
            job_resp = JobResponse.model_validate(job_orm)
            all_running_jobs.append(job_resp)

        else:
            # Case B: External 任务
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

            # external job 的 cpu / mem 已由 get_all_running_tasks 从 squeue 采到，
            # 一并填入让 cluster 页面与 magnus 原生 job 的资源展示对齐：前端按
            # cpu_count / memory_demand 渲染，三者皆缺时才回落到 "仅 CPU"。
            external_cpu_count = task.get("cpu_count") or None
            external_memory_mb = task.get("memory_mb") or 0
            if external_memory_mb <= 0:
                external_memory_demand = None
            elif external_memory_mb % 1024 == 0:
                external_memory_demand = f"{external_memory_mb // 1024}G"
            else:
                external_memory_demand = f"{external_memory_mb}M"

            mock_job = JobResponse(
                task_name = task["name"],
                description = "External slurm task",
                namespace = "External",
                repo_name = "N/A",
                branch = "N/A",
                commit_sha = "N/A",
                entry_command = "N/A",
                container_image = "N/A",
                gpu_type = task["gpu_type"].lower().replace(" ", ""),
                gpu_count = task["gpu_count"],
                cpu_count = external_cpu_count,
                memory_demand = external_memory_demand,
                job_type = JobType.EXTERNAL,
                id = f"{slurm_id} (slurm)",
                user_id = mock_user.id,
                status = JobStatus.RUNNING,
                slurm_job_id = slurm_id,
                start_time = start_dt,
                created_at = start_dt,
                user = mock_user,
            )
            all_running_jobs.append(mock_job)

    # --- 4. 排序 & 资源计算 (基于全量数据) ---
    magnus_group = [j for j in all_running_jobs if j.job_type != JobType.EXTERNAL]
    external_group = [j for j in all_running_jobs if j.job_type == JobType.EXTERNAL]

    magnus_group.sort(key=lambda x: x.start_time or datetime.min, reverse=True)
    external_group.sort(key=lambda x: x.start_time or datetime.min, reverse=True)

    sorted_all_running = magnus_group + external_group

    # 资源数字全部来自单次 scontrol 快照 + 单次 squeue --json（即 all_slurm_tasks）。
    # 三个 alloc 维度（gpu / cpu / mem）都从 squeue 的 job-level 数字派生，total
    # 取 SLURM 报告的 capacity；两者来源同时刻，保证 total = free + used 且
    # used == sum(running.<dim>)。alloc 不走 scontrol 的 CPUAlloc / AllocMem 是
    # 因为后者在 CG 期间立即清零，跟 squeue 视角下 job 仍占 cpu / mem 矛盾，
    # 详见 _slurm_manager._resource_query.NodeSnapshot docstring。
    node_snap = slurm_manager.get_node_snapshot()
    used_gpus = sum(job.gpu_count for job in sorted_all_running)
    used_cpus = sum(task.get("cpu_count", 0) for task in all_slurm_tasks)
    used_mem_mb = sum(task.get("memory_mb", 0) for task in all_slurm_tasks)
    display_total = max(node_snap.total_gpus, used_gpus)
    free_gpus = max(0, display_total - used_gpus)

    # --- 5. Running 列表分页切片 ---
    total_running = len(sorted_all_running)
    paginated_running = sorted_all_running[running_skip : running_skip + running_limit]

    # --- 6. Pending Jobs 处理与分页 ---
    # 排序：Pending/Queued/Paused 按调度优先级，Preparing 在最后
    # 排除已在 SLURM 中运行的 job，避免因状态延迟导致同一 job 同时出现在两列
    # NOTE: `NULL NOT IN (...)` 在 SQL 里结果是 NULL 而非 TRUE，会把 slurm_job_id 为
    # NULL 的行（未提交到 SLURM 的 PENDING/PREPARING 任务）静默过滤掉；用 or_ 显式
    # 放行 NULL，这些任务显然不可能已在 SLURM 中运行。
    running_job_ids = {job.slurm_job_id for job in magnus_jobs_orm}
    not_running_in_slurm = (
        or_(models.Job.slurm_job_id.is_(None), ~models.Job.slurm_job_id.in_(running_job_ids))
        if running_job_ids else None
    )
    pending_jobs_orm = db.query(models.Job).filter(
        models.Job.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PAUSED]),
        *([not_running_in_slurm] if not_running_in_slurm is not None else []),
    ).all()
    preparing_jobs_orm = db.query(models.Job).filter(
        models.Job.status == JobStatus.PREPARING,
        *([not_running_in_slurm] if not_running_in_slurm is not None else []),
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
            "free": free_gpus,
            "used": used_gpus,
            "cpu_total": node_snap.cpu_total,
            "cpu_free": max(0, node_snap.cpu_total - used_cpus),
            "mem_total_mb": node_snap.mem_total_mb,
            "mem_free_mb": max(0, node_snap.mem_total_mb - used_mem_mb),
        },
        "running_jobs": paginated_running,
        "total_running": total_running,
        "pending_jobs": paginated_pending,
        "total_pending": total_pending,
    }


@router.get(
    "/cluster/my-active-jobs",
    response_model=PagedJobResponse,
)
def get_my_active_jobs(
    skip: int = 0,
    limit: int = 5,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    获取当前用户及其下属"活跃"的任务
    """
    # 收集自己 + 所有下属的 user_id（递归）
    def _collect_descendant_ids(user: models.User) -> List[str]:
        ids = []
        for child in user.children:
            ids.append(child.id)
            ids.extend(_collect_descendant_ids(child))
        return ids

    user_ids = [current_user.id] + _collect_descendant_ids(current_user)

    # 获取 Running 任务
    running_orm = db.query(models.Job).filter(
        models.Job.user_id.in_(user_ids),
        models.Job.status == JobStatus.RUNNING,
    ).order_by(models.Job.start_time.desc()).all()

    # 获取排队任务
    queued_orm = db.query(models.Job).filter(
        models.Job.user_id.in_(user_ids),
        models.Job.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PAUSED]),
    ).all()
    preparing_orm = db.query(models.Job).filter(
        models.Job.user_id.in_(user_ids),
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