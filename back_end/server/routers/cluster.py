# back_end/server/routers/cluster.py
import time
import logging
import os
import threading
from typing import Annotated, Callable, List
from datetime import datetime, timezone

from fastapi import Header, APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import database
from .. import models
from ..models import JobStatus, JobType
from ..schemas import ClusterStatsResponse, JobListItem, JobResourceSummary, PagedJobResponse, UserInfo
from .._job_resources import allocated_job_resources, resource_mode_for_execution
from .auth import get_current_user
from .._magnus_config import magnus_config, is_local_mode, configured_execution_targets
from .._execution_targets import ExecutionTarget, select_execution_target, requested_execution_target
from .._slurm_manager import SlurmManager
from .._slurm_manager._resource_query import resource_partitions


_node_name = magnus_config["cluster"]["name"]



router = APIRouter()
logger = logging.getLogger(
    __name__,
)
_cluster_failure_log_times: dict[str, float] = {}
_cluster_failure_log_lock = threading.Lock()


def _report_cluster_query_failure(
    target_id: str,
    error: Exception,
) -> None:
    # Polling is concurrent; one target outage must not flood the server log.
    now = time.monotonic()
    with _cluster_failure_log_lock:
        previous = _cluster_failure_log_times.get(
            target_id,
        )
        if previous is not None and now - previous < 60:
            return
        _cluster_failure_log_times[target_id] = now

    # Exception strings/commands/traceback locals can contain credentials.
    # Code locations identify validation failures without publishing raw data.
    locations = []
    traceback = error.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        locations.append(
            f"{os.path.basename(code.co_filename)}:{traceback.tb_lineno}:{code.co_name}",
        )
        traceback = traceback.tb_next
    numbers = {}
    for name in ("returncode", "errno", "timeout"):
        value = getattr(
            error,
            name,
            None,
        )
        numbers[name] = value if type(value) in (int, float) else None
    logger.warning(
        "Cluster resource query failed: target=%s error=%s returncode=%s "
        "errno=%s timeout=%s locations=%s",
        target_id,
        type(error).__name__,
        numbers["returncode"],
        numbers["errno"],
        numbers["timeout"],
        " -> ".join(
            locations[-4:],
        ),
    )


# cluster 页面每次 poll 都要现查 SLURM（squeue + scontrol）。本机执行下是本地 subprocess、
# 很快；骑 ssh transport 的远端站点下每次都走 socket 拉一遍（大集群可达 ~MB 级 squeue JSON
# + 数百节点 scontrol），单次就要约 1 秒、并发下更慢。server.scheduler.cluster_stats_cache_ttl
# > 0 时把这些只读远端查询的结果按 TTL 缓存复用；默认 0 = 不缓存、每次现查（独占站点保持
# 现状、字节级不变）。调度器自身的 SLURM 查询不走这里 —— 它要实时数据做决策，缓存只服务
# cluster 页面这条只读链路。
#
# 缓存采用 **single-flight + serve-stale**，这对远端站点是硬要求：cluster 页面高频 poll，
# 若多个请求同时 miss 就会一起打同一个 SSH socket、互相拖慢，且查询耗时一旦超过 TTL / poll
# 间隔，请求就会越堆越多形成雪崩（cache stampede）。所以任一时刻**至多一个远端刷新在飞**
# （_refresh_lock），其余请求**立即返回上一份陈旧值、绝不堆叠打 socket**；冷启动（还没有任何
# 缓存值）时才在锁上短暂等待那唯一的首刷。陈旧度上限约 TTL + 一次查询耗时，对集群总览可接受。
_slurm_view_cache: dict = {}
_slurm_view_cache_lock = threading.Lock()    # 保护 _slurm_view_cache 的快操作
_slurm_view_refresh_locks: dict = {}


def _cached_slurm_view(cache_key, ttl_seconds: int, producer: Callable):
    """TTL 缓存 cluster 页面的远端 SLURM 查询，single-flight + serve-stale。
    ttl_seconds <= 0 时直接 producer()（不缓存，独占站点默认）。

    缓存的对象（task 列表 / NodeSnapshot）下游只读不改、跨请求共享安全。"""
    if ttl_seconds <= 0:
        return producer()

    with _slurm_view_cache_lock:
        cached = _slurm_view_cache.get(cache_key)
        refresh_lock = _slurm_view_refresh_locks.setdefault(cache_key, threading.Lock())
    if cached is not None and time.monotonic() - cached[0] < ttl_seconds:
        return cached[1]

    if cached is not None:
        # 有陈旧值：抢到刷新锁就由我刷新，抢不到（已有刷新在飞）就立即返回陈旧值，
        # 绝不堆叠打 socket —— 这是防雪崩的关键。
        if not refresh_lock.acquire(blocking=False):
            # A slow/stuck refresh must not keep advertising an arbitrarily old
            # allocation. The client can distinguish brief stale reuse from a
            # fresh sample; beyond this bound report unavailable instead.
            if time.monotonic() - cached[0] <= ttl_seconds + 30:
                return cached[1]
            raise RuntimeError("Slurm resource cache exceeded its stale limit")
    else:
        # 冷启动无任何值：在锁上等那唯一的首刷，等到后下面 re-check 直接拿到结果。
        if not refresh_lock.acquire(timeout=30):
            raise TimeoutError("Slurm resource refresh is still in progress")

    try:
        # 拿到刷新锁后 re-check：别的线程可能刚刷新过，避免重复远端查询。
        with _slurm_view_cache_lock:
            cached = _slurm_view_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < ttl_seconds:
            return cached[1]
        value = producer()
        with _slurm_view_cache_lock:
            _slurm_view_cache[cache_key] = (time.monotonic(), value)
        return value
    finally:
        refresh_lock.release()


