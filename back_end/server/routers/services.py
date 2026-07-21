# back_end/server/routers/services.py
import httpx
import logging
import asyncio
import socket
import threading
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone
from collections import defaultdict
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, case

from .. import database
from ..database import SessionLocal
from .. import models
from ..models import JobStatus, Service
from ..schemas import ServiceResponse, ServiceCreate, PagedServiceResponse, TransferRequest
from .._service_manager import service_manager
from .._id_registry import assert_id_available
from .._magnus_config import magnus_config, is_admin_user, apply_cluster_defaults, normalize_per_cpu_resources, validate_cluster_limits
from .._scheduler import scheduler
from .auth import get_current_user
from .users import _get_all_subordinate_ids
from ._authz import (
    assert_can_manage_resource,
    assert_valid_transfer_target,
    compute_can_manage,
)
from library import escape_like

logger = logging.getLogger(__name__)
router = APIRouter()

# Prevent concurrent creation conflicts for the same service
_service_spawn_locks = defaultdict(asyncio.Lock)

# Flow control semaphores (Per-Service). 被并发改：proxy（async，event loop）惰性
# get-or-create，create_service / delete_service（sync 端点，FastAPI 线程池）del。
# 用一把锁串起所有访问，杜绝 del 撞 proxy 的 check-then-act 引发的 KeyError / 重复建。
# 锁只包 dict 操作 + Semaphore() 构造（均同步、无 await），绝不跨 await 持有。
_service_semaphores: Dict[str, asyncio.Semaphore] = {}
_service_semaphores_lock = threading.Lock()

# 远端站点（transport=ssh）不支持 services：proxy 连的是 Magnus 宿主机的
# 127.0.0.1:{port}，而远端 job 跑在计算节点上、无网络路径回到 Magnus，服务永远拉不起。
# 明确 fail-fast（501）而非让用户撞 127.0.0.1 的神秘超时；待将来有反向 service 网关再放开。
_SERVICES_SUPPORTED = magnus_config["transport"]["mode"] != "ssh"
_REMOTE_SERVICES_DETAIL = (
    "Services are unavailable on this site: it drives a remote cluster over SSH, and "
    "remote compute nodes have no network path back to Magnus, so a service can never "
    "be reached. Run services on a co-located (local-transport) site."
)

# === [New] Double Bulkhead Configuration ===
# 1. Outer Bulkhead: Global Concurrency Limit for Proxy
#    Protects the Web Server (CPU/RAM/File Descriptors)
PROXY_GLOBAL_LIMIT = magnus_config["server"]["service_proxy"]["max_concurrency"]
_proxy_global_semaphore = asyncio.Semaphore(PROXY_GLOBAL_LIMIT)

# 2. Inner Bulkhead: Database Access Limit
#    Protects the SQLAlchemy Connection Pool (prevents TimeoutError)
_auth_db_semaphore = asyncio.Semaphore(5)


class ServiceSnapshot(BaseModel):
    id: str
    max_concurrency: int
    request_timeout: int
    assigned_port: Optional[int] = None
    entry_command: str
    job_task_name: str
    job_description: str
    owner_id: str
    namespace: str
    repo_name: str
    branch: str
    commit_sha: str
    gpu_count: int
    gpu_type: str
    cpu_count: Optional[int] = None
    memory_demand: Optional[str] = None
    runner: Optional[str] = None
    container_image: Optional[str] = None
    system_entry_command: Optional[str] = None
    job_type: str

    class Config:
        from_attributes = True


# === [New] Helper for Safe DB Access ===
async def _safe_db_call(func, *args, **kwargs):
    """
    Executes a synchronous DB function within a separate thread,
    protected by the Inner Bulkhead (_auth_db_semaphore).
    """
    async with _auth_db_semaphore:
        return await asyncio.to_thread(func, *args, **kwargs)


