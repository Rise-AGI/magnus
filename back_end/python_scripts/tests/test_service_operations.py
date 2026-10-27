"""Concurrent service decisions with real SQLite mappings and no executor."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
from pathlib import Path
import random
import runpy
import tempfile
import threading
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from test_user_lifecycle import ns, load_function


SERVER = Path(__file__).resolve().parents[2] / "server"
User, Service, Job, JobStatus = (ns[name] for name in ("User", "Service", "Job", "JobStatus"))


class ServiceOperationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = create_engine(f"sqlite:///{temporary.name}/services.db", connect_args={"timeout": 3})
        self.addCleanup(self.engine.dispose)
        ns["Base"].metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        self.killed = []
        def terminate(db, job):
            self.killed.append(job.id)
            job.status = JobStatus.TERMINATED
            db.commit()
        self.namespace = dict(ns)
        self.namespace.update(runpy.run_path(str(SERVER / "_blocking_io.py")))
        self.namespace.update(
            service_operation=runpy.run_path(str(SERVER / "_service_lifecycle.py"))["service_operation"],
            asyncio=asyncio, datetime=datetime, timezone=timezone, random=random, update=update,
            SessionLocal=self.sessions, models=SimpleNamespace(**ns), HTTPException=HTTPException,
            logger=logging.getLogger(__name__), Depends=lambda value: None,
            database=SimpleNamespace(get_db=lambda: None), get_current_user=lambda: None,
            scheduler=SimpleNamespace(inflight_job_ids=lambda: set(), terminate_job=terminate),
            _auth_db_semaphore=asyncio.Semaphore(5), _service_semaphores_lock=threading.Lock(), _service_semaphores={},
            assert_can_manage_resource=lambda *args, **kwargs: None,
            apply_cluster_defaults=lambda value, **kwargs: value,
            normalize_per_cpu_resources=lambda *args, **kwargs: None,
            validate_cluster_limits=lambda *args, **kwargs: None,
            enforce_field_size_limits=lambda *args, **kwargs: None,
            resource_mode_for_execution=lambda execution: "local",
            _resolve_target=lambda key: SimpleNamespace(
                id="local", config={"transport": {"mode": "local"}, "execution": {}, "cluster": {"default_container_image": "synthetic"}},
            ),
        )
        for name in ("service_job_released", "allocate_port", "_sync_service"):
            load_function("_service_manager.py", name, self.namespace)
        for name in ("_safe_db_call", "_shutdown_service_resources_sync", "_try_revive_service_standalone", "create_service", "delete_service"):
            load_function("routers/services.py", name, self.namespace)
        self.allocate = self.namespace["allocate_port"]
        self.namespace["service_manager"] = SimpleNamespace(allocate_port=lambda db, service: self.allocate(None, db, service))
        self.revive = self.namespace["_try_revive_service_standalone"]
        with self.sessions() as db:
            db.add(User(id="owner", name="owner", token="synthetic"))
            for identifier in ("first", "second"):
                db.add(Service(
                    id=identifier, owner_id="owner", name=identifier, is_active=True,
                    namespace="synthetic", repo_name="synthetic", branch="main", commit_sha="synthetic",
                    entry_command="true", job_task_name="probe", job_description="probe",
                    gpu_type="cpu", gpu_count=0, cpu_count=1, memory_demand="1G",
                    container_image="synthetic", max_concurrency=1,
                ))
            db.commit()

    async def test_cancelled_spawn_joins_before_next_request_can_spawn(self):
        entered, release = threading.Event(), threading.Event()
        def allocate(db, service):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test barrier")
            return self.allocate(None, db, service)
        self.namespace["service_manager"].allocate_port = allocate
        first = asyncio.create_task(self.namespace["_safe_db_call"](self.revive, "first"))
        second = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            first.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(first.done())
            self.assertEqual(self.namespace["_auth_db_semaphore"]._value, 4)
            second = asyncio.create_task(self.namespace["_safe_db_call"](self.revive, "first"))
            await asyncio.sleep(0.01)
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(first, 2)
            identifier, port = await asyncio.wait_for(second, 2)
            with self.sessions() as db:
                self.assertEqual(db.query(Job).count(), 1)
                self.assertEqual(db.get(Service, "first").current_job_id, identifier)
                self.assertEqual(db.get(Service, "first").assigned_port, port)
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    async def test_stale_stop_and_delete_sessions_still_terminate_new_job(self):
        for action, identifier in (("stop", "first"), ("delete", "second")):
            with self.subTest(action=action), self.sessions() as db:
                stale_service = db.get(Service, identifier)
                self.assertIsNone(stale_service.current_job_id)
                job_id, _ = await asyncio.to_thread(self.revive, identifier)
                if action == "stop":
                    request = SimpleNamespace(
                        id=identifier, execution_target=None, max_concurrency=1, model_fields_set=set(),
                        model_dump=lambda: {"is_active": False, "max_concurrency": 1, "cpu_count": 1, "memory_demand": "1G"},
                    )
                    self.namespace["create_service"](request, db, SimpleNamespace(id="owner"))
                    self.assertFalse(stale_service.is_active)
                    self.assertIsNone(stale_service.current_job_id)
                else:
                    self.namespace["delete_service"](identifier, db, SimpleNamespace(id="owner"))
                    self.assertIsNone(db.get(Service, identifier))
                self.assertIn(job_id, self.killed)
                self.assertEqual(db.get(Job, job_id).status, JobStatus.TERMINATED)

    async def test_manager_cannot_clear_a_later_spawn_association(self):
        previous, _ = self.revive("first")
        with self.sessions() as db:
            db.get(Job, previous).status = JobStatus.SUCCESS
            db.commit()
        entered, release = threading.Event(), threading.Event()
        released = self.namespace["service_job_released"]
        def observe(job):
            if job and job.id == previous:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test barrier")
            return released(job)
        self.namespace["service_job_released"] = observe
        manager = asyncio.create_task(asyncio.to_thread(self.namespace["_sync_service"], None, "first"))
        revival = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            revival = asyncio.create_task(asyncio.to_thread(self.revive, "first"))
            await asyncio.sleep(0.01)
            self.assertFalse(revival.done())
            release.set()
            await asyncio.wait_for(manager, 2)
            current, port = await asyncio.wait_for(revival, 2)
            self.assertNotEqual(current, previous)
            with self.sessions() as db:
                service = db.get(Service, "first")
                self.assertEqual((service.current_job_id, service.assigned_port), (current, port))
        finally:
            release.set()
            await asyncio.gather(manager, *([revival] if revival else []), return_exceptions=True)

    async def test_stopping_one_service_does_not_hold_sqlite_writer_during_cancel(self):
        self.revive("first")
        original = self.namespace["scheduler"].terminate_job
        def terminate(db, job):
            with self.sessions() as independent:
                independent.add(User(id="independent", name="independent"))
                independent.commit()
            original(db, job)
        self.namespace["scheduler"].terminate_job = terminate
        with self.sessions() as db:
            self.assertTrue(self.namespace["_shutdown_service_resources_sync"]("first", db))
            db.commit()
            self.assertIsNotNone(db.get(User, "independent"))

    async def test_different_services_reserve_ports_before_reading_used_set(self):
        entered, release, second_update = threading.Event(), threading.Event(), threading.Event()
        reads = []
        def before_execute(connection, cursor, statement, parameters, context, many):
            if statement.startswith("UPDATE services") and entered.is_set():
                second_update.set()
            if statement.startswith("SELECT services.assigned_port"):
                reads.append(statement)
                if len(reads) == 1:
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("test barrier")
        event.listen(self.engine, "before_cursor_execute", before_execute)
        choices = iter((12000, 12000, 12001))
        self.namespace["random"] = SimpleNamespace(randint=lambda *args: next(choices))
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(self.revive, "first")
            self.assertTrue(entered.wait(1))
            second = workers.submit(self.revive, "second")
            try:
                self.assertTrue(second_update.wait(1))
                self.assertEqual(len(reads), 1)
            finally:
                release.set()
            self.assertEqual({first.result(2)[1], second.result(2)[1]}, {12000, 12001})


if __name__ == "__main__":
    unittest.main()