def _get_cluster_stats_local(db: Session, running_skip: int, running_limit: int, pending_skip: int, pending_limit: int):
    """Local 模式的集群统计：只看 Magnus 数据库中的任务，资源取宿主机实际值"""
    import os

    running_jobs_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.status.in_([JobStatus.RUNNING, JobStatus.QUEUED])
    ).order_by(models.Job.start_time.desc()).all()

    pending_jobs_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
    ).all()
    preparing_jobs_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.status.in_([JobStatus.PREPARING, JobStatus.BUILDING])
    ).all()

    pending_jobs_orm.sort(key=_scheduler_sort_key, reverse=True)
    preparing_jobs_orm.sort(key=lambda x: x.created_at.timestamp(), reverse=True)
    all_pending = pending_jobs_orm + preparing_jobs_orm

    running_responses = [JobListItem.model_validate(j) for j in running_jobs_orm]
    total_running = len(running_responses)
    paginated_running = running_responses[running_skip:running_skip + running_limit]

    total_pending = len(all_pending)
    paginated_pending = [JobListItem.model_validate(j) for j in all_pending[pending_skip:pending_skip + pending_limit]]

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
            "allocation_tracked": False,
            "accounting_scope": "local",
            "observed_at": datetime.now(timezone.utc),
            "node_count": 1,
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


def _prefer_target(
    jobs: list,
    target: ExecutionTarget,
) -> None:
    jobs.sort(key=lambda job: not (
        job.execution_target == target.id
        or (target.is_default and job.execution_target is None)
    ))