def _get_service_snapshot_standalone(service_id: str)-> ServiceSnapshot:
    with SessionLocal() as db:
        service = db.query(Service).filter(Service.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        # Keep-Alive: We update activity here as it's a "read" intent
        service.last_activity_time = datetime.now(timezone.utc)
        db.commit()
        return ServiceSnapshot.model_validate(service)


def _check_active_status_standalone(service_id: str)-> bool:
    with SessionLocal() as db:
        service = db.query(Service.is_active).filter(Service.id == service_id).first()
        return service.is_active if service else False


def _shutdown_service_resources_sync(service_id: str, db: Session):
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        return

    # Delegate to Scheduler to terminate the Job
    if service.current_job_id:
        job = db.query(models.Job).filter(models.Job.id == service.current_job_id).first()
        
        if job and job.status in [JobStatus.PREPARING, JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED]:
            try:
                logger.info(f"Terminating job {job.id} for service {service.id} via Scheduler...")
                scheduler.terminate_job(db, job)
            except Exception as e:
                logger.error(f"Failed to terminate job {job.id} during service shutdown: {e}")

    # Clean up Service runtime state
    service.assigned_port = None
    service.current_job_id = None
    service.last_activity_time = datetime.now(timezone.utc)
    
    db.flush()


def _try_revive_service_standalone(service_id: str)-> Tuple[str, int]:
    with SessionLocal() as db:
        service = db.query(Service).filter(Service.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found (during revive)")
        if not service.is_active:
            raise HTTPException(status_code=503, detail="Service stopped by user (spawn aborted).")

        current_job = service.current_job

        should_revive = False
        if not current_job:
            should_revive = True
        elif current_job.status in [JobStatus.FAILED, JobStatus.TERMINATED, JobStatus.SUCCESS]:
            should_revive = True

        # Path A: No restart needed
        if not should_revive:
            if current_job is None or service.assigned_port is None:
                raise HTTPException(status_code=500, detail="State Error: Service active but no job/port.")
            return current_job.id, service.assigned_port

        # Path B: Restart needed
        try:
            port = service_manager.allocate_port(db, service)

            env_cmd = "\n".join([
                f"export MAGNUS_PORT={port}",
                service.entry_command,
            ])

            # 与 jobs.py / create_service 建 job 的路径对齐：legacy service 行的 cpu_count /
            # memory_demand 常为 null，直接抄给 job 会落到 SLURM 隐式分配（如 1 核）、且前端不
            # 显示核数 / 内存。补成集群默认值，per_cpu 站点再归一化为真实分配值。
            resolved_resources = apply_cluster_defaults({
                "cpu_count": service.cpu_count,
                "memory_demand": service.memory_demand,
            })
            normalize_per_cpu_resources(resolved_resources)

            new_job = models.Job(
                task_name = service.job_task_name,
                description = service.job_description,
                user_id = service.owner_id,
                namespace = service.namespace,
                repo_name = service.repo_name,
                branch = service.branch,
                commit_sha = service.commit_sha,
                gpu_count = service.gpu_count,
                gpu_type = service.gpu_type,
                cpu_count = resolved_resources["cpu_count"],
                memory_demand = resolved_resources["memory_demand"],
                ephemeral_storage = service.ephemeral_storage,
                runner = service.runner,
                entry_command = env_cmd,
                status = JobStatus.PREPARING,
                job_type = service.job_type,
                # NOTE: Service.container_image is schema-nullable (models/_service.py: Mapped[str | None]),
                # while Job.container_image is NOT NULL. New services route through apply_cluster_defaults,
                # but legacy rows predating that helper may still hold NULL — keep this fallback as defense in depth.
                container_image = service.container_image or magnus_config["cluster"]["default_container_image"],
                system_entry_command = service.system_entry_command,
            )

            db.add(new_job)
            db.flush()

            service.current_job_id = new_job.id
            # assigned_port 已在 allocate_port 中设置
            db.commit()

            logger.info(f"Service {service.id} revived with Job {new_job.id} on port {port}")
            return new_job.id, port

        except Exception as e:
            logger.error(f"Failed to revive service {service.id}: {e}")
            raise HTTPException(status_code=500, detail=f"Service spawn failed: {e}")


def _check_socket_sync(host: str, port: int, timeout: float = 0.5)-> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
    
    
async def _check_http_readiness(port: int)-> bool:
    url = f"http://127.0.0.1:{port}/health"
    short_time = 1.0
    try:
        async with httpx.AsyncClient(timeout=short_time) as client:
            response = await client.get(url)
            if response.status_code >= 500: return False
        return True
    except Exception:
        return False


def _refresh_status_standalone(job_id: str, _: str)-> str:
    with SessionLocal() as db:
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        if not job:
            return JobStatus.TERMINATED
        return job.status


def _update_activity_standalone(service_id: str):
    with SessionLocal() as db:
        service = db.query(models.Service).filter(models.Service.id == service_id).first()
        if service:
            service.last_activity_time = datetime.now(timezone.utc)
            db.commit()


# === CRUD Routes (Unchanged) ===

@router.post("/services", response_model=ServiceResponse)
def create_service(
    service_data: ServiceCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
)-> models.Service:
    if not _SERVICES_SUPPORTED:
        raise HTTPException(status_code=501, detail=_REMOTE_SERVICES_DETAIL)

    existing = db.query(Service).filter(Service.id == service_data.id).first()

    if not existing:
        assert_id_available(db, service_data.id)

    data = service_data.model_dump()

    # 所有 Optional 字段填入集群默认值
    apply_cluster_defaults(data)
    # per_cpu 站点把 cpu_count / memory_demand 归一化为真实分配值（与 jobs.py 对齐）
    normalize_per_cpu_resources(data)
    # 与 jobs.py 对齐：超额请求转 HTTP 400，避免通过 service revive 绕过 Job 资源上限
    try:
        validate_cluster_limits(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if existing:
        assert_can_manage_resource(db, current_user, existing.owner_id, resource_label="service")

        was_active = existing.is_active
        will_be_active = data.get("is_active", was_active)

        if service_data.max_concurrency != existing.max_concurrency:
            with _service_semaphores_lock:
                removed_sem = _service_semaphores.pop(existing.id, None)
            if removed_sem is not None:
                logger.info(f"Concurrency limit changed for {existing.id}, semaphore reset.")

        for k, v in data.items():
            setattr(existing, k, v)
        existing.owner_id = current_user.id
        existing.updated_at = datetime.now(timezone.utc)

        if was_active and not will_be_active:
            _shutdown_service_resources_sync(existing.id, db)
            logger.info(f"Service {existing.id} toggled OFF, resources cleaned up.")

        db.commit()
        db.refresh(existing)
        return existing

    data["is_active"] = False
    new_service = Service(
        **data,
        owner_id = current_user.id,
        last_activity_time = datetime.now(timezone.utc),
        updated_at = datetime.now(timezone.utc),
    )
    db.add(new_service)
    db.commit()
    db.refresh(new_service)
    return new_service


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(
    service_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> None:
    # ... (Same as original code) ...
    svc = db.query(Service).filter(Service.id == service_id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    assert_can_manage_resource(db, current_user, svc.owner_id, resource_label="service")

    _shutdown_service_resources_sync(service_id, db)

    db.delete(svc)
    db.commit()

    with _service_semaphores_lock:
        _service_semaphores.pop(service_id, None)


@router.post("/services/{service_id}/transfer", response_model=ServiceResponse)
def transfer_service(
    service_id: str,
    body: TransferRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Service:
    svc = db.query(Service).filter(Service.id == service_id).first()
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    assert_can_manage_resource(db, current_user, svc.owner_id, resource_label="service")

    assert_valid_transfer_target(db, current_user, body.new_owner_id)

    svc.owner_id = body.new_owner_id
    svc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(svc)
    return svc


@router.get("/services", response_model=PagedServiceResponse)
def list_services(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    owner_id: Optional[str] = None,
    active_only: bool = False,
    sort_by: str = Query("activity", regex="^(activity|updated)$"),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
)-> Dict[str, Any]:
    query = db.query(models.Service)

    if search:
        safe = escape_like(search)
        search_pattern = f"%{safe}%"
        query = query.filter(or_(
            models.Service.name.ilike(search_pattern, escape="\\"),
            models.Service.id.ilike(search_pattern, escape="\\"),
            models.Service.description.ilike(search_pattern, escape="\\"),
        ))

    if owner_id and owner_id != "all":
        query = query.filter(models.Service.owner_id == owner_id)

    if active_only:
        query = query.filter(models.Service.is_active == True)

    total = query.count()

    human_first = case((models.User.user_type == "human", 0), else_=1)
    secondary = models.Service.updated_at.desc() if sort_by == "updated" else models.Service.last_activity_time.desc()
    items = query.join(models.User, models.Service.owner_id == models.User.id)\
                 .options(
                     joinedload(models.Service.owner),
                     joinedload(models.Service.current_job).options(*models.job_list_load_options()),
                 )\
                 .order_by(human_first, secondary)\
                 .offset(skip).limit(limit).all()

    is_admin = is_admin_user(current_user)
    subordinate_ids = set(_get_all_subordinate_ids(db, current_user.id)) if not is_admin else set()
    result = []
    for svc in items:
        resp = ServiceResponse.model_validate(svc)
        resp.can_manage = compute_can_manage(
            db, current_user, svc.owner_id, subordinate_ids=subordinate_ids,
        )
        result.append(resp)

    return {"total": total, "items": result}


@router.get("/services/{service_id}", response_model=ServiceResponse)
def get_service(
    service_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
)-> ServiceResponse:
    service = db.query(models.Service)\
        .options(joinedload(models.Service.current_job).options(*models.job_list_load_options()))\
        .filter(models.Service.id == service_id).first()

    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    resp = ServiceResponse.model_validate(service)
    resp.can_manage = compute_can_manage(db, current_user, service.owner_id)
    return resp


# === [Modified] Standalone Auth Logic for Proxy ===

def _authenticate_adapter(request: Request):
    """
    Adapts the unified auth logic from auth.py to the standalone/thread-safe 
    context required by the proxy.
    
    Why this is needed:
    The proxy path explicitly avoids holding a main-thread DB session (to support high concurrency).
    Standard FastAPI Dependencies (Depends) work by injecting deps at the start of the request.
    Here, we need to:
    1. Create a short-lived DB session (SessionLocal) inside the thread.
    2. Manually call the auth logic.
    """
    with SessionLocal() as db:
        # We manually call get_current_user. 
        # Passing token=None tells it to look into the request (Headers/Query/Cookie).
        # Exceptions (401) will propagate up and be caught by the proxy pre-check.
        get_current_user(request, token=None, db=db)


# === Proxy Service Request ===

@router.api_route(
    "/services/{service_id}/{path:path}",
    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_service_request(
    service_id: str,
    path: str,
    request: Request,
    # REMOVED: db: Session = Depends(database.get_db)->Fixes Connection Holding
)-> StreamingResponse:
    if not _SERVICES_SUPPORTED:
        raise HTTPException(status_code=501, detail=_REMOTE_SERVICES_DETAIL)

    # === 1. Start SLA Timer ===
    start_time = datetime.now(timezone.utc)
    total_budget: Optional[float] = None 

    def get_remaining_time()-> float:
        # Before snapshot, allow a generous fallback
        if total_budget is None: return 30.0 
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        return max(0.0, total_budget - elapsed)

    # === 2. Lightweight Pre-checks (Protected by Inner Bulkhead) ===
    # Fast operations that don't need the Global Proxy Semaphore
    try:
        # A. Auth (Using the new adapter which calls shared logic)
        await _safe_db_call(_authenticate_adapter, request)
        
        # B. Get Config
        service_snap = await _safe_db_call(_get_service_snapshot_standalone, service_id)
        
        # Set Budget based on Service Configuration
        total_budget = float(service_snap.request_timeout)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Proxy pre-check failed for {service_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal pre-check failed")

    # === 3. Global Bulkhead (Outer Limit) ===
    try:
        # Wait for a global slot using the remaining budget
        await asyncio.wait_for(
            _proxy_global_semaphore.acquire(),
            timeout = get_remaining_time(),
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code = 503,
            detail = f"System Busy: Global proxy limit ({PROXY_GLOBAL_LIMIT}) reached. Timeout after {total_budget}s."
        )

    try:
        # === 4. Service Bulkhead (Per-Service Limit) ===
        # 锁内 get-or-create（与线程池侧的 del 原子互斥），拿到 semaphore 引用后在锁外
        # acquire/release —— 绝不跨 await 持有同步锁。
        with _service_semaphores_lock:
            service_sem = _service_semaphores.get(service_snap.id)
            if service_sem is None:
                service_sem = asyncio.Semaphore(service_snap.max_concurrency)
                _service_semaphores[service_snap.id] = service_sem

        try:
            await asyncio.wait_for(service_sem.acquire(), timeout=get_remaining_time())
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code = 429,
                detail = f"Service Busy: Max concurrency {service_snap.max_concurrency} reached."
            )

        try:
            # === Critical Section ===

            # Double-Check Active Status
            is_active_now = await _safe_db_call(_check_active_status_standalone, service_id)
            if not is_active_now:
                raise HTTPException(status_code=503, detail="Service stopped.")

            # 5. Spawn / Revive
            if get_remaining_time() <= 0:
                raise HTTPException(status_code=504, detail="SLA budget exhausted before spawn.")

            async with _service_spawn_locks[service_id]:
                current_job_id, assigned_port = await _safe_db_call(_try_revive_service_standalone, service_id)
                service_snap.assigned_port = assigned_port

            # 6. Wait for Readiness
            is_ready = False
            while get_remaining_time() > 0:
                job_status = await _safe_db_call(_refresh_status_standalone, current_job_id, service_id)

                if job_status in [JobStatus.FAILED, JobStatus.TERMINATED]:
                    raise HTTPException(status_code=502, detail="Service job failed during startup")

                if job_status == JobStatus.RUNNING:
                    if not service_snap.assigned_port:
                        await asyncio.sleep(1)
                        continue

                    # Socket check (Fast, pure async/sync wrapper)
                    socket_ok = await asyncio.to_thread(
                        _check_socket_sync, "127.0.0.1", service_snap.assigned_port
                    )

                    if socket_ok:
                        if await _check_http_readiness(service_snap.assigned_port):
                            is_ready = True
                            break
                
                await asyncio.sleep(1)

            if not is_ready:
                raise HTTPException(status_code=504, detail="Service startup timed out within SLA budget.")

            # 7. Forward Request
            proxy_timeout = httpx.Timeout(
                connect = 2.0,
                read = get_remaining_time(),
                write = 30.0,
                pool = 5.0,
            )

            client = httpx.AsyncClient(
                base_url = f"http://127.0.0.1:{service_snap.assigned_port}",
                timeout = proxy_timeout,
                follow_redirects = True,
            )

            try:
                body = await request.body()

                # 过滤 Host header，让 httpx 根据 base_url 自动设置正确的 Host
                # 否则当存在 HTTP_PROXY 时，代理会根据错误的 Host 路由请求
                filtered_headers = [
                    (k, v) for k, v in request.headers.raw
                    if k.lower() != b'host'
                ]

                rp_req = client.build_request(
                    request.method,
                    f"/{path}",
                    content = body,
                    headers = filtered_headers,
                    params = request.query_params,
                )

                await _safe_db_call(_update_activity_standalone, service_id)

                r = await client.send(rp_req, stream=True)

                return StreamingResponse(
                    r.aiter_raw(),
                    status_code = r.status_code,
                    headers = r.headers,
                    background = BackgroundTask(client.aclose),
                )

            except httpx.ConnectError:
                await client.aclose()
                raise HTTPException(status_code=502, detail="Service running but connection failed.")
            except Exception as e:
                await client.aclose()
                logger.error(f"Proxy error for {service_id}: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        finally:
            service_sem.release()

    finally:
        _proxy_global_semaphore.release()
