"""A broken offline drop must not block other receipts or revive consumed tokens."""
import ast
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
import runpy
from test_transport_timeouts import load_transport
from test_protocol_files import protocol
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_staging():
    path = SERVER / "_scheduler" / "_staging.py"
    namespace = {"replace_protocol_file": protocol.replace_protocol_file, "check_transfer_cancelled": load_transport()["check_transfer_cancelled"], "remaining_timeout": load_transport()["remaining_timeout"], "magnus_config": {"transport": {"ssh": {}}}, "__name__": __name__, "__file__": str(path), "logger": logging.getLogger("custody-stage-test")}
    namespace.update({name: value for name, value in runpy.run_path(str(SERVER / "_custody_receipts.py")).items() if not name.startswith("__")})
    namespace["StagingProgress"] = runpy.run_path(str(SERVER / "_staging_diagnostics.py"))["StagingProgress"]
    relay_path = SERVER / "_scheduler" / "_custody_relay.py"
    relay = ast.parse(relay_path.read_text())
    relay.body = [node for node in relay.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(relay, str(relay_path), "exec"), namespace)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)
    namespace["_StagingMixin"].config = namespace["magnus_config"]
    return namespace


class FileTransport:
    is_remote = True

    def __init__(self):
        self.fetches = []
        self.failed_path = None
        self.fail_cleanup = False

    def run(self, command, **kwargs):
        if "input" in kwargs:
            return subprocess.run(command, input=kwargs["input"], capture_output=True, text=True, check=True, timeout=kwargs.get("timeout", 10))
        if command[:2] == ["python3", "-c"] and "remove_job_path(sys.argv[1]" in command[2]:
            if self.fail_cleanup:
                return SimpleNamespace(returncode=1, stderr="cleanup failed")
            return subprocess.run(command, capture_output=True, text=True, timeout=10)
        if command[:2] == ["mkdir", "-p"]:
            for directory in command[2:]:
                Path(directory).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0)
        path = Path(command[-1])
        entries = sorted(os.listdir(path)) if path.exists() else None
        return SimpleNamespace(returncode=0, stdout=json.dumps(entries))

    def fetch(self, remote, local, *, timeout=None, cancel_event=None):
        self.fetches.append(Path(remote))
        if Path(remote) == self.failed_path:
            Path(local).write_bytes(b"partial transfer")
            raise OSError("transport interrupted")
        shutil.copyfile(remote, local)

    def fetch_bounded(self, remote, local, *, size, timeout, cancel_event=None):
        self.fetch(remote, local, timeout=timeout)
        if Path(local).stat().st_size != size:
            raise ValueError("size changed")

    def push(self, local, remote, *, cancel_event=None):
        shutil.copyfile(local, remote)


class CustodyStageOutTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_staging()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote"
        self.namespace["magnus_workspace_path"] = str(self.root / "workspace")
        self.local = self.root / "workspace" / "jobs" / "job"
        self.local.parent.mkdir(parents=True)
        self.transport = FileTransport()
        self.stored = {}
        self.calls = []
        self.failed_token = None
        self.imports = {}
        self.namespace["file_custody_manager"] = SimpleNamespace(
            store_file=self.store,
            import_receipt=lambda job, token: self.imports.get(token),
            acknowledge_import=lambda job, token, digest: self.imports.pop(token, None),
            acknowledge_receipts=lambda job, receipts: None,
        )
        self.scheduler = self.make_scheduler()

    def make_scheduler(self):
        scheduler = self.namespace["_StagingMixin"]()
        scheduler._preparing_jobs_lock = threading.Lock()
        scheduler._submission_cancel_events = {}
        scheduler.slurm_manager = SimpleNamespace(transport=self.transport)
        scheduler._remote_job_working_table = lambda job: str(self.remote)
        scheduler._local_job_working_table = lambda job: str(self.local)
        scheduler._custody_relay = self.namespace["CustodyRelay"](
            self.transport, self.namespace["file_custody_manager"],
            scheduler._local_job_working_table, scheduler._remote_job_working_table,
            self.namespace["_validate_custody_metadata"], self.namespace["logger"], 900,
            transfer_logs=scheduler._stage_out_logs,
        )
        return scheduler

    def final_pass(self, scheduler=None):
        scheduler = scheduler or self.scheduler
        scheduler._stage_out_final("job")
        scheduler._custody_relay.transfer_bulk("job")
        return scheduler._stage_out_final("job")

    def store(self, **kwargs):
        token = kwargs["explicit_token"]
        self.calls.append(token)
        if token == self.failed_token:
            raise RuntimeError("custody temporarily full")
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            path = Path(directory) / "received"
            kwargs["receive"](path)
            self.stored[token] = (kwargs["filename"], path.read_bytes())
        self.imports[token] = kwargs["source_digest"]
        return token

    def entry(self, suffix, *, legacy=False, content=b"artifact", metadata=True):
        token = "relay-" + suffix * 32
        path = self.remote / ".magnus_custody_drop" / token
        path.mkdir(parents=True)
        name = "answer.bin" if legacy else "payload"
        (path / name).write_bytes(content)
        meta = {"token": token, "filename": "answer.bin", "is_directory": False, "expire_minutes": 60, "max_downloads": 1}
        if not legacy:
            meta.update(storage_filename="payload", size_bytes=len(content))
        if metadata:
            (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return token, path, meta

    def test_polling_budget_stops_before_another_payload(self):
        first, _, _ = self.entry("a")
        second, _, _ = self.entry("b")
        now = [100.0]
        self.namespace["monotonic"] = lambda: now[0]
        self.namespace["remaining_timeout"].__globals__["time"] = SimpleNamespace(monotonic=lambda: now[0])
        original = self.transport.fetch
        budgets = []
        def fetch(remote, local, *, timeout=None):
            budgets.append(timeout)
            original(remote, local, timeout=timeout)
            now[0] += 6
        self.transport.fetch = fetch
        self.assertFalse(self.scheduler._stage_out_custody("job", deadline=105))
        self.assertEqual(budgets, [5])
        self.assertIn(first, self.stored)
        self.assertNotIn(second, self.stored)
        self.assertTrue(self.remote.is_dir())

    def test_incomplete_legacy_entry_does_not_starve_complete_entries(self):
        broken, broken_path, _ = self.entry("a", legacy=True, content=b"", metadata=False)
        old, _, _ = self.entry("b", legacy=True)
        new, _, _ = self.entry("c")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.assertEqual(set(self.stored), {old, new})
        self.assertTrue(broken_path.exists())
        self.assertEqual((broken_path / "answer.bin").read_bytes(), b"")
        self.assertNotIn(broken, self.calls)

    def test_successful_tokens_are_not_refetched_or_revived_after_expiry_and_restart(self):
        token, _, _ = self.entry("a")
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.stored.clear()
        self.scheduler = self.make_scheduler()
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])
        self.assertFalse(self.stored)
        self.assertEqual(sum(path.name == "payload" for path in self.transport.fetches), 1)

    def test_temporarily_full_custody_retries_only_failed_registration(self):
        first, _, _ = self.entry("a")
        second, _, _ = self.entry("b")
        self.failed_token = first
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.failed_token = None
        self.scheduler = self.make_scheduler()
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [first, second, first])

    def test_published_token_is_never_reimported_from_mutated_remote_metadata(self):
        token, path, meta = self.entry("a")
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        meta["max_downloads"] = 200
        (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])

    def test_hidden_partial_is_preserved_without_fetch_or_poisoning_other_entries(self):
        token, _, _ = self.entry("a")
        partial = self.remote / ".magnus_custody_drop" / ".partial-interrupted"
        partial.mkdir()
        (partial / "payload").write_bytes(b"partial")
        self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])
        self.assertTrue(partial.exists())
        self.assertFalse(any(".partial-interrupted" in path.parts for path in self.transport.fetches))

    def test_truncated_payload_is_not_registered_and_complete_neighbor_is(self):
        broken, path, _ = self.entry("a")
        (path / "payload").write_bytes(b"short")
        good, _, _ = self.entry("b")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [good])
        self.assertTrue(path.exists())
        self.assertNotIn(broken, self.stored)

    def test_transfer_interruption_preserves_remote_and_retries_cleanly(self):
        token, path, _ = self.entry("a")
        self.transport.failed_path = path / "payload"
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])
        self.assertEqual((path / "payload").read_bytes(), b"artifact")
        self.assertFalse((self.local / ".magnus_custody_drop").exists())
        self.transport.failed_path = None
        self.scheduler = self.make_scheduler()
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token, token])

    def test_completed_logs_are_not_downloaded_again_while_custody_is_pending(self):
        self.entry("a", metadata=False)
        log = self.remote / "slurm" / "output.txt"
        log.parent.mkdir()
        log.write_bytes(b"complete log")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.final_pass())
            self.assertFalse(self.final_pass(self.make_scheduler()))
        self.assertEqual(self.transport.fetches.count(log), 1)
        self.assertEqual((self.local / "slurm" / "output.txt").read_bytes(), b"complete log")

    def test_failed_log_transfer_still_registers_custody_then_retries_log(self):
        token, _, _ = self.entry("a")
        log = self.remote / "slurm" / "output.txt"
        log.parent.mkdir()
        log.write_bytes(b"complete log")
        self.transport.failed_path = log
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.final_pass())
        self.assertEqual(self.calls, [token])
        self.transport.failed_path = None
        self.scheduler = self.make_scheduler()
        self.assertTrue(self.final_pass())
        self.assertEqual(self.calls, [token])

    def test_marker_fetch_is_independent_of_broken_custody(self):
        self.entry("a", metadata=False)
        (self.remote / ".magnus_success").write_text("success", encoding="utf-8")
        self.scheduler._stage_out_markers("job")
        self.assertEqual((self.local / ".magnus_success").read_text(), "success")

    def test_final_log_discovery_has_short_queries_but_full_payload_budget(self):
        log = self.remote / "slurm" / "output.txt"
        log.parent.mkdir(parents=True)
        log.write_bytes(b"log")
        queries, payloads = [], []
        run, fetch = self.transport.run, self.transport.fetch
        def query(command, **kwargs):
            queries.append(kwargs["timeout"])
            return run(command, **kwargs)
        def receive(remote, local, **kwargs):
            payloads.append(kwargs["timeout"])
            return fetch(remote, local, **kwargs)
        self.transport.run = query
        self.transport.fetch = receive
        self.scheduler._stage_out_logs("job", timeout=900)
        self.assertTrue(all(value <= 30 for value in queries))
        self.assertGreater(payloads[0], 890)

    def test_bad_metadata_never_escapes_the_entry(self):
        for invalid in ("../other", "..\\other", "meta.json"):
            with self.subTest(invalid=invalid):
                meta = {"token": "relay-" + "a" * 32, "filename": "good", "storage_filename": invalid}
                with self.assertRaises(ValueError):
                    self.namespace["_validate_custody_metadata"](meta, meta["token"])

    def test_receipt_write_failure_does_not_report_complete(self):
        token, path, _ = self.entry("a")
        with patch.dict(self.namespace, {"_write_custody_receipts": lambda *args: (_ for _ in ()).throw(OSError("disk full"))}):
            with self.assertLogs("custody-stage-test", level="WARNING"):
                self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])
        self.assertTrue(path.exists())
        self.assertFalse((self.local / ".magnus_custody_receipts.json").exists())

    def test_corrupt_receipt_ledger_preserves_workspace_and_does_not_republish(self):
        self.entry("a")
        self.local.mkdir(parents=True)
        (self.local / ".magnus_custody_receipts.json").write_text("{corrupt", encoding="utf-8")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.final_pass())
        self.assertFalse(self.calls)
        self.assertTrue(self.remote.exists())

    def test_new_attempt_fetches_new_logs_but_keeps_receipt_history(self):
        token, _, _ = self.entry("a")
        self.assertTrue(self.final_pass())
        wrapper = self.root / "wrapper.py"
        wrapper.write_text("pass", encoding="utf-8")
        self.scheduler._stage_in_job("job", str(wrapper))
        ledger = self.namespace["_read_custody_receipts"](str(self.local))
        self.assertFalse(ledger["logs_complete"])
        self.assertIn(token, ledger["entries"])
        log = self.remote / "slurm" / "output.txt"
        log.write_bytes(b"new attempt log")
        self.assertTrue(self.final_pass())
        self.assertEqual((self.local / "slurm" / "output.txt").read_bytes(), b"new attempt log")
        self.assertEqual(self.calls, [token])

    @unittest.skipUnless(shutil.which("scp"), "local scp executable required")
    def test_retried_sdk_upload_replaces_partial_directory_without_nesting(self):
        sdk_name = self.namespace["_PLATFORM_SDK_SUBDIR"]
        sdk = self.local / sdk_name
        (sdk / "bin").mkdir(parents=True)
        (sdk / "bin/magnus").write_text("#!/bin/sh\nexit 0\n")
        (sdk / "client.py").write_text("complete = True\n")
        remote_sdk = self.remote / sdk_name
        remote_sdk.mkdir(parents=True)
        (remote_sdk / "old-partial").write_bytes(b"partial")
        wrapper = self.root / "wrapper.py"
        wrapper.write_text("pass")
        original_run = self.transport.run
        def run(command, **kwargs):
            if command[0] in {"rm", "chmod"}:
                return subprocess.run(command, timeout=2, check=kwargs.get("check", False))
            return original_run(command, **kwargs)
        self.transport.run = run
        self.transport.push = lambda local, remote, **kwargs: subprocess.run(
            ["scp", "-r", local, remote], check=True, capture_output=True, timeout=2,
        )
        self.scheduler._stage_in_job("job", str(wrapper))
        self.assertEqual((remote_sdk / "client.py").read_bytes(), (sdk / "client.py").read_bytes())
        self.assertFalse((remote_sdk / sdk_name).exists())
        self.assertFalse((remote_sdk / "old-partial").exists())
        self.assertTrue(os.access(remote_sdk / "bin/magnus", os.X_OK))

    def test_stage_in_keeps_manifest_separate_from_payload_named_meta_json(self):
        source = self.root / "user.json"
        source.write_bytes(b'{"user": true}')
        released = []
        self.namespace["file_custody_manager"] = SimpleNamespace(
            acquire_file=lambda *args, **kwargs: (source, "meta.json", False, 12),
            release_file=released.append,
        )
        token = "relay-" + "a" * 32
        self.scheduler._stage_in_custody("job", "magnus receive magnus-secret:" + token)
        entry = self.remote / ".magnus_custody_dropin" / token
        metadata = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["filename"], "meta.json")
        self.assertEqual(metadata["storage_filename"], "payload")
        self.assertEqual((entry / "payload").read_bytes(), source.read_bytes())
        self.assertEqual(released, [token])

    def test_cancelled_input_releases_lease_before_return_and_skips_metadata(self):
        source = self.root / "input"
        source.write_bytes(b"input")
        cancelled = threading.Event()
        self.scheduler._submission_cancel_events["job"] = cancelled
        acquired, released, pushed = [], [], []
        def acquire(token, **kwargs):
            acquired.append(token)
            return source, "input", False, 5
        def push(local, remote, *, cancel_event=None):
            self.assertIs(cancel_event, cancelled)
            self.assertEqual(released, [])
            pushed.append(remote)
            cancelled.set()
            self.namespace["check_transfer_cancelled"](cancel_event)
        self.namespace["file_custody_manager"] = SimpleNamespace(
            acquire_file=acquire, release_file=released.append,
        )
        self.transport.push = push
        command = " ".join("magnus-secret:relay-" + letter * 32 for letter in "ab")
        with self.assertRaises(InterruptedError):
            self.scheduler._stage_in_custody("job", command)
        self.assertEqual(len(acquired), 1)
        self.assertEqual(released, acquired)
        self.assertEqual(len(pushed), 1)
        self.assertTrue(pushed[0].endswith("/payload"))

    def test_empty_drop_and_local_execution_complete_without_side_effects(self):
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.transport.is_remote = False
        self.assertTrue(self.final_pass())
        self.assertFalse(self.local.exists())

    def test_remote_cleanup_failure_is_not_reported_as_complete(self):
        self.transport.fail_cleanup = True
        with self.assertRaises(RuntimeError):
            self.scheduler._cleanup_remote_job("job")

    def test_remote_cleanup_reclaims_readonly_image_directories(self):
        image = self.remote / "readonly-image"
        image.mkdir(parents=True)
        (image / "payload").write_text("scratch")
        image.chmod(0o555)
        self.scheduler._cleanup_remote_job("job")
        self.assertFalse(self.remote.exists())


if __name__ == "__main__":
    unittest.main()
