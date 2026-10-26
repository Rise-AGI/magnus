"""Custody recovery on real disk, including independent process death."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_file_custody_lifecycle import load_manager


class CustodyRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.namespace = load_manager()
        self.config = {"server": {
            "root": str(self.root),
            "file_custody": {
                "max_size": "1M", "max_file_size": None, "max_processes": 10,
                "default_ttl_minutes": 60, "max_ttl_minutes": 60,
            },
        }}
        self.namespace["magnus_config"] = self.config
        self.manager = self.restart()

    def restart(self):
        return self.namespace["FileCustodyManager"]()

    def store(self, **kwargs):
        return self.manager.store_file(
            "results.tar.gz",
            io.BytesIO(b"complete scientific results"),
            **kwargs,
        )

    def test_shutdown_preserves_bytes_expiry_flags_and_remaining_downloads(self):
        token = self.store(max_downloads=3, is_directory=True)
        original = self.manager.get_entry(token)
        self.manager.get_file_path(token)
        self.manager.release_file(token)
        self.manager.shutdown()
        with self.assertRaisesRegex(RuntimeError, "shutting down"):
            self.store()
        restored = self.restart()
        entry = restored.get_entry(token)
        self.assertFalse(entry.permanent)
        self.assertEqual(entry.expires_at, original.expires_at)
        self.assertEqual(entry.max_downloads, 3)
        self.assertEqual(entry.download_count, 1)
        self.assertEqual(restored._current_size, original.file_size)
        acquired = restored.get_file_path(token)
        self.assertEqual(acquired[0].read_bytes(), b"complete scientific results")
        self.assertTrue(acquired[2])
        restored.release_file(token)
        again = self.restart()
        self.assertEqual(again.get_entry(token).download_count, 2)
        self.assertIsNotNone(again.get_file_path(token))
        again.release_file(token)
        self.assertEqual(self.restart().get_file_status(token), "unknown")

    def test_downtime_does_not_renew_ttl(self):
        with patch.object(self.namespace["time"], "time", return_value=1000):
            token = self.store(expire_minutes=5)
        with patch.object(self.namespace["time"], "time", return_value=1299):
            self.assertEqual(self.restart().get_entry(token).expires_at, 1300)
        with patch.object(self.namespace["time"], "time", return_value=1300):
            expired = self.restart()
        self.assertEqual(expired.get_file_status(token), "unknown")
        self.assertEqual(expired._current_size, 0)

    def test_process_death_retains_completed_upload_and_claimed_downloads(self):
        source = """
import io,json,os,sys
from test_file_custody_lifecycle import load_manager
arguments=json.load(sys.stdin)
namespace=load_manager()
namespace['magnus_config']=arguments['config']
manager=namespace['FileCustodyManager']()
if arguments['action']=='store':
    token=manager.store_file('payload',io.BytesIO(b'bytes'),max_downloads=2)
    result={'token':token,'expiry':manager.get_entry(token).expires_at}
else:
    token=arguments['token']
    acquired=manager.get_file_path(token)
    result={'bytes':acquired[0].read_text() if acquired else None}
    if acquired:
        result['expiry']=manager.get_entry(token).expires_at
        result['claims']=manager.get_entry(token).download_count
print(json.dumps(result),flush=True)
os._exit(0)
"""

        def process(action, token=None):
            completed = subprocess.run(
                [sys.executable, "-c", source],
                input = json.dumps({"config": self.config, "action": action, "token": token}),
                text = True,
                capture_output = True,
                cwd = Path(__file__).parent,
                check = True,
                timeout = 10,
            )
            return json.loads(completed.stdout)

        uploaded = process("store")
        for expected in (1, 2):
            taken = process("take", uploaded["token"])
            self.assertEqual(taken, {"bytes": "bytes", "expiry": uploaded["expiry"], "claims": expected})
        self.assertEqual(process("take", uploaded["token"]), {"bytes": None})

    def test_process_death_during_upload_does_not_publish_partial_bytes(self):
        accepted = self.store()
        source = """
import io,json,os,sys
from test_file_custody_lifecycle import load_manager
namespace=load_manager()
namespace['magnus_config']=json.load(sys.stdin)
manager=namespace['FileCustodyManager']()
class Interrupted(io.BytesIO):
    def read(self,size=-1):
        if self.tell():
            os._exit(0)
        return super().read(size)
