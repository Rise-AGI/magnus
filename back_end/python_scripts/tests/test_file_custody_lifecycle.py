"""Isolated custody lifecycle regressions; never import the server singleton."""
from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import errno
import io
import json
import logging
import os
from pathlib import Path
import re
import runpy
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


SERVER = Path(__file__).resolve().parents[2] / "server"
CUSTODY = SERVER / "_file_custody_manager"


def load_manager():
    namespace = runpy.run_path(str(CUSTODY / "_types.py"))
    namespace.update(runpy.run_path(str(CUSTODY / "_word_list.py")))
    namespace.update(runpy.run_path(str(SERVER / "_size_utils.py")))
    namespace.update({
        "logger": logging.getLogger("custody_lifecycle_test"),
        "is_disk_full_oserror": lambda error: error.errno in (errno.ENOSPC, errno.EDQUOT),
    })
    tree = ast.parse((CUSTODY / "_manager.py").read_text(encoding="utf-8"))
    tree.body = [
        node for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and (node.level or node.module == "library"))
    ]
    exec(compile(tree, str(CUSTODY / "_manager.py"), "exec"), namespace)
    return namespace


class PausedReader(io.BytesIO):
    def __init__(self, data: bytes, pause_at: int = 0):
        super().__init__(data)
        self.pause_at = pause_at
        self.calls = 0
        self.paused = threading.Event()
        self.resume = threading.Event()

    def read(self, size: int = -1):
        if self.calls == self.pause_at:
            self.paused.set()
            if not self.resume.wait(timeout=5):
                raise TimeoutError("Test did not release its paused reader")
        self.calls += 1
        return super().read(size)


class CustodyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.namespace = load_manager()
        self.namespace["magnus_config"] = {"server": {
            "root": self.temp.name,
            "file_custody": {
                "max_size": "1M",
                "max_file_size": None,
                "max_processes": 10,
                "default_ttl_minutes": 60,
                "max_ttl_minutes": 60,
            },
        }}
        self.manager = self.namespace["FileCustodyManager"]()

    def store(self, data=b"contents", **kwargs):
        return self.manager.store_file("sample.bin", io.BytesIO(data), **kwargs)

    def assert_empty(self):
        self.assertEqual(self.manager._entries, {})
        self.assertEqual(self.manager._current_size, 0)
        self.assertEqual(list(self.manager._storage_root.iterdir()), [])

    def test_mkdir_failure_releases_slot_and_budget(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.store()
        self.assert_empty()
        self.assertIsNotNone(self.manager.get_entry(self.store()))

    def test_write_failure_removes_partial_file_and_budget(self):
        class BrokenReader(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError(errno.ENOSPC, "full")
                return super().read(size)

        with self.assertRaises(self.namespace["CustodyStorageFullError"]):
            self.manager.store_file("sample", BrokenReader(b"partial"))
        self.assert_empty()

    def test_actual_file_write_failure_removes_reserved_bytes(self):
        class BrokenWriter(io.BytesIO):
            def write(self, data):
                super().write(data[:2])
                raise OSError(errno.ENOSPC, "full")

        with patch.dict(self.namespace, {"open": lambda *args, **kwargs: BrokenWriter()}):
            with self.assertRaises(self.namespace["CustodyStorageFullError"]):
                self.store()
        self.assert_empty()

    def test_file_size_limit_releases_partial_reservation(self):
        self.manager._max_file_size = 5
        with self.assertRaises(self.namespace["FileTooLargeError"]):
            self.store(b"123456")
        self.assert_empty()

    def test_concurrent_upload_bytes_are_reserved_before_publication(self):
        self.manager._max_size = 5
        first = PausedReader(b"1234", pause_at=1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.manager.store_file, "first", first)
            try:
                self.assertTrue(first.paused.wait(timeout=3))
                self.assertEqual(self.manager._current_size, 4)
                with self.assertRaises(self.namespace["CustodyStorageFullError"]):
                    self.store(b"56")
                self.assertEqual(self.manager._current_size, 4)
            finally:
                first.resume.set()
            token = future.result(timeout=3)
        self.assertEqual(self.manager.get_entry(token).file_size, 4)

    def test_failed_manifest_publish_cleans_upload_preserving_old_entries(self):
        original = self.store(b"old", permanent=True)
        with patch.object(self.namespace["os"], "replace", side_effect=OSError("manifest failed")):
            with self.assertRaises(OSError):
                self.store(b"new", permanent=True)
        self.assertEqual(set(self.manager._entries), {original})
        self.assertEqual(self.manager._current_size, 3)
        self.assertEqual(set(json.loads(self.manager._manifest_path.read_text())), {original})
        self.assertFalse(self.manager._manifest_path.with_suffix(".tmp").exists())
        self.assertEqual(len(list(self.manager._storage_root.glob("*/*"))), 1)

    def test_other_permanent_publication_does_not_save_unfinished_upload(self):
        first = PausedReader(b"pending")
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.manager.store_file, "pending", first, None, False, None, True)
            try:
                self.assertTrue(first.paused.wait(timeout=3))
                ready = self.store(b"ready", permanent=True)
                self.assertEqual(set(json.loads(self.manager._manifest_path.read_text())), {ready})
            finally:
                first.resume.set()
            finished = pending.result(timeout=3)
        restored = self.namespace["FileCustodyManager"]()
        self.assertEqual(set(restored._entries), {ready, finished})
        self.assertEqual(restored._current_size, 12)

    def test_relay_duplicate_waits_for_committed_bytes(self):
        token = "relay-" + "a" * 32
        first = PausedReader(b"complete")
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(self.manager.store_file, "sample", first, explicit_token=token)
            try:
                self.assertTrue(first.paused.wait(timeout=3))
                duplicate = executor.submit(self.manager.store_file, "sample", io.BytesIO(b"ignored"), explicit_token=token)
                self.assertFalse(duplicate.done())
                self.assertIsNone(self.manager.get_entry(token))
            finally:
                first.resume.set()
            self.assertEqual(pending.result(timeout=3), token)
            self.assertEqual(duplicate.result(timeout=3), token)
        self.assertEqual(self.manager._current_size, 8)
        self.assertEqual((self.manager.get_entry(token).file_dir / "sample").read_bytes(), b"complete")

    def test_failed_delete_keeps_budget_and_retries(self):
        token = self.store()
        with patch.object(self.namespace["shutil"], "rmtree", side_effect=PermissionError("busy")), self.assertLogs("custody_lifecycle_test", level="WARNING"):
            self.manager.delete_entry(token)
        self.assertEqual(self.manager._current_size, 8)
        self.assertIsNone(self.manager.get_entry(token))
        self.manager.cleanup_expired()
        self.assert_empty()

    def test_download_leases_survive_expiry_and_final_download_cleanup(self):
        token = self.store(max_downloads=2)
        first = self.manager.get_file_path(token)
        second = self.manager.get_file_path(token)
        self.assertIsNone(self.manager.get_file_path(token))
        self.manager._entries[token].expires_at = 1
        self.manager.cleanup_expired()
        self.assertTrue(first[0].is_file())
        self.manager.release_file(token)
        self.assertTrue(second[0].is_file())
        self.assertEqual(self.manager._current_size, 8)
        self.manager.release_file(token)
        self.assert_empty()

    def test_stage_in_lease_does_not_spend_download_count(self):
        token = self.store(max_downloads=1)
        for _ in range(3):
            self.assertIsNotNone(self.manager.acquire_file(token, count_download=False))
            self.manager.release_file(token)
        self.assertEqual(self.manager.get_entry(token).download_count, 0)
        self.assertIsNotNone(self.manager.get_file_path(token))
        self.manager.release_file(token)
        self.assert_empty()

    def test_stage_in_caller_releases_lease_on_transfer_failure(self):
        tree = ast.parse((SERVER / "_scheduler" / "_staging.py").read_text(encoding="utf-8"))
        method = next(
            item for node in tree.body if isinstance(node, ast.ClassDef)
            for item in node.body if getattr(item, "name", None) == "_stage_in_custody"
        )
        namespace = {
            "file_custody_manager": self.manager,
            "_FILE_SECRET_RE": re.compile(r"magnus-secret:([0-9A-Za-z_-]+)"),
            "_CUSTODY_DROPIN_SUBDIR": ".magnus_custody_dropin",
            "os": os,
            "json": json,
            "logger": self.namespace["logger"],
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), "_stage_in_custody", "exec"), namespace)
        token = self.store(max_downloads=1)

        def failed_push(source, destination):
            self.assertTrue(Path(source).is_file())
            self.assertEqual(self.manager._entries[token].active_readers, 1)
            self.manager.delete_entry(token)
            self.assertTrue(Path(source).is_file())
            raise RuntimeError("transport disconnected")

        scheduler = SimpleNamespace(
            _is_remote_execution=lambda: True,
            _remote_job_working_table=lambda job_id: "/remote/job",
            _local_job_working_table=lambda job_id: self.temp.name,
            slurm_manager=SimpleNamespace(transport=SimpleNamespace(
                run=lambda command: SimpleNamespace(returncode=0),
                push=failed_push,
            )),
        )
        with self.assertRaisesRegex(RuntimeError, "transport disconnected"):
            namespace["_stage_in_custody"](scheduler, "job", f"magnus-secret:{token}")
        self.assert_empty()

    def test_file_response_releases_lease_after_send_error(self):
        from starlette.responses import FileResponse

        tree = ast.parse((SERVER / "routers" / "files.py").read_text(encoding="utf-8"))
        response_type = next(node for node in tree.body if getattr(node, "name", None) == "CustodyFileResponse")
        namespace = {"FileResponse": FileResponse, "file_custody_manager": self.manager}
        exec(compile(ast.Module(body=[response_type], type_ignores=[]), "CustodyFileResponse", "exec"), namespace)
        token = self.store(b"x" * 131072, max_downloads=1)
        acquired = self.manager.get_file_path(token)
        response = namespace["CustodyFileResponse"](token, path=str(acquired[0]))

        async def send(message):
            if message["type"] == "http.response.body":
                self.manager.cleanup_expired()
                self.assertTrue(acquired[0].is_file())
                raise ConnectionError("client disconnected")

        async def receive():
            return {"type": "http.disconnect"}

        with self.assertRaises(ConnectionError):
            asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
        self.assert_empty()

    def test_file_response_releases_lease_after_task_cancellation(self):
        from starlette.responses import FileResponse

        tree = ast.parse((SERVER / "routers" / "files.py").read_text(encoding="utf-8"))
        response_type = next(node for node in tree.body if getattr(node, "name", None) == "CustodyFileResponse")
        namespace = {"FileResponse": FileResponse, "file_custody_manager": self.manager}
        exec(compile(ast.Module(body=[response_type], type_ignores=[]), "CustodyFileResponse", "exec"), namespace)
        token = self.store(b"x" * 131072, max_downloads=1)
        acquired = self.manager.get_file_path(token)
        response = namespace["CustodyFileResponse"](token, path=str(acquired[0]))

        async def check():
            streaming = asyncio.Event()

            async def send(message):
                if message["type"] == "http.response.body":
                    streaming.set()
                    await asyncio.Event().wait()

            async def receive():
                return {"type": "http.disconnect"}

            task = asyncio.create_task(response({"type": "http", "method": "GET", "headers": []}, receive, send))
            await asyncio.wait_for(streaming.wait(), timeout=3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assert_empty()

        asyncio.run(check())

    def test_explicit_delete_waits_for_active_reader(self):
        token = self.store(permanent=True)
        acquired = self.manager.get_file_path(token)
        self.manager.delete_entry(token)
        self.assertIsNone(self.manager.get_file_path(token))
        self.assertTrue(acquired[0].is_file())
        self.assertEqual(json.loads(self.manager._manifest_path.read_text()), {})
        self.manager.release_file(token)
        self.assertFalse(acquired[0].exists())
        self.assertEqual(json.loads(self.manager._manifest_path.read_text()), {})

    def test_restart_does_not_restore_retired_permanent_download(self):
        token = self.store(b"old avatar", permanent=True)
        self.manager.get_file_path(token)
        self.manager.delete_entry(token)
        restarted = self.namespace["FileCustodyManager"]()
        self.assertIsNone(restarted.get_entry(token))
        self.assertEqual(restarted._current_size, 0)

    def test_stale_cleanup_cannot_delete_new_relay_registration(self):
        token = "relay-" + "b" * 32
        self.store(explicit_token=token)
        original = self.manager._entries[token]
        self.manager.delete_entry(token)
        self.store(b"replacement", explicit_token=token)
        self.manager.delete_entry(token, expected_entry=original)
        self.assertEqual(self.manager.get_entry(token).file_size, 11)

    def test_shutdown_does_not_remove_active_upload_directory(self):
        source = PausedReader(b"pending", pause_at=1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.manager.store_file, "sample", source)
            try:
                self.assertTrue(source.paused.wait(timeout=3))
                entry = next(iter(self.manager._entries.values()))
                self.manager.shutdown()
                self.assertTrue(entry.file_dir.exists())
            finally:
                source.resume.set()
            with self.assertRaises(self.namespace["CancelledError"]):
                pending.result(timeout=3)
        self.assert_empty()

    def test_cancelled_async_upload_joins_reader_before_request_can_close(self):
        async def check():
            source = PausedReader(b"pending")
            task = asyncio.create_task(self.manager.store_file_async("sample", source))
            self.assertTrue(await asyncio.to_thread(source.paused.wait, 3))
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            task.cancel()
            source.resume.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            source.close()
            self.assert_empty()

        asyncio.run(check())

    def test_cancellation_after_publication_rolls_back_unreturned_token(self):
        async def check():
            stored = threading.Event()
            resume = threading.Event()
            real_store = self.manager.store_file

            def delayed_return(*args, **kwargs):
                token = real_store(*args, **kwargs)
                stored.set()
                self.assertTrue(resume.wait(timeout=3))
                return token

            with patch.object(self.manager, "store_file", side_effect=delayed_return):
                task = asyncio.create_task(self.manager.store_file_async("sample", io.BytesIO(b"ready")))
                self.assertTrue(await asyncio.to_thread(stored.wait, 3))
                task.cancel()
                resume.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assert_empty()

        asyncio.run(check())

    def avatar_endpoint(self):
        from fastapi import HTTPException

        tree = ast.parse((SERVER / "routers" / "users.py").read_text(encoding="utf-8"))
        endpoint = next(node for node in tree.body if getattr(node, "name", None) == "upload_avatar")
        endpoint.decorator_list = []
        endpoint.returns = None
        endpoint.args.defaults = [ast.Constant(value=None) for _ in endpoint.args.defaults]
        for argument in endpoint.args.args:
            argument.annotation = None
        namespace = {
            "asyncio": asyncio,
            "io": io,
            "file_custody_manager": self.manager,
            "HTTPException": HTTPException,
            "_ALLOWED_AVATAR_TYPES": {"image/png"},
            "_MAX_AVATAR_SIZE": 2 * 1024 * 1024,
            "logger": self.namespace["logger"],
            "models": SimpleNamespace(User=SimpleNamespace(id="user")),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[endpoint], type_ignores=[])), "upload_avatar", "exec"), namespace)
        old_token = self.store(b"old avatar", permanent=True)
        target = SimpleNamespace(id="user", avatar_url=f"/api/files/download/{old_token}")
        database = Mock()
        database.query.return_value.filter.return_value.first.return_value = target
        upload = SimpleNamespace(filename="avatar.png", content_type="image/png", read=AsyncMock(return_value=b"new avatar"))
        return namespace["upload_avatar"], old_token, target, database, upload

    def test_oversize_avatar_does_not_delete_current_avatar(self):
        from fastapi import HTTPException

        endpoint, old_token, target, database, upload = self.avatar_endpoint()
        upload.read.return_value = b"x" * (2 * 1024 * 1024 + 1)
        with self.assertRaises(HTTPException):
            asyncio.run(endpoint("user", upload, database, target))
        self.assertIsNotNone(self.manager.get_entry(old_token))
        database.commit.assert_not_called()

    def test_avatar_database_failure_keeps_old_and_removes_new_file(self):
        endpoint, old_token, target, database, upload = self.avatar_endpoint()
        database.commit.side_effect = RuntimeError("database unavailable")
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            asyncio.run(endpoint("user", upload, database, target))
        database.rollback.assert_called_once()
        self.assertEqual(set(self.manager._entries), {old_token})
        self.assertEqual(set(json.loads(self.manager._manifest_path.read_text())), {old_token})

    def test_avatar_replacement_keeps_old_file_until_database_commit(self):
        endpoint, old_token, target, database, upload = self.avatar_endpoint()

        def commit():
            self.assertIsNotNone(self.manager.get_entry(old_token))
            self.assertEqual(len(self.manager._entries), 2)

        database.commit.side_effect = commit
        result = asyncio.run(endpoint("user", upload, database, target))
        self.assertIsNone(self.manager.get_entry(old_token))
        self.assertEqual(result["avatar_url"], target.avatar_url)
        self.assertEqual(len(self.manager._entries), 1)


if __name__ == "__main__":
    unittest.main()
