"""Untrusted Job protocol paths cannot redirect host reads or writes."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


SERVER = Path(__file__).resolve().parents[2] / "server"
spec = importlib.util.spec_from_file_location("isolated_protocol_files", SERVER / "_protocol_files.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)
open_protocol_file = protocol.open_protocol_file


class ProtocolFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.jobs = self.root / "jobs"
        (self.jobs / "job" / "slurm").mkdir(parents=True)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "output.txt"
        self.secret.write_bytes(b"host canary")
        self.relative = "job/slurm/output.txt"
        self.leaf = self.jobs / self.relative

    def test_regular_write_append_and_read(self):
        for mode, data in (("wb", b"first"), ("ab", b" second")):
            with open_protocol_file(self.jobs, self.relative, mode) as stream:
                stream.write(data)
        with open_protocol_file(self.jobs, self.relative) as stream:
            self.assertEqual(stream.read(), b"first second")

    def test_leaf_symlink_cannot_read_or_truncate_host_file(self):
        self.leaf.symlink_to(self.secret)
        for mode in ("rb", "wb", "ab"):
            with self.subTest(mode=mode), self.assertRaises(OSError):
                with open_protocol_file(self.jobs, self.relative, mode):
                    self.fail("opened symlink")
        self.assertEqual(self.secret.read_bytes(), b"host canary")

    def test_directory_link_cannot_redirect_reads_or_parent_creation(self):
        self.leaf.parent.rmdir()
        self.leaf.parent.symlink_to(self.outside, target_is_directory=True)
        for mode in ("rb", "wb", "ab"):
            with self.subTest(mode=mode), self.assertRaises(OSError):
                with open_protocol_file(self.jobs, self.relative, mode, create_parents=True):
                    self.fail("opened through directory link")
        self.assertEqual(self.secret.read_bytes(), b"host canary")

    def test_hardlink_cannot_read_or_truncate_shared_inode(self):
        os.link(self.secret, self.leaf)
        for mode in ("rb", "wb", "ab"):
            with self.subTest(mode=mode), self.assertRaises(OSError):
                with open_protocol_file(self.jobs, self.relative, mode):
                    self.fail("opened shared inode")
        self.assertEqual(self.secret.read_bytes(), b"host canary")

    @unittest.skipIf(os.name == "nt", "POSIX named pipe")
    def test_fifo_does_not_block_a_request_worker(self):
        os.mkfifo(self.leaf)
        began = time.monotonic()
        for mode in ("rb", "wb", "ab"):
            with self.subTest(mode=mode), self.assertRaises(OSError):
                with open_protocol_file(self.jobs, self.relative, mode):
                    self.fail("opened FIFO")
        self.assertLess(time.monotonic() - began, 1)

    @unittest.skipIf(os.name == "nt", "POSIX directory descriptor replacement race")
    def test_replaced_parent_cannot_redirect_an_open_in_progress(self):
        original = os.open
        moved = self.jobs / "job" / "retained"

        def swap_then_open(path, flags, *args, **kwargs):
            if path == "output.txt":
                self.leaf.parent.rename(moved)
                self.leaf.parent.symlink_to(self.outside, target_is_directory=True)
            return original(path, flags, *args, **kwargs)

        with patch.object(protocol.os, "open", swap_then_open):
            with open_protocol_file(self.jobs, self.relative, "wb") as stream:
                stream.write(b"job log")
        self.assertEqual(self.secret.read_bytes(), b"host canary")
        self.assertEqual((moved / "output.txt").read_bytes(), b"job log")

    def test_parent_traversal_and_absolute_paths_are_rejected(self):
        for path in ("../outside/output.txt", self.secret):
            with self.subTest(path=path), self.assertRaises(ValueError):
                with open_protocol_file(self.jobs, path):
                    self.fail("escaped root")

    def test_download_publication_replaces_link_without_writing_its_target(self):
        self.leaf.symlink_to(self.secret)
        download = self.root / "download"
        download.write_bytes(b"complete download")
        protocol.replace_protocol_file(self.jobs, self.relative, str(download))
        self.assertFalse(self.leaf.is_symlink())
        self.assertEqual(self.leaf.read_bytes(), b"complete download")
        self.assertEqual(self.secret.read_bytes(), b"host canary")

    def test_download_publication_refuses_parent_link_and_preserves_download(self):
        self.leaf.parent.rmdir()
        self.leaf.parent.symlink_to(self.outside, target_is_directory=True)
        download = self.root / "download"
        download.write_bytes(b"complete download")
        with self.assertRaises(OSError):
            protocol.replace_protocol_file(self.jobs, self.relative, str(download))
        self.assertEqual(download.read_bytes(), b"complete download")
        self.assertEqual(self.secret.read_bytes(), b"host canary")


if __name__ == "__main__":
    unittest.main()
