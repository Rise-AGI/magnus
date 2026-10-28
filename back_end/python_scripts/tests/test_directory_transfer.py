"""Exercise the real archive protocol with a local stand-in for SSH."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest

from test_transport_timeouts import SERVER, load_transport


RECEIVER = SERVER / "_slurm_manager" / "_directory_receiver.py"


class DirectoryTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "repository"
        self.source.mkdir()
        self.target = self.root / "remote" / "repository"
        self.namespace = load_transport()

    def pack(self, cancel_event=None, deadline=None):
        archive = self.root / "archive.tar.gz"
        metadata = self.namespace["pack_directory"](
            str(self.source),
            str(archive),
            deadline=deadline if deadline is not None else time.monotonic() + 20,
            cancel_event=cancel_event,
        )
        return archive, metadata

    def receive(self, archive, metadata, payload=None):
        return subprocess.run(
            [sys.executable, str(RECEIVER), str(self.target), "10"],
            input=json.dumps(metadata).encode() + b"\n" + (archive.read_bytes() if payload is None else payload),
            capture_output=True,
            timeout=15,
        )

    def assert_no_partial(self):
        self.assertFalse(self.target.exists())
        if self.target.parent.exists():
            self.assertEqual(list(self.target.parent.glob(".magnus-directory-*")), [])

    def test_roundtrip_preserves_complete_tree_and_modes(self):
        (self.source / ".git").mkdir()
        (self.source / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (self.source / "empty").mkdir()
        script = self.source / "script with spaces'$.sh"
        script.write_text("#!/bin/sh\nprintf hello\n")
        script.chmod(0o751)
        for index in range(800):
            (self.source / f"file-{index:04}.txt").write_bytes(bytes(range(256)))
        archive, metadata = self.pack()
        result = self.receive(archive, metadata)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / "empty").is_dir())
        files = [path for path in self.source.rglob("*") if path.is_file()]
        for path in files:
            copied = self.target / path.relative_to(self.source)
            self.assertEqual(copied.read_bytes(), path.read_bytes())
        if os.name != "nt":
            self.assertEqual((self.target / script.name).stat().st_mode & 0o777, 0o751)

    @unittest.skipIf(os.name == "nt", "symlinks require Windows developer-mode privileges")
    def test_symlinks_are_preserved_without_copying_external_contents(self):
        outside = self.root / "private"
        outside.mkdir()
        secret = b"control-host-only-canary"
        (outside / "sensitive").write_bytes(secret)
        (self.source / "relative").symlink_to("../private", target_is_directory=True)
        (self.source / "absolute").symlink_to(outside, target_is_directory=True)
        (self.source / "dangling").symlink_to("missing")
        archive, metadata = self.pack()
        with tarfile.open(archive) as packed:
            self.assertTrue(all(not member.isfile() for member in packed))
        result = self.receive(archive, metadata)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(self.target / "relative"), "../private")
        self.assertEqual(os.readlink(self.target / "absolute"), str(outside))
        self.assertEqual(os.readlink(self.target / "dangling"), "missing")
        self.assertEqual((outside / "sensitive").read_bytes(), secret)
        self.assertEqual(metadata["unpacked_bytes"], 0)

    @unittest.skipIf(os.name == "nt", "hard-link fixture is POSIX")
    def test_hard_links_preserve_bytes(self):
        (self.source / "one").write_bytes(b"linked bytes")
        os.link(self.source / "one", self.source / "two")
        archive, metadata = self.pack()
        self.assertEqual(self.receive(archive, metadata).returncode, 0)
        self.assertEqual((self.target / "one").read_bytes(), (self.target / "two").read_bytes())

    def test_corruption_truncation_and_extra_bytes_are_not_published(self):
        (self.source / "data").write_bytes(b"payload")
        archive, metadata = self.pack()
        for payload in (archive.read_bytes()[:-1], archive.read_bytes() + b"x", b"x" * metadata["size"]):
            with self.subTest(length=len(payload)):
                self.assertNotEqual(self.receive(archive, metadata, payload).returncode, 0)
                self.assert_no_partial()

    def test_existing_destination_is_never_overwritten_or_nested(self):
        self.target.mkdir(parents=True)
        (self.target / "keep").write_bytes(b"previous complete tree")
        archive, metadata = self.pack()
        self.assertNotEqual(self.receive(archive, metadata).returncode, 0)
        self.assertEqual(list(self.target.iterdir()), [self.target / "keep"])
        self.assertEqual((self.target / "keep").read_bytes(), b"previous complete tree")

    def test_paths_and_symlink_descendants_cannot_escape(self):
        for name, parent_link in (("../escaped", False), ("/escaped", False), ("link/escaped", True)):
            with self.subTest(name=name):
                archive = self.root / "malicious.tar.gz"
                with tarfile.open(archive, "w:gz") as packed:
                    if parent_link:
                        link = tarfile.TarInfo("link")
                        link.type = tarfile.SYMTYPE
                        link.linkname = str(self.root)
                        packed.addfile(link)
                    item = tarfile.TarInfo(name)
                    item.size = 6
                    packed.addfile(item, io.BytesIO(b"escape"))
                metadata = {
                    "size": archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "unpacked_bytes": 6,
                }
                self.assertNotEqual(self.receive(archive, metadata).returncode, 0)
                self.assertFalse((self.root / "escaped").exists())
                self.assert_no_partial()

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0, "requires POSIX unprivileged permissions")
    def test_unreadable_subdirectory_fails_instead_of_publishing_partial_tree(self):
        hidden = self.source / "hidden"
        hidden.mkdir()
        (hidden / "required").write_bytes(b"required input")
        hidden.chmod(0)
        try:
            with self.assertRaises(PermissionError):
                self.pack()
        finally:
            hidden.chmod(0o700)
        self.assert_no_partial()

    def test_pack_obeys_deadline_and_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            self.pack(cancel_event=cancelled)
        with self.assertRaises(TimeoutError):
            self.pack(deadline=time.monotonic() - 1)

    def test_directory_push_uses_one_stream_and_never_recursive_scp(self):
        (self.source / "file").write_bytes(b"contents")
        actor = self.namespace["_SshControlMasterTransport"]("socket", "host", "user")
        calls = []

        def stream(command, local_path, **kwargs):
            calls.append(command)
            self.assertEqual(command[:2], ["python3", "-c"])
            self.assertLessEqual(kwargs["timeout"], 10)
            return self.namespace["_stream_file_to_command"](
                [sys.executable, *command[1:]],
                local_path,
                **kwargs,
            )

        actor.run_with_file_input = stream
        actor._scp = lambda *args, **kwargs: self.fail("recursive SCP used")
        actor.push(str(self.source), str(self.target), timeout=10)
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.target / "file").read_bytes(), b"contents")


if __name__ == "__main__":
    unittest.main()