def _resolve_target(
    target_id: str | None,
):
    try:
        return select_execution_target(configured_execution_targets, target_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/cluster/targets")
def get_execution_targets(
    _: models.User = Depends(get_current_user),
    default_target: Annotated[str | None, Header(alias="X-Magnus-Default-Execution-Target")] = None,
):
    default_id = _resolve_target(default_target).id
    return [{
        "id": target.id,
        "name": target.name,
        "is_default": target.id == default_id,
        "cluster": {
            **target.config["cluster"],
            "mem_mode": target.config["execution"].get("slurm", {}).get("mem_mode", "explicit"),
            "mem_per_cpu_mb": target.config["execution"].get("slurm", {}).get("mem_per_cpu_mb", 4000),
        },
    } for target in configured_execution_targets.values()]


def _cluster_resource_totals(
    snapshot,
) -> dict:
    # Job rows may cross this node pool or remain in CG after node accounting
    # changes. Resource cards describe the selected nodes, not that job list.
    allocated = {}
    for key, attribute in (
        ("gpu_used", "allocated_gpus"),
        ("cpu_used", "allocated_cpus"),
        ("mem_used_mb", "allocated_mem_mb"),
    ):
        values = [getattr(node, attribute) for node in snapshot.nodes]
        allocated[key] = sum(values) if values and all(value is not None for value in values) else None
    return {
        **allocated,
        "total": snapshot.total_gpus,
        "cpu_total": snapshot.cpu_total,
        "mem_total_mb": snapshot.mem_total_mb,
        # Retain numeric legacy fields; allocation_tracked guards unknown data
        # for old clients. New clients can read each nullable dimension directly.
        "used": allocated["gpu_used"] if allocated["gpu_used"] is not None else 0,
        "free": max(0, snapshot.total_gpus - allocated["gpu_used"]) if allocated["gpu_used"] is not None else 0,
        "cpu_free": max(0, snapshot.cpu_total - allocated["cpu_used"]) if allocated["cpu_used"] is not None else 0,
        "mem_free_mb": max(0, snapshot.mem_total_mb - allocated["mem_used_mb"]) if allocated["mem_used_mb"] is not None else 0,
        "allocation_tracked": all(value is not None for value in allocated.values()),
    }


def _query_cluster_view(
    slurm_manager: SlurmManager,
    partitions: List[str],
):
    snapshot = slurm_manager.get_node_snapshot(
        partitions = partitions or None,
        _raise_on_error = True,
    )
    # A node shared by partitions can be occupied by a job submitted elsewhere.
    # Filtering job partitions would miss that competing allocation.
    tasks = slurm_manager.get_all_running_tasks(
        node_names = [node.name for node in snapshot.nodes] if partitions else None,
        _raise_on_error = True,
    )
    return tasks, snapshot, datetime.now(timezone.utc), time.monotonic()


@router.get(
    "/cluster/stats",
    response_model =ClusterStatsResponse,
)
def get_cluster_stats(
    running_skip: int = 0,
    running_limit: int = 10,
    pending_skip: int = 0,
    pending_limit: int = 10,
    execution_target: str | None = None,
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
    default_target: Annotated[str | None, Header(alias="X-Magnus-Default-Execution-Target")] = None,
):
    target = _resolve_target(
        requested_execution_target(
            execution_target,
            default_target,
        ),
    )
    config = target.config
    if is_local_mode:
        return _get_cluster_stats_local(db, running_skip, running_limit, pending_skip, pending_limit)

    # --- 1. 获取 Slurm 真实数据 ---
    # SlurmManager 涉及阻塞 Shell 命令，必须在线程池中运行 (def)
    slurm_manager = SlurmManager(config=config)
    tenant_partitions = resource_partitions(config)
    cluster_stats_cache_ttl = magnus_config["server"]["scheduler"]["cluster_stats_cache_ttl"]
    try:
        all_slurm_tasks, node_snapshot, observed_at, observed_monotonic = _cached_slurm_view(
            ("cluster_view", target.id, tuple(tenant_partitions)),
            cluster_stats_cache_ttl,
            lambda: _query_cluster_view(slurm_manager, tenant_partitions),
        )
    except Exception as error:
        _report_cluster_query_failure(
            target.id,
            error,
        )
        raise HTTPException(
            status_code = 503,
            detail = "Slurm resource accounting is temporarily unavailable",
        ) from error
    is_stale = cluster_stats_cache_ttl > 0 and time.monotonic() - observed_monotonic > cluster_stats_cache_ttl

    running_slurm_ids = [task["id"] for task in all_slurm_tasks]

    # Partitions of one controller share Slurm IDs; independent controllers do
    # not. Match only jobs reached through the selected Slurm connection.
    connected_targets = [
        candidate
        for candidate in configured_execution_targets.values()
        if candidate.slurm_connection_key == target.slurm_connection_key
    ]
    magnus_jobs_orm = []
    if running_slurm_ids:
        magnus_jobs_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
            or_(*(candidate.job_filter(models.Job) for candidate in connected_targets)),
            models.Job.slurm_job_id.in_(running_slurm_ids),
        ).all()

    magnus_job_map = {job.slurm_job_id: job for job in magnus_jobs_orm}

    # --- 3. 构造最终列表 ---
    all_running_jobs: List[JobListItem] = []

    for task in all_slurm_tasks:
        slurm_id = task["id"]

        if slurm_id in magnus_job_map:
            # Cancellation/preemption stays visible while Slurm releases the
            # allocation; a live row must not revive the DB lifecycle state.
            job_orm = magnus_job_map[slurm_id]
            job_target = select_execution_target(configured_execution_targets, job_orm.execution_target)
            job_resp = JobListItem.model_validate(job_orm)
            job_resp.resource_summary = JobResourceSummary(**allocated_job_resources(
                task,
                mode = resource_mode_for_execution(job_target.config["execution"]),
            ))
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
                start_dt = datetime.now(timezone.utc)

            # external job 的 cpu / mem 已由 get_all_running_tasks 从 squeue 采到，
            # 保留这些旧字段供旧客户端使用，新客户端统一读 resource_summary；
            # 外部任务已是全 Job 分配，不能再按本站每节点/每 task 约定乘一次。
            external_cpu_count = task.get("cpu_count") or None
            external_memory_mb = task.get("memory_mb") or 0
            if external_memory_mb <= 0:
                external_memory_demand = None
            elif external_memory_mb % 1024 == 0:
                external_memory_demand = f"{external_memory_mb // 1024}G"
            else:
                external_memory_demand = f"{external_memory_mb}M"

            mock_job = JobListItem(
                execution_target = target.id,
                task_name = task["name"],
                description = "External slurm task",
                namespace = "External",
                repo_name = "N/A",
                branch = "N/A",
                commit_sha = "N/A",
                container_image = "N/A",
                gpu_type = task["gpu_type"].lower().replace(" ", ""),
                gpu_count = task["gpu_count"],
                cpu_count = external_cpu_count,
                memory_demand = external_memory_demand,
                resource_summary = JobResourceSummary(**allocated_job_resources(
                    task,
                    mode = resource_mode_for_execution(config["execution"]),
                )),
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

    observed_job_ids = {job.id for job in magnus_jobs_orm}
    other_running = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.status == JobStatus.RUNNING,
        ~models.Job.id.in_(observed_job_ids),
    ).all()
    all_running_jobs.extend(JobListItem.model_validate(job) for job in other_running)

    magnus_group = [j for j in all_running_jobs if j.job_type != JobType.EXTERNAL]
    external_group = [j for j in all_running_jobs if j.job_type == JobType.EXTERNAL]

    # start_time 现在一律是 aware（ORM 侧 UtcDateTime / SLURM 侧带偏移量），哨兵也得
    # 是 aware，否则缺 start_time 的 job 一参与排序就 aware/naive 相比抛 TypeError。
    _epoch_start = datetime.min.replace(tzinfo=timezone.utc)
    magnus_group.sort(key=lambda x: x.start_time or _epoch_start, reverse=True)
    _prefer_target(magnus_group, target)
    external_group.sort(key=lambda x: x.start_time or _epoch_start, reverse=True)

    sorted_all_running = magnus_group + external_group

    # --- 5. Running 列表分页切片 ---
    total_running = len(sorted_all_running)
    paginated_running = sorted_all_running[running_skip : running_skip + running_limit]

    # Magnus IDs, unlike Slurm IDs, are unique across independent controllers.
    not_running_in_slurm = ~models.Job.id.in_(observed_job_ids)
    pending_jobs_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PAUSED]),
        not_running_in_slurm,
    ).all()
    preparing_jobs_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.status.in_([JobStatus.PREPARING, JobStatus.BUILDING]),
        not_running_in_slurm,
    ).all()

    # Pending/Queued/Paused 按调度器排序
    pending_jobs_orm.sort(key=_scheduler_sort_key, reverse=True)
    # Preparing 按创建时间排序
    preparing_jobs_orm.sort(key=lambda x: x.created_at.timestamp(), reverse=True)

    # 合并：Pending 在前，Preparing 在后
    all_pending_jobs_orm = pending_jobs_orm + preparing_jobs_orm
    _prefer_target(all_pending_jobs_orm, target)

    total_pending = len(all_pending_jobs_orm)
    paginated_pending_orm = all_pending_jobs_orm[pending_skip : pending_skip + pending_limit]

    paginated_pending = [JobListItem.model_validate(job) for job in paginated_pending_orm]

    return {
        "resources": {
            "node": target.name,
            "gpu_model": " / ".join(dict.fromkeys(gpu["label"] for gpu in config["cluster"]["gpus"])) or "N/A",
            **_cluster_resource_totals(node_snapshot),
            "observed_at": observed_at,
            "is_stale": is_stale,
            "accounting_scope": "partition" if tenant_partitions else "cluster",
            "unavailable_gpus": node_snapshot.unavailable_gpus,
            "unavailable_cpus": node_snapshot.unavailable_cpus,
            "unavailable_mem_mb": node_snapshot.unavailable_mem_mb,
            "node_count": node_snapshot.node_count,
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
    execution_target: str | None = None,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
    default_target: Annotated[str | None, Header(alias="X-Magnus-Default-Execution-Target")] = None,
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

    target = _resolve_target(
        requested_execution_target(
            execution_target,
            default_target,
        ),
    )
    user_ids = [current_user.id] + _collect_descendant_ids(current_user)

    # 获取 Running 任务
    running_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.user_id.in_(user_ids),
        models.Job.status == JobStatus.RUNNING,
    ).order_by(models.Job.start_time.desc()).all()

    # 获取排队任务
    queued_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.user_id.in_(user_ids),
        models.Job.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PAUSED]),
    ).all()
    preparing_orm = db.query(models.Job).options(*models.job_list_load_options()).filter(
        models.Job.user_id.in_(user_ids),
        models.Job.status.in_([JobStatus.PREPARING, JobStatus.BUILDING]),
    ).all()

    # 对排队任务应用调度排序
    queued_orm.sort(key=_scheduler_sort_key, reverse=True)
    # Preparing 按创建时间排序
    preparing_orm.sort(key=lambda x: x.created_at.timestamp(), reverse=True)

    # 合并全量列表：Running > Pending/Queued/Paused > Preparing
    all_jobs_orm = running_orm + queued_orm + preparing_orm
    _prefer_target(all_jobs_orm, target)

    # 计算总数与切片 (Pagination)
    total_count = len(all_jobs_orm)
    paginated_orm = all_jobs_orm[skip : skip + limit]

    return {
        "items": [JobListItem.model_validate(job) for job in paginated_orm],
        "total": total_count,
    }
