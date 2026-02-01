# back_end/server/_service_manager.py
import logging
import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Service, JobStatus
from ._scheduler import scheduler

logger = logging.getLogger(__name__)


class ServiceManager:
    def __init__(self):
        self._running = False

    async def start_background_loop(self):
        if self._running:
            return
        self._running = True
        logger.info("Service Manager background loop started.")
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Service Manager tick failed: {e}", exc_info=True)
            await asyncio.sleep(3)

    async def _tick(self):
        # 使用 run_in_executor 避免阻塞 asyncio loop
        await asyncio.to_thread(self._sync_logic)

    def _sync_logic(self):
        with SessionLocal() as db:
            active_services = db.query(Service).filter(Service.is_active == True).all()
            now = datetime.now(timezone.utc)

            for service in active_services:
                if not service.current_job_id:
                    continue

                job = service.current_job
                
                # 情况 1：Job 已经挂了/结束了，清理僵尸状态
                if not job or job.status in [JobStatus.FAILED, JobStatus.TERMINATED, JobStatus.SUCCESS]:
                    service.current_job_id = None
                    service.assigned_port = None
                    db.add(service)
                    continue

                # 情况 2：Job 正在运行或等待，检查是否需要 Scale Down
                if job.status in [JobStatus.PENDING, JobStatus.PAUSED]:
                    # Anti-Starvation
                    service.last_activity_time = now
                    db.add(service)

                elif job.status == JobStatus.RUNNING:
                    idle_duration = (now - service.last_activity_time).total_seconds()
                    idle_limit_seconds = service.idle_timeout * 60

                    # 约定 idle_timeout=0 表示永不 Scale Down
                    if service.idle_timeout and (idle_duration > idle_limit_seconds):
                        logger.info(f"Service {service.id} idle for {idle_duration:.0f}s. Scaling to zero.")
                        scheduler.terminate_job(db, job)
                        
                        # Scale Down 时立即解除关联
                        service.current_job_id = None
                        service.assigned_port = None
                        db.add(service)

            db.commit()

    def allocate_port(
        self,
        db: Session,
    ) -> int:
        used_ports = set(
            row[0] for row in db.query(Service.assigned_port)
            .filter(Service.assigned_port.is_not(None))
            .with_for_update()
            .all()
        )

        for _ in range(100):
            candidate = random.randint(10000, 30000)
            if candidate not in used_ports:
                return candidate

        raise RuntimeError("Failed to allocate a free port for Service after 100 attempts.")


service_manager = ServiceManager()