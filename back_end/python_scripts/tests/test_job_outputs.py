"""Read retained job output over HTTP without starting production singletons."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
import logging
import runpy
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Optional, Dict, Any
import unittest

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, Enum, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from test_job_finalization import JobStatus, SERVER
from test_protocol_files import open_protocol_file
from test_job_resources import load_schemas


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    has_dockerfile: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus))
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    task_name = "output recovery"
    user_id = "owner"
    namespace = "example"
    repo_name = "example"
    entry_command = "run"
    gpu_count = 0
    gpu_type = "cpu"
    cpu_count = 1
    memory_demand = "1G"
    resource_mode = "slurm_per_cpu"
    job_type = "A2"
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)


class JobOutputTests(unittest.TestCase):
    def setUp(
        self,
    ):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'state.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.table = self.root / "workspace" / "jobs" / "job"
        self.table.mkdir(parents=True)
        self.payload = '{"exit_code":1,"checkpoint":"retained"}'
        self.action = "echo inspect checkpoint"
        self.diagnostic = "Scheduler reported FAILED during execution"
        self.set_job(JobStatus.FAILED, self.diagnostic)

        def get_db():
            with self.sessions() as db:
                yield db
                # A read must stay harmless even if another dependency commits.
                db.commit()

        def get_user(authorization: str = Header(default="")):
            if authorization != "Bearer test":
                raise HTTPException(status_code=401)
            return SimpleNamespace(id="another-user")

        schemas = load_schemas()
        self.namespace = {
            "__name__": __name__,
            **{key: value for key, value in runpy.run_path(str(SERVER / "_staging_diagnostics.py")).items() if key in {"read_staging", "staging_message"}},
            "os": os,
            "open_protocol_file": open_protocol_file,
            "Optional": Optional,
            "Dict": Dict,
            "Any": Any,
            "Session": Session,
            "JobStatus": JobStatus,
            "models": SimpleNamespace(Job=Job, User=object),
            "JobResponse": schemas["JobResponse"],
            "database": SimpleNamespace(get_db=get_db),
            "get_current_user": get_user,
            "magnus_config": {"server": {"root": str(self.root)}},
            "MAX_MARKER_PREVIEW_SIZE": 1024 * 1024,
            "resource_manager": SimpleNamespace(builds=SimpleNamespace(get_log_path=lambda job: self.root / "build_diagnostics" / job / "output.txt")),
            "router": APIRouter(),
            "Depends": Depends,
            "HTTPException": HTTPException,
            "Query": Query,
            "logger": logging.getLogger(__name__),
        }
        source = SERVER / "routers" / "jobs.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {
            "_read_marker_file", "_resolve_marker_field", "get_job_detail",
            "get_job_result", "get_job_action", "get_job_logs_paginated", "_read_log_page", "_safe_utf8_truncate", "get_job_build_logs",
        }
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(tree, str(source), "exec"), self.namespace)
        app = FastAPI()
        app.include_router(self.namespace["router"], prefix="/api")
        self.http = TestClient(app)
        self.addCleanup(self.http.close)

    def set_job(
        self,
        status,
        result=None,
        action=None,
    ):
        with self.sessions() as db:
            job = db.get(Job, "job")
            if job is None:
                job = Job(id="job")
                db.add(job)
            job.status = status
            job.result = result
            job.action = action
            db.commit()

    def publish(
        self,
        result=None,
    ):
        (self.table / ".magnus_result").write_text(self.payload if result is None else result, encoding="utf-8")
        (self.table / ".magnus_action").write_text(self.action, encoding="utf-8")

    def get(
        self,
        suffix="",
    ):
        response = self.http.get(
            f"/api/jobs/job{suffix}",
            headers={"Authorization": "Bearer test"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_outputs(
        self,
        result,
        action,
    ):
        detail = self.get()
        self.assertEqual(detail["result"], result)
        self.assertEqual(detail["action"], action)
        self.assertEqual(self.get("/result"), result)
        self.assertEqual(self.get("/action"), action)
        return detail

    def test_protocol_links_cannot_expose_host_file_over_http(self):
        outside = self.root / "host-private"
        outside.mkdir()
        (outside / "output.txt").write_text("host-secret-canary")
        (self.table / ".magnus_result").symlink_to(outside / "output.txt")
        (self.table / "slurm").symlink_to(outside, target_is_directory=True)
        self.assertNotIn("host-secret-canary", self.get("/result"))
        self.assertNotIn("host-secret-canary", self.get("/logs")["logs"])

    def test_build_logs_use_their_own_protected_root(self):
        path = self.root / "build_diagnostics" / "job" / "output.txt"
        path.parent.mkdir(parents=True)
        path.write_text("building image")
        self.assertEqual(self.get("/build/logs")["logs"], "building image")
        path.write_text("a" * 300000 + "build done")
        self.assertTrue(self.get("/build/logs")["logs"].endswith("build done"))
        path.unlink()
        secret = self.root / "host-private"
        secret.write_text("host-secret-canary")
        path.symlink_to(secret)
        self.assertNotIn("host-secret-canary", self.get("/build/logs")["logs"])

    def test_log_pagination_remains_bounded(self):
        (self.table / "slurm").mkdir()
        (self.table / "slurm" / "output.txt").write_text("a" * 300000 + "last line")
        last = self.get("/logs")
        self.assertGreater(last["total_pages"], 1)
        self.assertLessEqual(len(last["logs"]), 200 * 1024)
        self.assertTrue(last["logs"].endswith("last line"))

    def test_failed_result_is_readable_without_rewriting_diagnostic_or_status(
        self,
    ):
        self.publish()
        detail = self.assert_outputs(self.payload, self.action)
        self.assertEqual(detail["status"], "Failed")
        with self.sessions() as db:
            job = db.get(Job, "job")
            self.assertEqual(job.status, JobStatus.FAILED)
            self.assertEqual(job.result, self.diagnostic)
            self.assertIsNone(job.action)

    def test_all_ended_executions_expose_output_without_a_database_sentinel(
        self,
    ):
        self.publish()
        for status in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TERMINATED, JobStatus.PAUSED):
            with self.subTest(status=status):
                self.set_job(status)
                self.assertEqual(self.assert_outputs(self.payload, self.action)["status"], status.value)

    def test_absent_or_evicted_outputs_keep_inline_diagnostic_and_action(
        self,
    ):
        self.set_job(JobStatus.FAILED, self.diagnostic, "legacy action")
        self.assert_outputs(self.diagnostic, "legacy action")
        self.publish()
        self.assert_outputs(self.payload, self.action)
        for marker in self.table.iterdir():
            marker.unlink()
        self.assert_outputs(self.diagnostic, "legacy action")

    def test_old_success_sentinels_and_missing_files_keep_optional_string_contract(
        self,
    ):
        self.set_job(JobStatus.SUCCESS, ".magnus_result", ".magnus_action")
        self.assert_outputs(None, None)
        self.publish()
        self.assert_outputs(self.payload, self.action)
        self.publish("")
        self.assert_outputs("", self.action)

    def test_action_does_not_require_a_result_marker(
        self,
    ):
        (self.table / ".magnus_action").write_text(self.action, encoding="utf-8")
        self.assert_outputs(self.diagnostic, self.action)

    def test_active_attempts_do_not_expose_partial_or_previous_markers(
        self,
    ):
        self.publish()
        for status in (JobStatus.PREPARING, JobStatus.BUILDING, JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING):
            with self.subTest(status=status):
                self.set_job(status)
                self.assert_outputs(None, None)

    def test_preview_remains_bounded_and_invalid_utf8_is_readable(
        self,
    ):
        path = self.table / ".magnus_result"
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        result = self.get("/result")
        self.assertLess(len(result), 1024 * 1024 + 100)
        self.assertIn("Content truncated", result)
        path.write_bytes(b"partial\xffresult")
        self.assertEqual(self.get("/result"), "partial\ufffdresult")

    def test_non_file_marker_does_not_hide_diagnostic(
        self,
    ):
        (self.table / ".magnus_result").mkdir()
        self.assert_outputs(self.diagnostic, None)

    def test_read_permissions_remain_authenticated_and_cross_user(
        self,
    ):
        self.publish()
        for suffix in ("", "/result", "/action"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.http.get(f"/api/jobs/job{suffix}").status_code, 401)
                self.assertEqual(self.http.get(f"/api/jobs/missing{suffix}", headers={"Authorization": "Bearer test"}).status_code, 404)
        self.assert_outputs(self.payload, self.action)


if __name__ == "__main__":
    unittest.main()
