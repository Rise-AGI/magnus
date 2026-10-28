"""Terminal computation and durable artifact recovery have separate lifetimes."""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import logging
import importlib.util
import sys
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from test_protocol_files import open_protocol_file

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
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus))
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    finalization_status: Mapped[str | None] = mapped_column(String, nullable=True)
    submission_attempt: Mapped[str | None] = mapped_column(String, nullable=True)
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
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or (not node.level and node.module != "pywheels.file_tools")]
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
        self.release_overrides = {}
        self.release_checks = []
        self.marker_failure = set()
        self.transfer_failure = set()
        self.cleanup_failure = set()
        self.success = set()
        self.marker_hook = None
        self.final_hook = None
        self.namespace = {
            "__name__": __name__,
            "threading": threading,
            "open_protocol_file": open_protocol_file,
            "SessionLocal": self.sessions,
            "Session": object,
            "Job": Job,
            "JobStatus": JobStatus,
            "ClusterSnapshot": object,
            "logger": logging.getLogger("finalization-test"),
            "magnus_workspace_path": str(self.root),
            "magnus_ephemeral_workspace_path": str(self.root),
            "is_local_mode": False,
            "magnus_config": {},
            "remove_job_path": remove,
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
        actor._job_query = lambda db, *entities: db.query(*entities)
        actor._finalization_retry_after = {}
        actor._test_artifact_retry = {}
        actor._preparing_jobs_lock = threading.Lock()
        actor._submitting_jobs = set()
        actor._submission_cancel_events = {}
        actor._submission_stopping = False
        actor._staged_job_ids = set()
        actor._submission_deferred = lambda: False
        actor._reconcile_submissions = lambda: None
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
        def observe(identifier):
            self.release_checks.append(identifier)
            result = self.statuses.get(identifier, "UNKNOWN")
            if isinstance(result, Exception):
                raise result
            released = self.release_overrides.get(identifier, result in {"FAILED", "COMPLETED", "CANCELLED", "TIMEOUT"})
            return SimpleNamespace(status=result, allocation_released=released)
        actor.slurm_manager = SimpleNamespace(check_job_status=status, observe_job=observe)
        actor._stage_out_markers = markers
        actor._stage_out_final = final
        actor._custody_relay = SimpleNamespace(forget=lambda job: None)
        actor._stage_out_custody = lambda identifier: True
        actor._stage_out_logs = lambda identifier: self.events.append(("logs", identifier))
        actor._cleanup_remote_job = remote_cleanup
        return actor

    def sync(self, actor=None):
        actor = actor or self.scheduler
        candidates = actor._sync_reality_slurm()
        now = self.namespace["monotonic"]()
        for identifier, external in candidates.items():
            if now < actor._test_artifact_retry.get(identifier, 0):
                continue
            delay = actor._recover_job_artifacts(identifier, external)
            actor._test_artifact_retry[identifier] = now + delay

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

    def test_observation_commits_terminal_status_without_entering_bulk_recovery(self):
        table = self.add_job(observed="TIMEOUT")
        self.scheduler._stage_out_final = lambda identifier: self.fail("bulk recovery blocked observations")
        candidates = self.scheduler._sync_reality_slurm()
        self.assertEqual(candidates, {"job": "slurm-job"})
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "TIMEOUT"))
        self.assertTrue((table / "repository" / "checkpoint").exists())

    def test_classified_job_does_not_refetch_markers_during_recovery(self):
        self.add_job(observed="TIMEOUT")
        self.scheduler._sync_reality_slurm()
        self.marker_failure.add("job")
        self.scheduler._classify_slurm_job = lambda *args: self.fail("classified row was rewritten")
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))

    def test_terminal_status_precedes_transfer_and_another_job_keeps_progressing(self):
        table = self.add_job()
        other = self.add_job("other")
        self.transfer_failure.add("job")
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "FAILED"))
        self.assertIn(("transfer", "job", JobStatus.FAILED), self.events)
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertEqual(self.state("other")[:3], (JobStatus.FAILED, None, None))
        self.assertFalse((other / "repository").exists())

    def test_restart_rechecks_release_without_reclassifying_terminal_execution(self):
        table = self.add_job()
        self.transfer_failure.add("job")
        self.sync()
        self.transfer_failure.clear()
        self.statuses.clear()
        self.release_overrides["slurm-job"] = True
        self.events.clear()
        self.sync(self.new_scheduler())
        self.assertFalse(any(event[0] == "query" for event in self.events))
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))
        self.assertFalse((table / "repository").exists())
        self.assertEqual((table / "slurm" / "output.txt").read_text(), "complete log")

    def test_pending_retry_waits_without_delaying_first_terminal_classification(self):
        self.add_job()
        now = [100.0]
        self.namespace["monotonic"] = lambda: now[0]
        self.transfer_failure.add("job")
        self.sync()
        self.assertEqual(self.state()[0], JobStatus.FAILED)
        self.transfer_failure.clear()
        self.events.clear()
        now[0] = 159.0
        self.sync()
        self.assertEqual(self.events, [])
        self.assertEqual(self.state()[2], "FAILED")
        now[0] = 160.0
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))
        self.assertEqual(self.scheduler._finalization_retry_after, {})

    def test_unknown_and_completing_do_not_end_or_clean_a_job(self):
        table = self.add_job(observed="UNKNOWN")
        for observed in ("UNKNOWN", "RUNNING"):
            self.statuses["slurm-job"] = observed
            self.sync()
            self.assertEqual(self.state()[:3], (JobStatus.RUNNING, "slurm-job", None))
            self.assertTrue((table / "repository").exists())
        self.assertFalse(any(event[0] == "cleanup" for event in self.events))

    def test_unresolved_submission_protects_cancelled_workspace(self):
        table = self.add_job(status=JobStatus.TERMINATED)
        with self.sessions() as db:
            job = db.get(Job, "job")
            job.slurm_job_id = None
            job.submission_attempt = '{"comment":"unknown"}'
            job.finalization_status = "CLEANUP"
            db.commit()
        self.assertFalse(self.scheduler._clean_up_working_table("job"))
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertFalse(any(event[0] == "cleanup" for event in self.events))

    def test_cleanup_imports_runtime_dependencies_without_injected_globals(self):
        table = self.add_job(status=JobStatus.FAILED)
        with self.sessions() as db:
            db.get(Job, "job").finalization_status = "CLEANUP"
            db.commit()
        root_name = "isolated_finalization_import"
        root = ModuleType(root_name)
        root.__path__ = [str(SERVER)]
        package = ModuleType(f"{root_name}._scheduler")
        package.__path__ = [str(SERVER / "_scheduler")]
        package.logger = self.namespace["logger"]
        package.magnus_workspace_path = str(self.root)
        package.magnus_ephemeral_workspace_path = str(self.root)
        modules = {root_name: root, package.__name__: package}
        for name, values in {
            "_magnus_config": {"is_local_mode": False},
            "_protocol_files": {"open_protocol_file": open_protocol_file},
            "_workspace_cleanup": {"remove_job_path": remove},
            "database": {"SessionLocal": self.sessions},
            "models": {"Job": Job, "JobStatus": JobStatus},
        }.items():
            dependency = ModuleType(f"{root_name}.{name}")
            vars(dependency).update(values)
            modules[dependency.__name__] = dependency
        spec = importlib.util.spec_from_file_location(
            f"{package.__name__}._job_lifecycle",
            SERVER / "_scheduler" / "_job_lifecycle.py",
        )
        module = importlib.util.module_from_spec(spec)
        modules[spec.name] = module
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
            actor = module._JobLifecycleMixin()
            actor._preparing_jobs_lock = threading.Lock()
            actor._submitting_jobs = set()
            actor._staged_job_ids = set()
            observed = SimpleNamespace(allocation_released=False)
            actor.slurm_manager = SimpleNamespace(observe_job=lambda identifier: observed)
            actor._cleanup_remote_job = lambda identifier: None
            self.assertFalse(actor._clean_up_working_table("job"))
            self.assertTrue((table / "repository" / "checkpoint").exists())
            observed.allocation_released = True
            self.assertTrue(actor._clean_up_working_table("job"))
            self.assertFalse((table / "repository").exists())

    def test_terminal_execution_still_holds_allocation_and_incremental_outputs(self):
        table = self.add_job()
        self.release_overrides["slurm-job"] = False
        self.scheduler._stage_out_custody = lambda identifier: self.events.append(("incremental", identifier))
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "FAILED"))
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertIn(("incremental", "job"), self.events)
        self.assertIn(("logs", "job"), self.events)
        self.assertFalse(any(event[0] in {"transfer", "cleanup"} for event in self.events))
        self.release_overrides["slurm-job"] = True
        self.sync(self.new_scheduler())
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))
        self.assertFalse((table / "repository").exists())

    def test_durable_cleanup_waits_when_allocation_is_held_or_unknown_after_restart(self):
        table = self.add_job(status=JobStatus.FAILED)
        with self.sessions() as db:
            db.get(Job, "job").finalization_status = "CLEANUP"
            db.commit()
        for state in ("RUNNING", "PENDING", "UNKNOWN"):
            with self.subTest(state=state):
                self.statuses["slurm-job"] = state
                self.sync(self.new_scheduler())
                self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "CLEANUP"))
                self.assertTrue((table / "repository" / "checkpoint").exists())
        self.statuses["slurm-job"] = "COMPLETED"
        self.sync(self.new_scheduler())
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))

    def test_cleanup_rechecks_release_after_final_transfer(self):
        table = self.add_job()
        self.final_hook = lambda identifier: self.release_overrides.update({"slurm-job": False})
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "CLEANUP"))
        self.assertTrue((table / "repository" / "checkpoint").exists())
        self.assertFalse(any(event[0] == "cleanup" for event in self.events))

    def test_success_marker_wins_over_cancel_and_completion_without_marker_fails(self):
        self.add_job("success", observed="CANCELLED")
        self.add_job("missing", status=JobStatus.QUEUED, observed="COMPLETED")
        self.success.add("success")
        self.sync()
        self.assertEqual(self.state("success")[:3], (JobStatus.SUCCESS, None, None))
        self.assertEqual(self.state("missing")[0], JobStatus.FAILED)
        self.assertIn("no success marker", self.state("missing")[3])

    def test_marker_transport_failure_is_not_a_missing_marker_and_is_durable(self):
        table = self.add_job(observed="COMPLETED")
        self.marker_failure.add("job")
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.RUNNING, "slurm-job", "COMPLETED"))
        self.assertFalse(any(event[0] == "transfer" for event in self.events))
        self.assertTrue((table / "repository").exists())
        self.marker_failure.clear()
        self.statuses.clear()
        self.release_overrides["slurm-job"] = True
        self.success.add("job")
        self.sync(self.new_scheduler())
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, None, None))

    def test_cleanup_retry_does_not_reinterpret_deleted_success_marker(self):
        table = self.add_job(observed="COMPLETED")
        self.success.add("job")
        self.cleanup_failure.add("job")
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, "slurm-job", "CLEANUP"))
        self.assertFalse((table / ".magnus_success").exists())
        self.cleanup_failure.clear()
        self.events.clear()
        self.sync(self.new_scheduler())
        self.assertEqual(self.state()[:3], (JobStatus.SUCCESS, None, None))
        self.assertEqual(self.events, [("cleanup", "job")])

    def test_paused_and_terminated_stay_owned_until_artifacts_are_safe(self):
        for status in (JobStatus.PAUSED, JobStatus.TERMINATED):
            identifier = status.value
            table = self.add_job(identifier, status=status, observed="CANCELLED")
            self.transfer_failure.add(identifier)
            self.sync()
            self.assertEqual(self.state(identifier)[:3], (status, f"slurm-{identifier}", "CANCELLED"))
            self.assertTrue((table / "repository").exists())
            self.transfer_failure.remove(identifier)
            self.sync(self.new_scheduler())
            self.assertEqual(self.state(identifier)[:3], (status, None, None))

    def test_cancel_during_marker_read_does_not_resurrect_success(self):
        self.add_job(observed="COMPLETED")
        self.success.add("job")
        def cancel(identifier):
            with self.sessions() as db:
                db.get(Job, identifier).status = JobStatus.TERMINATED
                db.commit()
        self.marker_hook = cancel
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, None, None))

    def test_failed_executions_retain_user_outputs_and_scheduler_diagnostic(
        self,
    ):
        for observed in ("FAILED", "TIMEOUT", "CANCELLED", "COMPLETED"):
            with self.subTest(observed=observed):
                table = self.add_job(observed, observed=observed)
                (table / ".magnus_result").write_text('{"exit_code":1}')
                (table / ".magnus_action").write_text("echo recover checkpoint")
                self.sync()
                self.assertEqual(self.state(observed)[:3], (JobStatus.FAILED, None, None))
                self.assertTrue(self.state(observed)[3])
                self.assertEqual((table / ".magnus_result").read_text(), '{"exit_code":1}')
                self.assertEqual((table / ".magnus_action").read_text(), "echo recover checkpoint")
                self.assertFalse((table / "repository").exists())

    def test_cancelled_marker_failure_retains_workspace_and_retries_after_restart(
        self,
    ):
        for status in (JobStatus.PAUSED, JobStatus.TERMINATED):
            with self.subTest(status=status):
                identifier = status.value
                table = self.add_job(identifier, status=status, observed="CANCELLED")
                self.marker_failure.add(identifier)
                self.scheduler._sync_reality_slurm()
                with self.assertRaisesRegex(RuntimeError, "marker transport unavailable"):
                    self.scheduler._recover_job_artifacts(identifier, f"slurm-{identifier}")
                self.assertEqual(self.state(identifier)[:3], (status, f"slurm-{identifier}", "CANCELLED"))
                self.assertTrue((table / "repository" / "checkpoint").exists())
                self.assertFalse(any(event[0] == "cleanup" and event[1] == identifier for event in self.events))
                self.marker_failure.remove(identifier)

                def receive(job_id):
                    destination = self.root / "jobs" / job_id
                    (destination / ".magnus_result").write_text("checkpoint")
                    (destination / ".magnus_action").write_text("echo recover")

                self.marker_hook = receive
                self.statuses.clear()
                self.release_overrides[f"slurm-{identifier}"] = True
                self.sync(self.new_scheduler())
                self.assertEqual(self.state(identifier)[:3], (status, None, None))
                self.assertEqual((table / ".magnus_result").read_text(), "checkpoint")
                self.assertEqual((table / ".magnus_action").read_text(), "echo recover")

    def test_termination_during_staging_waits_for_submission_worker_cleanup(self):
        table = self.add_job(status=JobStatus.PENDING)
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        terminate = load_method("_scheduler/_core.py", "terminate_job", self.namespace)
        submit = load_method("_scheduler/_decisions.py", "_submit_jobs", self.namespace)
        actor = self.scheduler
        actor.enabled = True
        actor.preparing_jobs = {}
        actor._loop = None
        def staging(db, job):
            self.assertEqual(actor._submitting_jobs, {"job"})
            with self.sessions() as other:
                terminate(actor, other, other.get(Job, "job"))
            self.assertTrue(actor._submission_cancel_events["job"].is_set())
            self.assertTrue((table / "repository" / "checkpoint").exists())
            self.assertFalse(actor._clean_up_working_table("job"))
        actor._submit_to_slurm = staging
        submit(actor, ["job"])
        self.assertEqual(actor._submitting_jobs, set())
        self.assertEqual(actor._submission_cancel_events, {})
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, None, "CLEANUP"))
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, None, None))
        self.assertFalse((table / "repository").exists())

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
        self.namespace["remove_job_path"] = lambda path: None
        self.sync()
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, "slurm-job", "CLEANUP"))
        self.assertFalse(any(event[0] == "cleanup" for event in self.events))
        self.namespace["remove_job_path"] = remove
        self.sync(self.new_scheduler())
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
        self.sync()
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
                self.sync()
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
        self.sync()
        self.assertEqual(self.state()[0], JobStatus.FAILED)
        self.assertIn("Out of memory", self.state()[3])

    def test_terminate_pending_recovery_does_not_cancel_or_clean_it(self):
        table = self.add_job()
        self.transfer_failure.add("job")
        self.sync()
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
        gc.inflight_job_ids = lambda: set(gc.preparing_jobs) | gc._submitting_jobs
        gc.last_workspace_gc_time = datetime.min.replace(tzinfo=timezone.utc)
        gc.preparing_jobs = {}
        gc._submitting_jobs = set()
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
        async def run(function, *args):
            return function(*args)
        namespace["_run_blocking_io"] = run
        decisions = load_method("_scheduler/_decisions.py", "_make_decisions", namespace)
        actor = SimpleNamespace(
            _job_query=lambda db, *entities: db.query(*entities),
            preparing_jobs={},
            _preparing_jobs_lock=threading.Lock(),
            _queue_pending_submission=lambda: None,
            _resume_paused_job=self.scheduler._resume_paused_job,
        )
        self.scheduler._init_job_working_dir = lambda path: None
        asyncio.run(decisions(actor))
        self.assertEqual(self.state()[0], JobStatus.PAUSED)
        with self.sessions() as db:
            db.get(Job, "job").finalization_status = None
            db.commit()
        asyncio.run(decisions(actor))
        self.assertEqual(self.state()[0], JobStatus.PREPARING)

    def test_resumed_attempt_cannot_return_previous_markers_after_preparation_fails(
        self,
    ):
        table = self.add_job(status=JobStatus.PAUSED)
        for marker in (".magnus_success", ".magnus_result", ".magnus_action", ".magnus_oom"):
            (table / marker).write_text("previous attempt")
        with self.sessions() as db:
            job = db.get(Job, "job")
            job.slurm_job_id = None
            job.result = "previous inline result"
            job.action = "previous inline action"
            db.commit()
        initialize = load_method("_scheduler/_submit.py", "_init_job_working_dir", self.namespace)
        self.namespace["guarantee_file_exist"] = lambda path, **kwargs: Path(path).mkdir(parents=True, exist_ok=True)
        self.scheduler._init_job_working_dir = lambda path: initialize(self.scheduler, path)
        self.assertTrue(self.scheduler._resume_paused_job("job"))
        self.assertEqual(self.state(), (JobStatus.PREPARING, None, None, None))
        with self.sessions() as db:
            job = db.get(Job, "job")
            self.assertIsNone(job.action)
            job.status = JobStatus.FAILED
            job.result = "new preparation failure"
            db.commit()
        self.scheduler._clean_up_working_table("job")
        self.assertEqual(self.state()[3], "new preparation failure")
        self.assertFalse(any((table / marker).exists() for marker in (".magnus_result", ".magnus_action")))

    def test_failed_or_cancelled_resume_does_not_start_another_attempt(
        self,
    ):
        table = self.add_job(status=JobStatus.PAUSED)
        (table / ".magnus_result").write_text("previous attempt")
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        def fail(path):
            raise OSError("cannot clear prior output")
        self.scheduler._init_job_working_dir = fail
        with self.assertRaises(OSError):
            self.scheduler._resume_paused_job("job")
        self.assertEqual(self.state()[0], JobStatus.PAUSED)
        self.assertEqual((table / ".magnus_result").read_text(), "previous attempt")
        with self.sessions() as db:
            db.get(Job, "job").status = JobStatus.TERMINATED
            db.commit()
        self.scheduler._init_job_working_dir = lambda path: self.fail("cancelled job lost its output")
        self.assertFalse(self.scheduler._resume_paused_job("job"))
        self.assertEqual(self.state()[0], JobStatus.TERMINATED)
        self.assertEqual((table / ".magnus_result").read_text(), "previous attempt")

    def test_partial_marker_reset_failure_restores_previous_outputs(
        self,
    ):
        table = self.add_job(status=JobStatus.PAUSED)
        names = (".magnus_success", ".magnus_result", ".magnus_action", ".magnus_oom")
        for name in names:
            (table / name).write_text(name)
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        replace = os.replace

        def fail_later(source, destination):
            if Path(source) == table / ".magnus_action":
                raise OSError("injected move failure after result was moved")
            return replace(source, destination)

        self.scheduler._init_job_working_dir = lambda path: None
        with patch.object(os, "replace", side_effect=fail_later), self.assertRaises(OSError):
            self.scheduler._resume_paused_job("job")
        self.assertEqual(self.state()[0], JobStatus.PAUSED)
        for name in names:
            self.assertEqual((table / name).read_text(), name)
        self.assertEqual(list(table.glob(".magnus-output-reset-*")), [])

    def test_interrupted_marker_reset_keeps_backup_and_blocks_new_attempt(
        self,
    ):
        table = self.add_job(status=JobStatus.PAUSED)
        backup = table / ".magnus-output-reset-interrupted"
        backup.mkdir()
        (backup / ".magnus_result").write_text("retained output")
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "needs recovery"):
            self.scheduler._resume_paused_job("job")
        self.assertEqual(self.state()[0], JobStatus.PAUSED)
        self.assertEqual((backup / ".magnus_result").read_text(), "retained output")
        with self.sessions() as db:
            db.get(Job, "job").status = JobStatus.TERMINATED
            db.commit()
        self.scheduler._clean_up_working_table("job")
        self.assertEqual((backup / ".magnus_result").read_text(), "retained output")

    def test_build_pin_release_rechecks_submission_ownership_after_candidate_snapshot(self):
        self.add_job(status=JobStatus.PENDING)
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        actor = self.scheduler
        actor.preparing_jobs = {}
        actor._build_pinned_jobs = {"job"}
        released = []
        def begin_submission_before_terminal_query():
            actor._submitting_jobs.add("job")
            actor._submission_cancel_events["job"] = threading.Event()
            with self.sessions() as db:
                db.get(Job, "job").status = JobStatus.TERMINATED
                db.commit()
            return self.sessions()
        namespace = dict(self.namespace)
        namespace["SessionLocal"] = begin_submission_before_terminal_query
        namespace["resource_manager"] = SimpleNamespace(builds=SimpleNamespace(release=released.append))
        release = load_method("_scheduler/_core.py", "_release_finished_builds", namespace)
        release(actor)
        self.assertEqual(released, [])
        self.assertEqual(actor._build_pinned_jobs, {"job"})
        actor._submitting_jobs.clear()
        namespace["SessionLocal"] = self.sessions
        release(actor)
        self.assertEqual(released, ["job"])
        self.assertEqual(actor._build_pinned_jobs, set())

    def test_staging_failure_is_committed_before_cleanup_and_retried_without_slurm_id(self):
        table = self.add_job(status=JobStatus.PENDING)
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        fail = load_method("_scheduler/_submit.py", "_fail_job_before_submission", self.namespace)
        with self.sessions() as db:
            fail(self.scheduler, db, "job", "staging timeout")
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, "CLEANUP"))
        self.assertTrue((table / "repository").exists())
        self.cleanup_failure.add("job")
        self.sync()
        self.assertEqual(self.state()[2], "CLEANUP")
        self.cleanup_failure.clear()
        self.sync(self.new_scheduler())
        self.assertEqual(self.state()[:3], (JobStatus.FAILED, None, None))

    def test_setup_error_after_user_termination_keeps_termination_and_schedules_cleanup(self):
        for local in (False, True):
            with self.subTest(local=local):
                identifier = "docker" if local else "slurm"
                self.add_job(identifier, status=JobStatus.PENDING)
                with self.sessions() as db:
                    db.get(Job, identifier).slurm_job_id = None
                    db.commit()
                namespace = dict(self.namespace, is_local_mode=local)
                namespace["traceback"] = __import__("traceback")
                actor = self.new_scheduler()
                fail = load_method("_scheduler/_submit.py", "_fail_job_before_submission", namespace)
                actor._fail_job_before_submission = lambda db, job_id, message: fail(actor, db, job_id, message)
                def setup(db, job):
                    with self.sessions() as other:
                        other.get(Job, identifier).status = JobStatus.TERMINATED
                        other.commit()
                    raise OSError("setup failed after cancellation")
                actor._built_environment_ready = setup
                actor.docker_manager = object()
                name = "_submit_to_docker" if local else "_submit_to_slurm"
                operation = load_method("_scheduler/_submit.py", name, namespace)
                setattr(actor, name, lambda db, job: operation(actor, db, job))
                submit = load_method("_scheduler/_decisions.py", "_submit_jobs", namespace)
                with self.assertLogs("finalization-test", level="ERROR"):
                    submit(actor, [identifier])
                self.assertEqual(self.state(identifier)[:3], (JobStatus.TERMINATED, None, "CLEANUP"))
                self.assertEqual(actor._submitting_jobs, set())

    def test_staging_failure_cannot_overwrite_concurrent_termination(self):
        self.add_job(status=JobStatus.TERMINATED)
        with self.sessions() as db:
            db.get(Job, "job").slurm_job_id = None
            db.commit()
        fail = load_method("_scheduler/_submit.py", "_fail_job_before_submission", self.namespace)
        with self.sessions() as db:
            fail(self.scheduler, db, "job", "staging timeout")
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, None, None))

    def test_local_mode_dispatches_deferred_submission_cleanup(self):
        table = self.add_job(status=JobStatus.TERMINATED)
        with self.sessions() as db:
            current = db.get(Job, "job")
            current.slurm_job_id = None
            current.finalization_status = "CLEANUP"
            db.commit()
        self.namespace["is_local_mode"] = True
        self.scheduler._sync_reality_docker = lambda: None
        self.assertEqual(self.scheduler._sync_reality(), {"job": None})
        self.scheduler._recover_job_artifacts("job", None)
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, None, None))
        self.assertFalse((table / "repository").exists())

    def test_docker_failure_and_timeout_keep_markers_and_failure_state(
        self,
    ):
        self.scheduler._docker_log_cursors = {}
        self.scheduler._dump_docker_logs = lambda *args, **kwargs: "cursor"
        self.scheduler._docker_job_timed_out = lambda job: True
        for status, observed, oom in (
            (JobStatus.QUEUED, "FAILED", False),
            (JobStatus.RUNNING, "FAILED", True),
            (JobStatus.RUNNING, "MISSING", False),
            (JobStatus.RUNNING, "RUNNING", False),
        ):
            with self.subTest(status=status, observed=observed, oom=oom):
                identifier = f"docker-{status.value}-{observed}"
                table = self.add_job(identifier, status=status)
                (table / ".magnus_result").write_text("partial result")
                (table / ".magnus_action").write_text("echo recover")
                self.scheduler.docker_manager = SimpleNamespace(
                    check_container_status=lambda name: observed,
                    get_termination_info=lambda name: {"oom_killed": oom},
                    stop_container=lambda name: None,
                    remove_container=lambda name: None,
                )
                with patch.object(Job, "time_limit", 1, create=True):
                    self.scheduler._sync_reality_docker()
                self.assertEqual(self.state(identifier)[:3], (JobStatus.FAILED, None, None))
                self.assertTrue(self.state(identifier)[3])
                self.assertEqual((table / ".magnus_result").read_text(), "partial result")
                self.assertEqual((table / ".magnus_action").read_text(), "echo recover")
                self.assertFalse((table / "repository").exists())
                if oom:
                    self.assertIn("Out of memory", self.state(identifier)[3])

    def test_cancelled_pending_allocation_is_a_retry_candidate(self):
        self.add_job(status=JobStatus.TERMINATED, observed="PENDING")
        cancellations = []
        self.scheduler.terminate_job = lambda db, job: cancellations.append(job.slurm_job_id)
        for _ in range(3):
            candidates = self.scheduler._sync_reality_slurm()
            self.assertEqual(candidates, {"job": "slurm-job"})
            self.assertEqual(self.scheduler._recover_job_artifacts("job", "slurm-job"), 30)
        self.assertEqual(cancellations, ["slurm-job"] * 3)
        self.assertEqual(self.state()[:3], (JobStatus.TERMINATED, "slurm-job", None))

    def test_unknown_docker_state_or_failed_removal_retains_allocation_and_workspace(self):
        table = self.add_job(status=JobStatus.RUNNING)
        self.scheduler._docker_log_cursors = {}
        self.scheduler._dump_docker_logs = lambda *args, **kwargs: "cursor"
        self.scheduler._docker_job_timed_out = lambda job: True
        for observed in ("UNKNOWN", "FAILED", "COMPLETED", "RUNNING"):
            def reject(*args):
                raise RuntimeError("daemon unavailable")
            self.scheduler.docker_manager = SimpleNamespace(
                check_container_status=lambda name: observed,
                get_termination_info=lambda name: {"oom_killed": False},
                stop_container=reject,
                remove_container=reject,
            )
            with self.subTest(observed=observed), patch.object(Job, "time_limit", 1, create=True):
                with self.sessions() as db:
                    current = db.get(Job, "job")
                    current.status = JobStatus.RUNNING
                    current.finalization_status = None
                    db.commit()
                self.scheduler._finalization_retry_after.clear()
                self.scheduler._sync_reality_docker()
                status = {"FAILED": JobStatus.FAILED, "COMPLETED": JobStatus.SUCCESS}.get(observed, JobStatus.RUNNING)
                finalization = observed if observed in ("FAILED", "COMPLETED") else None
                self.assertEqual(self.state()[:3], (status, "slurm-job", finalization))
                self.assertTrue((table / "repository").exists())

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
            connection.execute(text("CREATE TABLE services (id TEXT PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE cluster_snapshots (id INTEGER PRIMARY KEY)"))
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
