# back_end/server/routers/services.py
import httpx
import logging
import asyncio
import traceback
import socket
from typing import Optional, Dict, Any
from datetime import datetime
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .. import database
from .. import models
from ..models import JobStatus, Service
from ..schemas import ServiceResponse, ServiceCreate, PagedServiceResponse
from .._service_manager import service_manager
from .._magnus_config import magnus_config
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# 防止同一服务的并发创建冲突 (检查-执行 竞态保护)
# key: service_id, value: asyncio.Lock
_service_spawn_locks = defaultdict(asyncio.Lock)

# 流量控制信号量字典
# key: service_id, value: asyncio.Semaphore
_service_semaphores: Dict[str, asyncio.Semaphore] = {}


@router.post(
    "/services",
    response_model=ServiceResponse,
)
async def create_or_update_service(
    service_data: ServiceCreate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
)-> models.Service:
    existing = db.query(Service).filter(Service.id == service_data.id).first()
    data = service_data.model_dump()

    if existing:
        if existing.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to modify this service")

        # 如果修改了最大并发数，删除旧的信号量以强制重建
        if service_data.max_concurrency != existing.max_concurrency:
            if existing.id in _service_semaphores:
                del _service_semaphores[existing.id]
                logger.info(f"Concurrency limit changed for {existing.id}, semaphore reset.")

        for k, v in data.items():
            setattr(existing, k, v)

        existing.owner_id = current_user.id

        db.commit()
        db.refresh(existing)
        return existing

    else:
        new_service = Service(
            **data,
            owner_id=current_user.id,
            is_active=True,
            last_activity_time=datetime.utcnow(),
        )
        db.add(new_service)
        db.commit()
        db.refresh(new_service)
        return new_service


