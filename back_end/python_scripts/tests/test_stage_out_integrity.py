"""Remote artifact failures must remain retryable and never trigger cleanup."""
from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
import runpy
from test_transport_timeouts import load_transport


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_staging():
    path = SERVER / "_scheduler" / "_staging.py"
    namespace = {"remaining_timeout": load_transport()["remaining_timeout"], "magnus_config": {"transport": {"ssh": {}}}, "__name__": __name__, "__file__": str(path), "logger": logging.getLogger("stage-test")}
    namespace.update({name: value for name, value in runpy.run_path(str(SERVER / "_custody_receipts.py")).items() if not name.startswith("__")})
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)
    namespace["_StagingMixin"].config = namespace["magnus_config"]
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

    def fetch(self, remote, local, *, timeout=None):
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

    def test_optional_absence_succeeds_but_transport_failure_stays_pending(self):
        self.assertTrue(self.scheduler._stage_out_final("empty"))
        self.transport.query_failure = True
        with self.assertLogs("stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("empty"))

    def test_partial_fetch_keeps_previous_log_and_remote_original(self):
        remote = Path(self.scheduler._remote_job_working_table("job")) / "slurm" / "output.txt"
        local = Path(self.scheduler._local_job_working_table("job")) / "slurm" / "output.txt"
        remote.parent.mkdir(parents=True)
        local.parent.mkdir(parents=True)
        remote.write_text("complete log")
        local.write_text("previous log")
        self.transport.failed_leaf = "output.txt"
        with self.assertLogs("stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("job"))
        self.assertEqual(local.read_text(), "previous log")
        self.assertEqual(remote.read_text(), "complete log")
        self.assertEqual(list(local.parent.iterdir()), [local])
        self.transport.failed_leaf = None
        self.assertTrue(self.scheduler._stage_out_final("job"))
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
        with self.assertLogs("stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("job"))
        received = []
        self.namespace["file_custody_manager"].store_file = lambda **kwargs: received.append(kwargs["file_obj"].read())
        self.assertTrue(self.scheduler._stage_out_final("job"))
        self.assertEqual(received, [b"artifact"])
        self.assertTrue(entry.exists())



if __name__ == "__main__":
    unittest.main()
