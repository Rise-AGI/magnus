"""Service associations remain owned until external execution has released."""
from datetime import datetime, timezone
import logging
import runpy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import HTTPException
from sqlalchemy import Boolean, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship, sessionmaker

from test_job_finalization import Base, Job, JobStatus
from test_execution_targets import load_method


class Service(Base):
    __tablename__ = "lifecycle_services"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    current_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    current_job: Mapped[Job] = relationship()
    assigned_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    execution_target = "local"


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(temporary.name) / 'state.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.job = Job(id="job", status=JobStatus.RUNNING, slurm_job_id="123")
        self.service = Service(id="service", current_job=self.job, assigned_port=12345, is_active=True)
        self.db.add_all([self.job, self.service])
        self.db.commit()
        self.inflight = set()
        self.scheduler = SimpleNamespace(terminate_job=Mock(), inflight_job_ids=lambda: self.inflight)
        self.namespace = {
            "JobStatus": JobStatus, "Service": Service, "models": SimpleNamespace(Job=Job),
            "scheduler": self.scheduler, "HTTPException": HTTPException,
            "logger": logging.getLogger(__name__), "datetime": datetime, "timezone": timezone,
            "SessionLocal": self.sessions,
            "service_operation": runpy.run_path(str(Path(__file__).resolve().parents[2] / "server/_service_lifecycle.py"))["service_operation"],
            "_resolve_target": lambda key: SimpleNamespace(config={"transport": {"mode": "local"}}),
        }
        self.released = load_method("_service_manager.py", "service_job_released", self.namespace)
        self.shutdown = load_method("routers/services.py", "_shutdown_service_resources_sync", self.namespace)
        self.revive = load_method("routers/services.py", "_try_revive_service_standalone", self.namespace)
        sync = load_method("_service_manager.py", "_sync_service", self.namespace)
        self.manager = SimpleNamespace(_sync_service=lambda identifier: sync(None, identifier))
        self.tick = load_method("_service_manager.py", "_sync_logic", self.namespace)

    def test_shutdown_failure_and_disabled_scheduler_keep_association(self):
        for error in (RuntimeError("not acknowledged"), None):
            self.scheduler.terminate_job.side_effect = error
            with self.subTest(error=error), self.assertRaises(HTTPException) as caught:
                self.shutdown("service", self.db)
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(self.service.current_job_id, "job")
            self.assertEqual(self.service.assigned_port, 12345)

    def test_terminal_allocation_keeps_port_and_blocks_revival(self):
        def terminate(db, job):
            job.status = JobStatus.TERMINATED
            db.commit()

        self.scheduler.terminate_job.side_effect = terminate
        self.assertFalse(self.shutdown("service", self.db))
        self.assertEqual(self.service.assigned_port, 12345)
        with self.assertRaises(HTTPException) as caught:
            self.revive("service")
        self.assertEqual(caught.exception.status_code, 503)

    def test_inactive_service_is_released_only_after_allocation_drains(self):
        self.service.is_active = False
        self.job.status = JobStatus.TERMINATED
        self.db.commit()
        self.tick(self.manager)
        self.db.refresh(self.service)
        self.assertEqual(self.service.current_job_id, "job")
        self.job.slurm_job_id = None
        self.db.commit()
        self.inflight.add("job")
        self.tick(self.manager)
        self.db.refresh(self.service)
        self.assertEqual(self.service.assigned_port, 12345)
        self.inflight.clear()
        self.tick(self.manager)
        self.db.refresh(self.service)
        self.assertIsNone(self.service.current_job_id)
        self.assertIsNone(self.service.assigned_port)

    def test_unknown_submission_retains_service_and_port_after_stop(self):
        self.service.is_active = False
        self.job.status = JobStatus.TERMINATED
        self.job.slurm_job_id = None
        self.job.submission_attempt = '{"comment":"unknown"}'
        self.db.commit()
        self.assertFalse(self.released(self.job))
        self.tick(self.manager)
        self.db.refresh(self.service)
        self.assertEqual(self.service.current_job_id, "job")
        self.assertEqual(self.service.assigned_port, 12345)

    def test_released_execution_allows_explicit_shutdown_cleanup(self):
        self.job.status = JobStatus.TERMINATED
        self.job.slurm_job_id = None
        self.db.commit()
        self.assertTrue(self.shutdown("service", self.db))
        self.assertIsNone(self.service.current_job_id)
        self.assertIsNone(self.service.assigned_port)
        self.scheduler.terminate_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