@router.api_route(
    "/services/{service_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_service_request(
    service_id: str,
    path: str,
    request: Request,
    db: Session = Depends(database.get_db)
)-> StreamingResponse:
    # 1. 基础检查
    service = db.query(Service).filter(Service.id == service_id).first()
    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    if not service.is_active:
        raise HTTPException(status_code=503, detail="Service is inactive")

    # Keep-Alive
    service.last_activity_time = datetime.utcnow()
    db.commit()

    # 2. 获取或创建信号量 (Semaphore)
    if service.id not in _service_semaphores:
        # 使用 Service 内禀的 max_concurrency
        _service_semaphores[service.id] = asyncio.Semaphore(service.max_concurrency)

    sem = _service_semaphores[service.id]

    # 3. 定义总时间预算 (SLA)
    # 所有的动作（排队拿锁、拉起服务、建立连接）都必须在这个时间窗口内完成
    start_time = datetime.utcnow()
    total_budget = service.request_timeout

    def get_remaining_time()-> float:
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        return max(0.0, total_budget - elapsed)

    # 4. 尝试获取信号量 (流量控制门卫)
    try:
        # 如果这里排队超过了剩余时间，直接抛出 429
        await asyncio.wait_for(sem.acquire(), timeout=get_remaining_time())
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=429,
            detail=f"Service is busy (Max concurrency {service.max_concurrency} reached). Please try again later."
        )

    try:
        # === 进入流量控制区 ===

        # 5. 检查与拉起 (Spawn Logic with Lock)
        # 再次检查剩余时间
        if get_remaining_time() <= 0:
            raise HTTPException(status_code=504, detail="Timeout while waiting for concurrency slot")

        async with _service_spawn_locks[service_id]:
            db.refresh(service)
            current_job = service.current_job

            should_revive = False
            if not current_job:
                should_revive = True
            elif current_job.status in [JobStatus.FAILED, JobStatus.TERMINATED, JobStatus.SUCCESS]:
                should_revive = True

            if should_revive:
                try:
                    port = service_manager.allocate_port(db)

                    env_cmd = "\n".join([
                        f"export MAGNUS_PORT={port}",
                        service.entry_command,
                    ])

                    # 使用 Service 定义的 Job 元数据
                    new_job = models.Job(
                        task_name=service.job_task_name,
                        description=service.job_description,
                        user_id=service.owner_id,
                        namespace=service.namespace,
                        repo_name=service.repo_name,
                        branch=service.branch,
                        commit_sha=service.commit_sha,
                        gpu_count=service.gpu_count,
                        gpu_type=service.gpu_type,
                        cpu_count=service.cpu_count,
                        memory_demand=service.memory_demand,
                        runner=service.runner,
                        entry_command=env_cmd,
                        status=JobStatus.PENDING,
                        job_type=service.job_type,
                    )

                    db.add(new_job)
                    db.flush()

                    service.current_job_id = new_job.id
                    service.assigned_port = port
                    db.commit()

                    current_job = new_job
                    logger.info(f"Service {service.id} revived with Job {new_job.id} on port {port}")

                except Exception as e:
                    logger.error(f"Failed to revive service {service.id}: {e}")
                    raise HTTPException(status_code=500, detail=f"Service spawn failed: {e}")

        # 6. 阻塞等待服务就绪 (Wait Logic)
        is_ready = False

        while get_remaining_time() > 0:
            db.refresh(current_job)

            if current_job.status in [JobStatus.FAILED, JobStatus.TERMINATED]:
                raise HTTPException(status_code=502, detail="Service job failed during startup")

            if current_job.status in [JobStatus.PENDING, JobStatus.PAUSED]:
                service.last_activity_time = datetime.utcnow()
                db.commit()
                await asyncio.sleep(1)
                continue

            if current_job.status == JobStatus.RUNNING:
                if not service.assigned_port:
                    await asyncio.sleep(1)
                    continue

                try:
                    # 快速探测端口
                    with socket.create_connection(("127.0.0.1", service.assigned_port), timeout=0.5):
                        is_ready = True
                        break
                except (ConnectionRefusedError, socket.timeout, OSError):
                    service.last_activity_time = datetime.utcnow()
                    db.commit()
                    await asyncio.sleep(1)
                    continue

            await asyncio.sleep(1)

        if not is_ready:
            raise HTTPException(status_code=504, detail={
                "detail": "Service startup timed out",
                "job_id": current_job.id,
                "status": current_job.status
            })

        # 7. 转发请求 (Forward Logic)
        target_url = f"http://127.0.0.1:{service.assigned_port}/{path}"
        if request.query_params:
            target_url += f"?{request.query_params}"

        # 从配置中读取 httpx 的参数
        service_config = magnus_config.get("server", {}).get("services", {})

        proxy_timeout = httpx.Timeout(
            connect=service_config.get("proxy_connect_timeout", 2.0),
            read=service_config.get("proxy_read_timeout", 600.0),
            write=service_config.get("proxy_write_timeout", 30.0),
            pool=service_config.get("proxy_pool_timeout", 5.0),
        )

        client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{service.assigned_port}",
            timeout=proxy_timeout,
            follow_redirects=True,
        )

        try:
            body = await request.body()

            rp_req = client.build_request(
                request.method,
                f"/{path}",
                content=body,
                headers=request.headers.raw,
                params=request.query_params,
            )

            service.last_activity_time = datetime.utcnow()
            db.commit()

            r = await client.send(rp_req, stream=True)

            return StreamingResponse(
                r.aiter_raw(),
                status_code=r.status_code,
                headers=r.headers,
                background=BackgroundTask(client.aclose),
            )

        except httpx.ConnectError:
            await client.aclose()
            raise HTTPException(status_code=502, detail="Service process is running but connection failed.")
        except Exception as e:
            await client.aclose()
            logger.error(f"Proxy error for {service.id}: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=str(e))

    finally:
        # === 离开流量控制区 ===
        sem.release()


@router.get(
    "/services",
    response_model=PagedServiceResponse,
)
async def list_services(
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    owner_id: Optional[str] = None,
    db: Session = Depends(database.get_db)
)-> Dict[str, Any]:
    query = db.query(models.Service)

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                models.Service.name.ilike(search_pattern),
                models.Service.id.ilike(search_pattern),
                models.Service.description.ilike(search_pattern),
            )
        )

    if owner_id and owner_id != "all":
        query = query.filter(models.Service.owner_id == owner_id)

    total = query.count()

    items = query.order_by(models.Service.last_activity_time.desc()) \
        .offset(skip) \
        .limit(limit) \
        .all()

    return {"total": total, "items": items}