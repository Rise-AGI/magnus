"""Exercise deletion permissions and link boundaries without a running server."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "server" / "_workspace_cleanup.py"
SPEC = importlib.util.spec_from_file_location("isolated_workspace_cleanup", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WorkspaceCleanupTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor-based cleanup")
    def test_parallel_walk_is_bounded_even_below_single_child_spines(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            for index in range(24):
                leaf = root / "scratch" / "nested" / str(index)
                leaf.mkdir(parents=True)
                (leaf / "payload").write_text("scratch")
            unlink = os.unlink
            guard = threading.Lock()
            overlap = threading.Event()
            active = peak = 0

            def slow_unlink(name, *args, **kwargs):
                nonlocal active, peak
                if name != "payload":
                    return unlink(name, *args, **kwargs)
                with guard:
                    active += 1
                    peak = max(peak, active)
                    if active >= 2:
                        overlap.set()
                try:
                    if not overlap.wait(2):
                        raise TimeoutError("Expected overlapping subtree workers")
                    return unlink(name, *args, **kwargs)
                finally:
                    with guard:
                        active -= 1

            with patch.object(MODULE.os, "unlink", slow_unlink):
                result = MODULE.cleanup_job_path(root, workers=4)
            self.assertEqual(result.status, "done", result)
            self.assertEqual(result.removed, 51)
            self.assertGreaterEqual(peak, 2)
            self.assertLessEqual(peak, 4)
            self.assertFalse(root.exists())

    @unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor-based cleanup")
    def test_structured_timeout_preserves_progress_for_the_next_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            root.mkdir()
            for index in range(10):
                (root / str(index)).write_text("scratch")
            clock = [0.0]
            unlink = os.unlink

            def slow_unlink(*args, **kwargs):
                unlink(*args, **kwargs)
                clock[0] += 1

            with patch.object(MODULE, "monotonic", lambda: clock[0]), patch.object(MODULE.os, "unlink", slow_unlink):
                result = MODULE.cleanup_job_path(root, timeout_seconds=3, workers=1)
            self.assertEqual(result.status, "progress")
            self.assertEqual(result.removed, 3)
            self.assertEqual(len(list(root.iterdir())), 7)
            result = MODULE.cleanup_job_path(root)
            self.assertEqual(result.status, "done")
            self.assertEqual(result.removed, 8)

    @unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor-based cleanup")
    def test_locked_root_stays_owned_until_active_workers_drain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            root.mkdir()
            for name in ("blocked", "other"):
                (root / name).write_text("scratch")
            entered, release = threading.Event(), threading.Event()
            unlink = os.unlink
            output = []

            def blocked_unlink(name, *args, **kwargs):
                if name == "blocked":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("Test worker was not released")
                return unlink(name, *args, **kwargs)

            with patch.object(MODULE.os, "unlink", blocked_unlink):
                thread = threading.Thread(target=lambda: output.append(MODULE.cleanup_job_path(root)))
                thread.start()
                try:
                    self.assertTrue(entered.wait(2))
                    retry = MODULE.cleanup_job_path(root)
                    self.assertEqual(retry.status, "busy")
                    self.assertEqual(retry.removed, 0)
                    self.assertTrue((root / "blocked").exists())
                finally:
                    release.set()
                    thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(output[0].status, "done", output)

    def test_keep_contract_preserves_protocol_files_and_recovery_backups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            root.mkdir()
            for name in (".magnus_result", ".magnus-output-reset-old", "scratch"):
                (root / name).write_text(name)
            result = MODULE.cleanup_job_path(
                root,
                preserve=(".magnus_result",),
                preserve_prefixes=(".magnus-output-reset-",),
            )
            self.assertEqual(result.status, "done", result)
            self.assertEqual(sorted(path.name for path in root.iterdir()), [".magnus-output-reset-old", ".magnus_result"])
            self.assertEqual((root / ".magnus_result").read_text(), ".magnus_result")

    def test_structured_protocol_rejects_malformed_or_false_success(self):
        result = MODULE.CleanupResult("done", 3, 0.2, "")
        self.assertEqual(MODULE.parse_cleanup_result(json.dumps(result._asdict())), result)
        for value in (
            {},
            {**result._asdict(), "status": "unknown"},
            {**result._asdict(), "removed": True},
            {**result._asdict(), "elapsed_seconds": float("nan")},
            {**result._asdict(), "error": "OSError"},
            {**result._asdict(), "status": "failed"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.parse_cleanup_result(json.dumps(value))
        with self.assertRaises(ValueError):
            MODULE.parse_cleanup_result(" " * 4097)

    def test_unsupported_platform_uses_serial_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            root.mkdir()
            (root / "payload").write_text("scratch")
            is_directory = os.path.isdir
            with patch.object(MODULE.os.path, "isdir", lambda path: False if path == "/proc/self/fd" else is_directory(path)):
                result = MODULE.cleanup_job_path(root)
            self.assertEqual(result.status, "done", result)
            self.assertFalse(root.exists())

    def test_readonly_and_unsearchable_directories_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            nested = root / "readonly" / "unsearchable"
            nested.mkdir(parents=True)
            (nested / "file").write_text("scratch")
            nested.chmod(0)
            nested.parent.chmod(0o555)
            try:
                MODULE.remove_job_path(root)
                self.assertFalse(root.exists())
            finally:
                if nested.parent.exists():
                    nested.parent.chmod(0o700)
                    if nested.exists():
                        nested.chmod(0o700)

    @unittest.skipUnless(os.name == "posix", "Symlink creation is platform-specific")
    def test_symlinks_and_hardlinked_files_do_not_modify_external_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            external = parent / "external"
            external.mkdir()
            protected = external / "file"
            protected.write_text("keep")
            protected.chmod(0o444)
            root = parent / "job"
            root.mkdir()
            (root / "directory-link").symlink_to(external, target_is_directory=True)
            (root / "broken-link").symlink_to(parent / "missing")
            os.link(protected, root / "hardlink")
            MODULE.remove_job_path(root)
            self.assertEqual(protected.read_text(), "keep")
            self.assertEqual(stat.S_IMODE(protected.stat().st_mode), 0o444)
            root.symlink_to(external, target_is_directory=True)
            MODULE.remove_job_path(root)
            self.assertTrue(protected.exists())
            MODULE.remove_job_path(root)

    def test_helper_is_compatible_with_remote_python(self):
        ast.parse(SOURCE.read_text(), feature_version=(3, 6))

    @unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor-based cleanup")
    def test_timeout_keeps_deletion_progress_and_retry_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            root.mkdir()
            for index in range(20):
                (root / str(index)).write_bytes(b"scratch")
            clock = [0.0]
            unlink = os.unlink

            def slow_unlink(*args, **kwargs):
                unlink(*args, **kwargs)
                clock[0] += 1

            with patch.object(MODULE, "monotonic", lambda: clock[0]), patch.object(
                MODULE.os,
                "unlink",
                slow_unlink,
            ):
                with self.assertRaises(TimeoutError):
                    MODULE.remove_job_path(
                        root,
                        timeout_seconds=3,
                    )
            self.assertEqual(len(list(root.iterdir())), 17)
            MODULE.remove_job_path(
                root,
                timeout_seconds=3,
            )
            self.assertFalse(root.exists())

    @unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor-based cleanup")
    def test_overlapping_cleanup_does_not_walk_or_delete(self):
        import fcntl

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "job"
            root.mkdir()
            payload = root / "file"
            payload.write_text("keep until prior cleanup finishes")
            descriptor = os.open(
                root,
                os.O_RDONLY | os.O_DIRECTORY,
            )
            try:
                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
                with self.assertRaises(BlockingIOError):
                    MODULE.remove_job_path(
                        root,
                    )
                self.assertTrue(payload.is_file())
            finally:
                os.close(
                    descriptor,
                )
            MODULE.remove_job_path(
                root,
            )
            self.assertFalse(root.exists())

    @unittest.skipUnless(hasattr(os, "O_PATH"), "Linux descriptor-based cleanup")
    def test_directory_replaced_by_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            external = parent / "external"
            external.mkdir()
            protected = external / "file"
            protected.write_text("keep")
            external.chmod(0o555)
            root = parent / "job"
            root.mkdir()
            nested = root / "nested"
            nested.mkdir()
            open_directory = os.open
            replaced = False

            def swap_before_open(name, flags, *args, **kwargs):
                nonlocal replaced
                if name == "nested" and not replaced:
                    nested.rmdir()
                    nested.symlink_to(external, target_is_directory=True)
                    replaced = True
                return open_directory(name, flags, *args, **kwargs)

            with patch.object(MODULE.os, "open", swap_before_open):
                with self.assertRaises(OSError):
                    MODULE.remove_job_path(
                        root,
                    )
            self.assertTrue(replaced)
            self.assertEqual(protected.read_text(), "keep")
            self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o555)
            external.chmod(0o755)
            MODULE.remove_job_path(
                root,
            )
            self.assertTrue(protected.exists())


if __name__ == "__main__":
    unittest.main()