manager.store_file('partial',Interrupted(b'incomplete'))
raise AssertionError('upload should have interrupted the process')
"""
        subprocess.run(
            [sys.executable, "-c", source],
            input = json.dumps(self.config),
            text = True,
            capture_output = True,
            cwd = Path(__file__).parent,
            check = True,
            timeout = 10,
        )
        self.assertEqual(len(list(self.manager._storage_root.glob("*/partial"))), 1)
        restored = self.restart()
        self.assertEqual(set(restored._entries), {accepted})
        self.assertEqual(list(restored._storage_root.glob("*/partial")), [])

    def test_concurrent_claims_cannot_gain_downloads_across_restart(self):
        token = self.store(max_downloads=9)

        def claim(manager):
            acquired = manager.get_file_path(token)
            if acquired is None:
                return False
            try:
                self.assertEqual(acquired[0].read_bytes(), b"complete scientific results")
                return True
            finally:
                manager.release_file(token)

        with ThreadPoolExecutor(max_workers=4) as workers:
            self.assertEqual(sum(workers.map(lambda _: claim(self.manager), range(3))), 3)
        restored = self.restart()
        with ThreadPoolExecutor(max_workers=8) as workers:
            self.assertEqual(sum(workers.map(lambda _: claim(restored), range(20))), 6)
        self.assertEqual(self.restart().get_file_status(token), "unknown")

    def test_failed_claim_commit_does_not_return_a_lease(self):
        token = self.store(max_downloads=1)
        with patch.object(self.manager._index, "save", side_effect=OSError("metadata full")):
            with self.assertRaises(OSError):
                self.manager.get_file_path(token)
        entry = self.manager.get_entry(token)
        self.assertEqual((entry.download_count, entry.active_readers), (0, 0))
        restored = self.restart()
        self.assertIsNotNone(restored.get_file_path(token))
        restored.release_file(token)

    def test_failed_upload_commit_preserves_previously_accepted_files(self):
        original = self.store()
        with patch.object(self.manager._index, "save", side_effect=OSError("metadata full")):
            with self.assertRaises(OSError):
                self.store()
        restored = self.restart()
        self.assertEqual(set(restored._entries), {original})
        self.assertEqual(restored._current_size, len(b"complete scientific results"))

    def test_deleted_active_reader_is_not_revived_after_restart(self):
        token = self.store()
        acquired = self.manager.get_file_path(token)
        self.manager.delete_entry(token)
        self.assertTrue(acquired[0].is_file())
        self.assertEqual(self.restart().get_file_status(token), "unknown")
        self.assertFalse(acquired[0].exists())

    def test_stage_in_and_status_checks_do_not_spend_or_renew(self):
        token = self.store(max_downloads=1)
        expiry = self.manager.get_entry(token).expires_at
        for _ in range(3):
            self.assertEqual(self.manager.get_file_status(token), "ready")
            self.assertIsNotNone(self.manager.acquire_file(token, count_download=False))
            self.manager.release_file(token)
        entry = self.restart().get_entry(token)
        self.assertEqual((entry.expires_at, entry.download_count), (expiry, 0))

    def test_corrupt_or_unsupported_index_preserves_all_payloads(self):
        token = self.store()
        path = self.manager.get_entry(token).file_dir / "results.tar.gz"
        index = self.manager._index.path
        index.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(OSError, "storage left unchanged"):
            self.restart()
        self.assertEqual(path.read_bytes(), b"complete scientific results")
        self.assertEqual(index.read_bytes(), b"not a sqlite database")

    def test_corrupt_legacy_index_cannot_erase_temporary_payloads(self):
        token = self.store()
        self.manager._manifest_path.write_text("broken")
        with self.assertRaisesRegex(ValueError, "storage left unchanged"):
            self.restart()
        self.assertTrue((self.manager.get_entry(token).file_dir / "results.tar.gz").is_file())

    def test_legacy_permanent_manifest_is_still_readable_and_separate(self):
        avatar = self.store(permanent=True)
        legacy = json.loads(self.manager._manifest_path.read_text())
        self.assertEqual(legacy, {avatar: {"filename": "results.tar.gz", "is_directory": False}})
        token = self.store(max_downloads=2)
        restored = self.restart()
        self.assertTrue(restored.get_entry(avatar).permanent)
        self.assertFalse(restored.get_entry(token).permanent)
        self.assertEqual(json.loads(restored._manifest_path.read_text()), legacy)
        if os.name == "posix":
            self.assertEqual(restored._index.path.stat().st_mode & 0o777, 0o600)

    def test_failed_orphan_cleanup_cannot_silently_release_disk_budget(self):
        token = self.store(expire_minutes=-1)
        path = self.manager._entries[token].file_dir
        with patch.object(self.namespace["shutil"], "rmtree", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                self.restart()
        self.assertTrue(path.is_dir())
        self.assertEqual(self.restart()._current_size, 0)

    def test_restored_payloads_still_enforce_capacity(self):
        token = self.store()
        self.config["server"]["file_custody"]["max_size"] = str(len(b"complete scientific results"))
        restored = self.restart()
        self.assertEqual(restored.get_file_status(token), "ready")
        with self.assertRaises(self.namespace["CustodyStorageFullError"]):
            restored.store_file("extra", io.BytesIO(b"x"))


if __name__ == "__main__":
    unittest.main()
