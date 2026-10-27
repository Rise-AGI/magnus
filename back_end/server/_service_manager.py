# back_end/server/_service_manager.py
import logging
import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import update

from .database import SessionLocal
from .models import Service, JobStatus
from ._scheduler import scheduler
from ._blocking_io import _run_blocking_io
from ._service_lifecycle import service_operation

logger = logging.getLogger(__name__)


def service_job_released(
    job,
) -> bool:
    if job is None:
        return True
    return (
        job.status in (JobStatus.FAILED, JobStatus.TERMINATED, JobStatus.SUCCESS)
        and job.slurm_job_id is None
        and job.id not in scheduler.inflight_job_ids()
    )


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
        await _run_blocking_io(self._sync_logic)

    def _sync_logic(self):
        with SessionLocal() as db:
            service_ids = db.query(Service.id).filter(Service.current_job_id.is_not(None)).all()
        for (service_id,) in service_ids:
            try:
                self._sync_service(service_id)
            except Exception as error:
                logger.warning("Service %s reconciliation failed (%s)", service_id, type(error).__name__)

    def _sync_service(
        self,
        service_id: str,
    ) -> None:
        with service_operation(service_id), SessionLocal() as db:
            service = db.query(Service).filter(Service.id == service_id).first()
            if service is None or service.current_job_id is None:
                return
            job = service.current_job
            now = datetime.now(timezone.utc)
            if service_job_released(job):
                service.current_job_id = None
                service.assigned_port = None
            elif not service.is_active:
                return
            elif job.status in (JobStatus.PENDING, JobStatus.PAUSED, JobStatus.PREPARING, JobStatus.BUILDING):
                service.last_activity_time = now
            elif job.status == JobStatus.RUNNING:
                idle_duration = (now - service.last_activity_time).total_seconds()
                if service.idle_timeout and idle_duration > service.idle_timeout * 60:
                    logger.info("Service %s idle for %.0fs. Scaling to zero.", service.id, idle_duration)
                    scheduler.terminate_job(db, job)
                    if service_job_released(job):
                        service.current_job_id = None
                        service.assigned_port = None
            db.commit()

    def allocate_port(
        self,
        db: Session,
        service: Service,
    )-> int:
        # SQLite ignores FOR UPDATE; reserve its writer before reading the port
        # set. The caller commits this reservation with the new Job association.
        reserved = db.execute(
            update(Service).where(Service.id == service.id).values(id=Service.id),
            execution_options={"synchronize_session": False},
        )
        if reserved.rowcount != 1:
            raise RuntimeError("Service disappeared during port allocation")
        used_ports = {
            row[0] for row in db.query(Service.assigned_port)
            .filter(Service.assigned_port.is_not(None))
            .all()
        }

        for _ in range(100):
            candidate = random.randint(10000, 30000)
            if candidate not in used_ports:
                service.assigned_port = candidate
                db.flush()
                return candidate

        raise RuntimeError("Failed to allocate a free port for Service after 100 attempts.")


service_manager = ServiceManager()
