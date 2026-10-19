"""A broken offline drop must not block other receipts or revive consumed tokens."""
import ast
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_staging():
    path = SERVER / "_scheduler" / "_staging.py"
    namespace = {"__name__": __name__, "__file__": str(path), "logger": logging.getLogger("custody-stage-test")}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class FileTransport:
    is_remote = True

    def __init__(self):
        self.fetches = []
        self.failed_path = None
        self.fail_cleanup = False

    def run(self, command, **kwargs):
        if command[:2] == ["rm", "-rf"]:
            return SimpleNamespace(returncode=int(self.fail_cleanup), stderr="cleanup failed")
        if command[:2] == ["mkdir", "-p"]:
            Path(command[-1]).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0)
        path = Path(command[-1])
        entries = sorted(os.listdir(path)) if path.exists() else None
        return SimpleNamespace(returncode=0, stdout=json.dumps(entries))

    def fetch(self, remote, local):
        self.fetches.append(Path(remote))
        if Path(remote) == self.failed_path:
            Path(local).write_bytes(b"partial transfer")
            raise OSError("transport interrupted")
        shutil.copyfile(remote, local)

    def push(self, local, remote):
        shutil.copyfile(local, remote)


class CustodyStageOutTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_staging()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote"
        self.local = self.root / "local"
        self.transport = FileTransport()
        self.stored = {}
        self.calls = []
        self.failed_token = None
        self.namespace["file_custody_manager"] = SimpleNamespace(store_file=self.store)
        self.scheduler = self.make_scheduler()

    def make_scheduler(self):
        scheduler = self.namespace["_StagingMixin"]()
        scheduler.slurm_manager = SimpleNamespace(transport=self.transport)
        scheduler._remote_job_working_table = lambda job: str(self.remote)
        scheduler._local_job_working_table = lambda job: str(self.local)
        return scheduler

    def store(self, **kwargs):
        token = kwargs["explicit_token"]
        self.calls.append(token)
        if token == self.failed_token:
            raise RuntimeError("custody temporarily full")
        self.stored[token] = (kwargs["filename"], kwargs["file_obj"].read())
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
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [first, second, first])

    def test_token_metadata_change_is_not_silently_accepted(self):
        token, path, meta = self.entry("a")
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        meta["max_downloads"] = 200
        (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])

    def test_hidden_partial_is_preserved_without_fetch_or_poisoning_other_entries(self):
        token, _, _ = self.entry("a")
        partial = self.remote / ".magnus_custody_drop" / ".partial-interrupted"
        partial.mkdir()
        (partial / "payload").write_bytes(b"partial")
        with self.assertLogs("custody-stage-test", level="WARNING"):
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
        self.assertFalse(self.calls)
        self.assertEqual((path / "payload").read_bytes(), b"artifact")
        self.assertEqual(list((self.local / ".magnus_custody_drop").iterdir()), [])
        self.transport.failed_path = None
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.assertEqual(self.calls, [token])

    def test_completed_logs_are_not_downloaded_again_while_custody_is_pending(self):
        self.entry("a", metadata=False)
        log = self.remote / "slurm" / "output.txt"
        log.parent.mkdir()
        log.write_bytes(b"complete log")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("job"))
            self.assertFalse(self.make_scheduler()._stage_out_final("job"))
        self.assertEqual(self.transport.fetches.count(log), 1)
        self.assertEqual((self.local / "slurm" / "output.txt").read_bytes(), b"complete log")

    def test_failed_log_transfer_still_registers_custody_then_retries_log(self):
        token, _, _ = self.entry("a")
        log = self.remote / "slurm" / "output.txt"
        log.parent.mkdir()
        log.write_bytes(b"complete log")
        self.transport.failed_path = log
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("job"))
        self.assertEqual(self.calls, [token])
        self.transport.failed_path = None
        self.assertTrue(self.scheduler._stage_out_final("job"))
        self.assertEqual(self.calls, [token])

    def test_marker_fetch_is_independent_of_broken_custody(self):
        self.entry("a", metadata=False)
        (self.remote / ".magnus_success").write_text("success", encoding="utf-8")
        self.scheduler._stage_out_markers("job")
        self.assertEqual((self.local / ".magnus_success").read_text(), "success")

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
        self.local.mkdir()
        (self.local / ".magnus_custody_receipts.json").write_text("{corrupt", encoding="utf-8")
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("job"))
        self.assertFalse(self.calls)
        self.assertTrue(self.remote.exists())

    def test_new_attempt_fetches_new_logs_but_keeps_receipt_history(self):
        token, _, _ = self.entry("a")
        self.assertTrue(self.scheduler._stage_out_final("job"))
        wrapper = self.root / "wrapper.py"
        wrapper.write_text("pass", encoding="utf-8")
        self.scheduler._stage_in_job("job", str(wrapper))
        ledger = self.namespace["_read_custody_receipts"](str(self.local))
        self.assertFalse(ledger["logs_complete"])
        self.assertIn(token, ledger["entries"])
        log = self.remote / "slurm" / "output.txt"
        log.write_bytes(b"new attempt log")
        self.assertTrue(self.scheduler._stage_out_final("job"))
        self.assertEqual((self.local / "slurm" / "output.txt").read_bytes(), b"new attempt log")
        self.assertEqual(self.calls, [token])

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

    def test_empty_drop_and_local_execution_complete_without_side_effects(self):
        self.assertTrue(self.scheduler._stage_out_custody("job"))
        self.transport.is_remote = False
        self.assertTrue(self.scheduler._stage_out_final("job"))
        self.assertFalse(self.local.exists())

    def test_remote_cleanup_failure_is_not_reported_as_complete(self):
        self.transport.fail_cleanup = True
        with self.assertRaises(RuntimeError):
            self.scheduler._cleanup_remote_job("job")


if __name__ == "__main__":
    unittest.main()
