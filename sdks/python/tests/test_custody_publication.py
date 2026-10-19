"""Offline uploads publish a complete entry or no receipt at all."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch


PACKAGE = "_magnus_custody_publication_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / "src" / "magnus")]
sys.modules[PACKAGE] = package
client_module = importlib.import_module(f"{PACKAGE}.client")
download_module = importlib.import_module(f"{PACKAGE}.http_download")


class CustodyPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.drop = self.root / "drop"
        self.source = self.root / "input.bin"
        self.source.write_bytes(b"complete data")
        self.client = client_module.MagnusClient.__new__(client_module.MagnusClient)

    def publish(self):
        secret = self.client._drop_file(self.source, str(self.drop), 60, 2)
        entry = self.drop / secret.removeprefix("magnus-secret:")
        return entry, json.loads((entry / "meta.json").read_text(encoding="utf-8"))

    def test_file_becomes_visible_only_after_complete_manifest_and_payload(self):
        native_copy = shutil.copyfile

        def observe_copy(source, target):
            visible = list(self.drop.iterdir())
            self.assertEqual(len(visible), 1)
            self.assertTrue(visible[0].name.startswith(".partial-"))
            return native_copy(source, target)

        with patch.object(client_module.shutil, "copyfile", side_effect=observe_copy):
            entry, metadata = self.publish()
        self.assertEqual(list(self.drop.iterdir()), [entry])
        self.assertEqual(metadata["filename"], "input.bin")
        self.assertEqual(metadata["size_bytes"], len(b"complete data"))
        self.assertEqual((entry / metadata["storage_filename"]).read_bytes(), b"complete data")

    def test_manifest_filename_is_a_valid_user_payload(self):
        self.source = self.root / "meta.json"
        self.source.write_text('{"user": true}', encoding="utf-8")
        entry, metadata = self.publish()
        self.assertEqual(metadata["filename"], "meta.json")
        self.assertEqual((entry / "payload").read_text(encoding="utf-8"), '{"user": true}')
        target = self.root / "received.json"
        with patch.dict(os.environ, {"MAGNUS_CUSTODY_DROPIN_DIR": str(self.drop)}):
            download_module._read_staged_dropin(entry.name, str(target), True)
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_read_only_file_can_be_published(self):
        self.source.chmod(0o444)
        try:
            entry, _ = self.publish()
            self.assertEqual((entry / "payload").read_bytes(), b"complete data")
        finally:
            self.source.chmod(0o644)

    def test_legacy_dropin_remains_readable(self):
        entry = self.drop / "legacy"
        entry.mkdir(parents=True)
        (entry / "meta.json").write_text(json.dumps({"filename": "answer.txt"}), encoding="utf-8")
        (entry / "answer.txt").write_bytes(b"legacy answer")
        target = self.root / "received.txt"
        with patch.dict(os.environ, {"MAGNUS_CUSTODY_DROPIN_DIR": str(self.drop)}):
            download_module._read_staged_dropin(entry.name, str(target), True)
        self.assertEqual(target.read_bytes(), b"legacy answer")

    def test_dropin_storage_name_cannot_escape_its_entry(self):
        entry, metadata = self.publish()
        metadata["storage_filename"] = "../input.bin"
        (entry / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
        target = self.root / "received.txt"
        with patch.dict(os.environ, {"MAGNUS_CUSTODY_DROPIN_DIR": str(self.drop)}):
            self.assertIsNone(download_module._read_staged_dropin(entry.name, str(target), True))
        self.assertFalse(target.exists())

    def test_directory_archive_retains_original_layout(self):
        self.source = self.root / "results"
        (self.source / "nested").mkdir(parents=True)
        (self.source / "nested" / "answer.txt").write_bytes(b"42")
        entry, metadata = self.publish()
        self.assertTrue(metadata["is_directory"])
        self.assertEqual(metadata["filename"], "results.tar.gz")
        with tarfile.open(entry / "payload", "r:gz") as archive:
            self.assertEqual(archive.extractfile("results/nested/answer.txt").read(), b"42")

    def test_copy_failure_removes_only_its_unpublished_entry(self):
        self.drop.mkdir()
        other = self.drop / "other-writer"
        other.mkdir()
        for failure in (OSError("disk full"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(client_module.shutil, "copyfile", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        self.publish()
                self.assertEqual(list(self.drop.iterdir()), [other])

    def test_manifest_or_rename_failure_does_not_return_a_receipt(self):
        for target in (client_module.json, client_module.os):
            method = "dump" if target is client_module.json else "rename"
            with self.subTest(method=method):
                with patch.object(target, method, side_effect=OSError("write failed")):
                    with self.assertRaises(OSError):
                        self.publish()
                self.assertEqual(list(self.drop.iterdir()), [])

    def test_killed_writer_leaves_only_a_hidden_unpublished_directory(self):
        script = """import importlib, pathlib, sys, types
package = types.ModuleType('custody_child')
package.__path__ = [sys.argv[1]]
sys.modules['custody_child'] = package
module = importlib.import_module('custody_child.client')
def interrupted_copy(source, target):
    pathlib.Path(target).write_bytes(b'partial')
    print('writing', flush=True)
    sys.stdin.readline()
module.shutil.copyfile = interrupted_copy
client = module.MagnusClient.__new__(module.MagnusClient)
client._drop_file(pathlib.Path(sys.argv[2]), sys.argv[3], 60, 1)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, package.__path__[0], str(self.source), str(self.drop)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "writing")
            process.kill()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        entries = list(self.drop.iterdir())
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].name.startswith(".partial-"))
        self.assertFalse((entries[0] / "meta.json").exists())


if __name__ == "__main__":
    unittest.main()
