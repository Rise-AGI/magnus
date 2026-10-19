"""Terminal computation and durable artifact recovery have separate lifetimes."""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import DateTime, Enum, String, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


SERVER = Path(__file__).resolve().parents[2] / "server"
status_source = ast.parse((SERVER / "models" / "_job.py").read_text(encoding="utf-8"))
status_node = next(node for node in status_source.body if isinstance(node, ast.ClassDef) and node.name == "JobStatus")
import enum
exec(compile(ast.Module(body=[status_node], type_ignores=[]), "JobStatus", "exec"))


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus))
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    finalization_status: Mapped[str | None] = mapped_column(String, nullable=True)
    memory_demand: Mapped[str | None] = mapped_column(String, default="1G")
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def load_mixin(
    relative_path,
    namespace,
):
    path = SERVER / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)


def remove(
    path,
):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


def load_method(
    relative_path,
    name,
    namespace,
):
    source = SERVER / relative_path
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


class SlurmFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'state.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.events = []
        self.statuses = {}
        self.marker_failure = set()
        self.transfer_failure = set()
        self.cleanup_failure = set()
        self.success = set()
        self.marker_hook = None
        self.final_hook = None
        self.namespace = {
            "__name__": __name__,
            "SessionLocal": self.sessions,
            "Job": Job,
            "JobStatus": JobStatus,
            "ClusterSnapshot": object,
            "logger": logging.getLogger("finalization-test"),
            "magnus_workspace_path": str(self.root),
            "magnus_ephemeral_workspace_path": str(self.root),
            "is_local_mode": False,
            "magnus_config": {},
            "delete_file": remove,
        }
        load_mixin("_scheduler/_sync.py", self.namespace)
        lifecycle = ast.parse((SERVER / "_scheduler" / "_job_lifecycle.py").read_text(encoding="utf-8"))
        lifecycle.body = [
            node for node in lifecycle.body
            if not isinstance(node, ast.ImportFrom) or (not node.level and node.module != "pywheels.file_tools")
        ]
        exec(compile(lifecycle, "_job_lifecycle.py", "exec"), self.namespace)
        self.scheduler_class = type("Scheduler", (self.namespace["_SyncMixin"], self.namespace["_JobLifecycleMixin"]), {})
        self.scheduler = self.new_scheduler()

    def new_scheduler(self):
        actor = self.scheduler_class()
        actor._finalization_retry_after = {}
        def status(identifier):
            self.events.append(("query", identifier))
            result = self.statuses[identifier]
            if isinstance(result, Exception):
                raise result
            return result
        def markers(identifier):
            self.events.append(("markers", identifier))
            if identifier in self.marker_failure:
                raise RuntimeError("marker transport unavailable")
            if identifier in self.success:
                (self.root / "jobs" / identifier / ".magnus_success").write_text("success")
            if self.marker_hook:
                self.marker_hook(identifier)
        def final(identifier):
            with self.sessions() as db:
                job = db.get(Job, identifier)
                self.events.append(("transfer", identifier, job.status))
            if self.final_hook:
                self.final_hook(identifier)
            return identifier not in self.transfer_failure
        def remote_cleanup(identifier):
            self.events.append(("cleanup", identifier))
            if identifier in self.cleanup_failure:
                raise RuntimeError("remote filesystem unavailable")
        actor.slurm_manager = SimpleNamespace(check_job_status=status)
        actor._stage_out_markers = markers
        actor._stage_out_final = final
        actor._stage_out_logs = lambda identifier: self.events.append(("logs", identifier))
        actor._cleanup_remote_job = remote_cleanup
        return actor

    def add_job(self, identifier="job", status=JobStatus.RUNNING, observed="FAILED"):
        external = f"slurm-{identifier}"
        self.statuses[external] = observed
        with self.sessions() as db:
            db.add(Job(id=identifier, status=status, slurm_job_id=external))
            db.commit()
        table = self.root / "jobs" / identifier
        (table / "repository").mkdir(parents=True)
        (table / "repository" / "checkpoint").write_bytes(b"irreplaceable results")
        (table / "slurm").mkdir()
        (table / "slurm" / "output.txt").write_text("complete log")
        return table

    def state(self, identifier="job"):
        with self.sessions() as db:
            job = db.get(Job, identifier)
            return job.status, job.slurm_job_id, job.finalization_status, job.result

    def directory_symlink(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows account does not have symbolic-link creation privileges")
            raise

    def test_terminal_status_precedes_transfer_and_another_job_keeps_progressing(self):
        table = self.add_job()
        other = self.add_job("other")
        self.transfer_failure.add("job")
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "FAILED"))
        self.assertIn(("transfer", "job", JobStatus.FAILED), self.events)
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertEqual(self.state("other")[:3], (JobStatus.FAILED, None, None))
        self.assertFalse((other / "repository").exists())

    def test_restart_retries_without_requerying_expired_slurm_accounting(self):
        table = self.add_job()
        self.transfer_failure.add("job")
        self.scheduler._sync_reality_slurm()
        self.transfer_failure.clear()
        self.statuses.clear()
        self.events.clear()
        self.new_scheduler()._sync_reality_slurm()
        self.assertFalse(any(event[0] == "query" for event in self.events))
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))
        self.assertFalse((table / "repository").exists())
        self.assertEqual((table / "slurm" / "output.txt").read_text(), "complete log")

    def test_pending_retry_waits_without_delaying_first_terminal_classification(self):
        self.add_job()
        now = [100.0]
        self.namespace["monotonic"] = lambda: now[0]
        self.transfer_failure.add("job")
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[0], JobStatus.FAILED)
        self.transfer_failure.clear()
        self.events.clear()
        now[0] = 159.0
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.events, [])
        self.assertEqual(self.state()[2], "FAILED")
        now[0] = 160.0
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))
        self.assertEqual(self.scheduler._finalization_retry_after, {})

    def test_unknown_and_completing_do_not_end_or_clean_a_job(self):
        table = self.add_job(observed="UNKNOWN")
        for observed in ("UNKNOWN", "RUNNING"):
            self.statuses["slurm-job"] = observed
            self.scheduler._sync_reality_slurm()
            self.assertEqual(self.state()[:3], (JobStatus.RUNNING, "slurm-job", None))
            self.assertTrue((table / "repository").exists())
        self.assertFalse(any(event[0] == "cleanup" for event in self.events))

    def test_success_marker_wins_over_cancel_and_completion_without_marker_fails(self):
        self.add_job("success", observed="CANCELLED")
        self.add_job("missing", status=JobStatus.QUEUED, observed="COMPLETED")
        self.success.add("success")
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state("success")[:3], (JobStatus.SUCCESS, None, None))
        self.assertEqual(self.state("missing")[0], JobStatus.FAILED)
        self.assertIn("no success marker", self.state("missing")[3])

    def test_marker_transport_failure_is_not_a_missing_marker_and_is_durable(self):
        table = self.add_job(observed="COMPLETED")
        self.marker_failure.add("job")
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.RUNNING, "slurm-job", "COMPLETED"))
        self.assertFalse(any(event[0] == "transfer" for event in self.events))
        self.assertTrue((table / "repository").exists())
        self.marker_failure.clear()
        self.statuses.clear()
        self.success.add("job")
        self.new_scheduler()._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, None, None))

    def test_cleanup_retry_does_not_reinterpret_deleted_success_marker(self):
        table = self.add_job(observed="COMPLETED")
        self.success.add("job")
        self.cleanup_failure.add("job")
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, "slurm-job", "CLEANUP"))
        self.assertFalse((table / ".magnus_success").exists())
        self.cleanup_failure.clear()
        self.events.clear()
        self.new_scheduler()._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, None, None))
        self.assertEqual(self.events, [("cleanup", "job")])

    def test_paused_and_terminated_stay_owned_until_artifacts_are_safe(self):
        for status in (JobStatus.PAUSED, JobStatus.TERMINATED):
            identifier = status.value
            table = self.add_job(identifier, status=status, observed="CANCELLED")
            self.transfer_failure.add(identifier)
            self.scheduler._sync_reality_slurm()
            self.assertEqual(self.state(identifier)[:3], (status, f"slurm-{identifier}", "CANCELLED"))
            self.assertTrue((table / "repository").exists())
            self.transfer_failure.remove(identifier)
            self.new_scheduler()._sync_reality_slurm()
            self.assertEqual(self.state(identifier)[:3], (status, None, None))

    def test_cancel_during_marker_read_does_not_resurrect_success(self):
        self.add_job(observed="COMPLETED")
        self.success.add("job")
        def cancel(identifier):
            with self.sessions() as db:
                db.get(Job, identifier).status = JobStatus.TERMINATED
                db.commit()
        self.marker_hook = cancel
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, None, None))

    def test_cleanup_refuses_pending_transfer_from_other_callers(self):
        table = self.add_job()
        with self.sessions() as db:
            db.get(Job, "job").finalization_status = "FAILED"
            db.commit()
        self.assertFalse(self.scheduler._clean_up_working_table("job"))
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertEqual(self.events, [])

    def test_failed_local_delete_keeps_cleanup_pending(self):
        self.add_job()
        self.namespace["delete_file"] = lambda path: None
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "CLEANUP"))
        self.assertFalse(any(event[0] == "cleanup" for event in self.events))
        self.namespace["delete_file"] = remove
        self.new_scheduler()._sync_reality_slurm()
        self.assertIsNone(self.state()[2])

    def test_cleanup_unlinks_directory_and_broken_symlinks_without_following_them(self):
        table = self.add_job()
        external = self.root / "shared-results"
        external.mkdir()
        (external / "checkpoint").write_bytes(b"must remain")
        directory_link = table / "shared"
        broken_link = table / "missing"
        self.directory_symlink(external, directory_link)
        self.directory_symlink(self.root / "missing-target", broken_link)
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))
        self.assertFalse(os.path.lexists(directory_link))
        self.assertFalse(os.path.lexists(broken_link))
        self.assertEqual((external / "checkpoint").read_bytes(), b"must remain")
        self.assertFalse((self.root / "missing-target").exists())

    def test_cleanup_unlinks_separate_ephemeral_symlinks_without_following_them(self):
        ephemeral = self.root / "ephemeral"
        (ephemeral / "jobs").mkdir(parents=True)
        self.namespace["magnus_ephemeral_workspace_path"] = str(ephemeral)
        for broken in (False, True):
            with self.subTest(broken=broken):
                identifier = f"ephemeral-{broken}"
                self.add_job(identifier)
                target = self.root / f"external-{broken}"
                if not broken:
                    target.mkdir()
                    (target / "checkpoint").write_bytes(b"must remain")
                link = ephemeral / "jobs" / identifier
                self.directory_symlink(target, link)
                self.scheduler._sync_reality_slurm()
                self.assertEqual(self.state(identifier)[:3], (JobStatus.FAILED, None, None))
                self.assertFalse(os.path.lexists(link))
                if broken:
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual((target / "checkpoint").read_bytes(), b"must remain")

    def test_oom_marker_retains_specific_failure_reason(self):
        table = self.add_job()
        (table / ".magnus_oom").write_text("OOM")
        self.transfer_failure.add("job")
        self.scheduler._sync_reality_slurm()
        self.assertEqual(self.state()[0], JobStatus.FAILED)
        self.assertIn("Out of memory", self.state()[3])

    def test_terminate_pending_recovery_does_not_cancel_or_clean_it(self):
        table = self.add_job()
        self.transfer_failure.add("job")
        self.scheduler._sync_reality_slurm()
        terminate = load_method("_scheduler/_core.py", "terminate_job", self.namespace)
        self.scheduler.enabled = True
        self.scheduler.preparing_jobs = {}
        self.scheduler._preparing_jobs_lock = threading.Lock()
        self.scheduler._loop = None
        self.events.clear()
        with self.sessions() as db:
            terminate(self.scheduler, db, db.get(Job, "job"))
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, "slurm-job", "FAILED"))
        self.assertTrue((table / "repository").exists())
        self.assertEqual(self.events, [])

    def test_gc_excludes_pending_recovery_even_without_external_id(self):
        table = self.add_job(status=JobStatus.FAILED)
        other = self.add_job("done", status=JobStatus.FAILED)
        with self.sessions() as db:
            job = db.get(Job, "job")
            job.slurm_job_id = None
            job.finalization_status = "FAILED"
            db.get(Job, "done").slurm_job_id = None
            db.commit()
        namespace = dict(self.namespace)
        namespace["magnus_config"] = {"server": {"scheduler": {"workspace_size_cap": "1"}}}
        namespace["_parse_size_string"] = int
        namespace["resource_manager"] = SimpleNamespace(builds=SimpleNamespace(diagnostics_root=self.root / "diagnostics"))
        load_mixin("_scheduler/_workspace_gc.py", namespace)
        gc = namespace["_WorkspaceGCMixin"]()
        gc.last_workspace_gc_time = datetime.min.replace(tzinfo=timezone.utc)
        gc.preparing_jobs = {}
        gc._preparing_jobs_lock = threading.Lock()
        gc._reclaim_workspace_over_cap()
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertFalse(other.exists())

    def test_preemption_resumes_only_after_persistent_recovery_clears(self):
        self.add_job(status=JobStatus.PAUSED)
        with self.sessions() as db:
            job = db.get(Job, "job")
            job.slurm_job_id = None
            job.finalization_status = "FAILED"
            db.commit()
        namespace = dict(self.namespace, asyncio=asyncio)
        async def run(function):
            function()
        namespace["_run_blocking_io"] = run
        decisions = load_method("_scheduler/_decisions.py", "_make_decisions", namespace)
        actor = SimpleNamespace(
            preparing_jobs={},
            _preparing_jobs_lock=threading.Lock(),
            _schedule_pending_jobs=lambda: None,
        )
        asyncio.run(decisions(actor))
        self.assertEqual(self.state()[0], JobStatus.PAUSED)
        with self.sessions() as db:
            db.get(Job, "job").finalization_status = None
            db.commit()
        asyncio.run(decisions(actor))
        self.assertEqual(self.state()[0], JobStatus.PREPARING)

    def test_docker_completion_keeps_existing_local_cleanup_behavior(self):
        table = self.add_job(status=JobStatus.QUEUED)
        self.scheduler.docker_manager = SimpleNamespace(
            check_container_status=lambda name: "COMPLETED",
            remove_container=lambda name: self.events.append(("remove", name)),
        )
        self.scheduler._docker_log_cursors = {}
        self.scheduler._dump_docker_logs = lambda *args, **kwargs: "cursor"
        self.scheduler._sync_reality_docker()
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, None, None))
        self.assertFalse((table / "repository").exists())
        self.assertIn(("remove", "magnus-job-job"), self.events)

    def test_migration_preserves_legacy_history_and_is_idempotent(self):
        engine = create_engine(f"sqlite:///{self.root / 'legacy.db'}")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, slurm_job_id TEXT, created_at TEXT, user_id TEXT, result TEXT)"))
            connection.execute(text("INSERT INTO jobs VALUES ('legacy', 'FAILED', NULL, '2026-10-19', 'owner', 'original evidence')"))
            connection.execute(text("CREATE TABLE explorer_sessions (id TEXT PRIMARY KEY)"))
        namespace = dict(
            self.namespace,
            engine=engine,
            _repair_legacy_job_metrics_reference=lambda: None,
            _make_job_container_image_nullable=lambda: None,
        )
        migrate = load_method("main.py", "run_migrations", namespace)
        migrate()
        migrate()
        with engine.connect() as connection:
            record = connection.execute(text("SELECT id, status, result, finalization_status FROM jobs")).one()
        self.assertEqual(tuple(record), ("legacy", "FAILED", "original evidence", None))
        self.assertIn("ix_jobs_finalization_status", {index["name"] for index in inspect(engine).get_indexes("jobs")})

    def test_resubmission_cannot_accept_stale_markers_after_failed_removal(self):
        table = self.add_job()
        marker = table / ".magnus_success"
        marker.write_text("previous attempt")
        namespace = dict(self.namespace)
        namespace["guarantee_file_exist"] = lambda path, **kwargs: Path(path).mkdir(parents=True, exist_ok=True)
        initialize = load_method("_scheduler/_submit.py", "_init_job_working_dir", namespace)
        with patch("os.remove", side_effect=PermissionError("marker is not writable")):
            with self.assertRaises(PermissionError):
                initialize(self.scheduler, str(table))
        self.assertTrue(marker.exists())
        initialize(self.scheduler, str(table))
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
