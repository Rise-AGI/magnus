"""Remote artifact failures must remain retryable and never trigger cleanup."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_staging():
    path = SERVER / "_scheduler" / "_staging.py"
    namespace = {"__name__": __name__, "__file__": str(path), "logger": logging.getLogger("stage-test")}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class ArtifactTransport:
    is_remote = True

    def __init__(self):
        self.failed_leaf = None
        self.query_failure = False

    def run(self, command, **kwargs):
        if self.query_failure:
            return SimpleNamespace(returncode=255, stdout="", stderr="connection lost")
        path = Path(command[-1])
        return SimpleNamespace(returncode=0, stdout=json.dumps(os.listdir(path) if path.exists() else None))

    def fetch(self, remote, local):
        if Path(remote).name == self.failed_leaf:
            Path(local).write_bytes(b"partial")
            raise RuntimeError("connection lost mid-transfer")
        if Path(remote).is_dir():
            shutil.copytree(remote, local)
        else:
            shutil.copyfile(remote, local)


class StageOutTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_staging()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.transport = ArtifactTransport()
        self.scheduler = self.namespace["_StagingMixin"]()
        self.scheduler.slurm_manager = SimpleNamespace(transport=self.transport, check_job_status=lambda identifier: "COMPLETED")
        self.scheduler._remote_job_working_table = lambda job: str(self.root / "remote" / job)
        self.scheduler._local_job_working_table = lambda job: str(self.root / "local" / job)
        self.namespace["file_custody_manager"] = SimpleNamespace(store_file=lambda **kwargs: None)

    def test_optional_absence_succeeds_but_transport_failure_propagates(self):
        self.scheduler._stage_out_final("empty")
        self.transport.query_failure = True
        with self.assertRaises(RuntimeError):
            self.scheduler._stage_out_final("empty")

    def test_partial_fetch_keeps_previous_log_and_remote_original(self):
        remote = Path(self.scheduler._remote_job_working_table("job")) / "slurm" / "output.txt"
        local = Path(self.scheduler._local_job_working_table("job")) / "slurm" / "output.txt"
        remote.parent.mkdir(parents=True)
        local.parent.mkdir(parents=True)
        remote.write_text("complete log")
        local.write_text("previous log")
        self.transport.failed_leaf = "output.txt"
        with self.assertRaises(RuntimeError):
            self.scheduler._stage_out_final("job")
        self.assertEqual(local.read_text(), "previous log")
        self.assertEqual(remote.read_text(), "complete log")
        self.assertEqual(list(local.parent.iterdir()), [local])
        self.transport.failed_leaf = None
        self.scheduler._stage_out_final("job")
        self.assertEqual(local.read_text(), "complete log")

    def test_custody_registration_failure_is_retryable_without_nested_copy(self):
        token = "relay-" + "a" * 32
        entry = Path(self.scheduler._remote_job_working_table("job")) / ".magnus_custody_drop" / token
        entry.mkdir(parents=True)
        (entry / "payload").write_bytes(b"artifact")
        (entry / "meta.json").write_text(json.dumps({"token": token, "filename": "payload"}))
        def fail(**kwargs):
            raise RuntimeError("custody temporarily full")
        self.namespace["file_custody_manager"].store_file = fail
        with self.assertRaises(RuntimeError):
            self.scheduler._stage_out_final("job")
        received = []
        self.namespace["file_custody_manager"].store_file = lambda **kwargs: received.append(kwargs["file_obj"].read())
        self.scheduler._stage_out_final("job")
        self.assertEqual(received, [b"artifact"])
        self.assertTrue(entry.exists())

    def test_one_failed_job_does_not_finalize_it_or_starve_other_jobs(self):
        class Field:
            def __init__(self, name): self.name = name
            def __eq__(self, value): return (self.name, value)
            def in_(self, value): return (self.name, value)
            def isnot(self, value): return (self.name, value)
        states = SimpleNamespace(**{name: name for name in ("QUEUED", "RUNNING", "TERMINATED", "PAUSED", "SUCCESS", "FAILED")})
        cases = [(initial, result) for initial in (states.QUEUED, states.RUNNING, states.TERMINATED, states.PAUSED) for result in ("COMPLETED", "FAILED")]
        for initial, result in cases:
            with self.subTest(initial=initial, result=result):
                self.scheduler.slurm_manager.check_job_status = lambda identifier: result
                self.scheduler._has_oom_marker = lambda job: False
                jobs = {name: SimpleNamespace(id=name, status=initial, slurm_job_id=name, memory_demand="1G") for name in ("bad", "good")}
                class Query:
                    def filter(self, *predicates):
                        self.predicates = predicates
                        return self
                    def all(self):
                        status = self.predicates[0][1]
                        allowed = status if isinstance(status, list) else [status]
                        return [job for job in jobs.values() if job.status in allowed]
                    def first(self): return jobs.get(self.predicates[0][1])
                class Session:
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def query(self, *args): return Query()
                    def commit(self): pass
                cleaned = []
                def stage(job):
                    if job == "bad": raise RuntimeError("connection lost mid-transfer")
                def finalize(job):
                    job.status = states.SUCCESS
                    job.slurm_job_id = None
                    cleaned.append(job.id)
                self.scheduler._stage_out_final = stage
                self.scheduler._finalize_completed_job = finalize
                self.scheduler._clean_up_working_table = cleaned.append
                path = SERVER / "_scheduler" / "_sync.py"
                tree = ast.parse(path.read_text(encoding="utf-8"))
                cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_SyncMixin")
                method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_sync_reality_slurm")
                namespace = {"SessionLocal": Session, "Job": SimpleNamespace(id=Field("id"), status=Field("status"), slurm_job_id=Field("slurm_job_id")), "JobStatus": states, "logger": logging.getLogger("stage-test"), "datetime": datetime, "timezone": timezone, "os": os, "magnus_workspace_path": str(self.root / "local")}
                exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
                with self.assertLogs("stage-test", level="WARNING"):
                    namespace["_sync_reality_slurm"](self.scheduler)
                self.assertEqual((jobs["bad"].status, jobs["bad"].slurm_job_id), (initial, "bad"))
                self.assertIsNone(jobs["good"].slurm_job_id)
                self.assertEqual(cleaned, ["good"])


if __name__ == "__main__":
    unittest.main()
