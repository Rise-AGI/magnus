"""HTTP, crash recovery and unique custody publication in isolated storage."""
import ast
import asyncio
from contextlib import contextmanager
import threading
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_custody_status import CustodyStatusTests, SERVER


upload_module = runpy.run_path(str(SERVER / "_custody_receipts.py"))
upload_tree = ast.parse((SERVER / "_file_custody_manager/_uploads.py").read_text())
upload_tree.body = [node for node in upload_tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
exec(compile(upload_tree, "_uploads.py", "exec"), upload_module)
UploadError = upload_module["UploadError"]


class ResumableTests(unittest.TestCase):
    set_job = CustodyStatusTests.set_job

    def setUp(self):
        CustodyStatusTests.setUp(self)
        namespace = dict(self.router_namespace, **{
            key: value for key, value in upload_module.items() if key in {"UploadStore", "UploadError", "CHUNK_SIZE"}
        })
        tree = ast.parse((SERVER / "routers/uploads.py").read_text())
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        exec(compile(tree, "uploads.py", "exec"), namespace)
        self.uploads = namespace["upload_store"]
        self.addCleanup(self.uploads.shutdown)
        self.upload_namespace = namespace
        app = FastAPI()
        middleware = runpy.run_path(str(SERVER / "_upload_receive.py"))["UploadTelemetryMiddleware"]
        app.add_middleware(middleware)
        app.include_router(self.router_namespace["router"], prefix="/api")
        app.include_router(namespace["router"], prefix="/api")
        self.api = TestClient(app)
        self.addCleanup(self.api.close)
        self.headers = {"Authorization": "Bearer test", "Tus-Resumable": "1.0.0"}

    def create(self, payload=b"content", key=None, checksum=None, options=None):
        metadata = {"filename": "answer.bin", "sha256": checksum or hashlib.sha256(payload).hexdigest(), "options": json.dumps(options or {"max_downloads": 1})}
        key = key or f"{int(time.time())}-{uuid.uuid4().hex}"
        headers = {
            **self.headers,
            "Upload-Length": str(len(payload)),
            "Upload-Metadata": ",".join(name + " " + base64.b64encode(value.encode()).decode() for name, value in metadata.items()),
            "Idempotency-Key": key,
        }
        response = self.api.post("/api/files/uploads", headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.headers["location"], headers

    def chunk(self, path, payload, offset=0, checksum=None):
        return self.api.patch(path, content=payload, headers={
            **self.headers,
            "Content-Type": "application/offset+octet-stream",
            "Upload-Offset": str(offset),
            "Upload-Checksum": "sha256 " + base64.b64encode(checksum or hashlib.sha256(payload).digest()).decode(),
        })

    def completed(self, path):
        for _ in range(200):
            response = self.api.get(path, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result["state"] not in {"open", "finalizing"}:
                return result
            time.sleep(0.01)
        self.fail("Publication did not complete")

    def test_bad_checksum_and_offset_do_not_change_acknowledged_bytes(self):
        path, _ = self.create(b"abcdef")
        self.assertEqual(self.chunk(path, b"abc", checksum=b"x" * 32).status_code, 460)
        self.assertEqual(self.chunk(path, b"abc", offset=1).status_code, 409)
        self.assertEqual(self.chunk(path, b"abc").status_code, 204)
        self.assertEqual(self.api.head(path, headers=self.headers).headers["upload-offset"], "3")
        self.assertEqual(self.chunk(path, b"def", offset=3).status_code, 204)
        receipt = self.completed(path)
        self.assertEqual(receipt["state"], "complete")
        downloaded = self.api.get("/api/files/download/" + receipt["file_secret"].removeprefix("magnus-secret:"))
        self.assertEqual(downloaded.content, b"abcdef")

    def test_lost_creation_and_completion_replies_do_not_republish_consumed_file(self):
        path, headers = self.create()
        second = self.api.post("/api/files/uploads", headers=headers)
        self.assertEqual(second.headers["location"], path)
        self.assertEqual(self.chunk(path, b"content").status_code, 204)
        receipt = self.completed(path)
        token = receipt["file_secret"].removeprefix("magnus-secret:")
        self.assertEqual(self.api.get("/api/files/download/" + token).content, b"content")
        again = self.api.post("/api/files/uploads", headers=headers)
        self.assertEqual(again.headers["location"], path)
        self.assertEqual(self.completed(path)["file_secret"], receipt["file_secret"])
        self.assertNotEqual(self.completed(path)["custody_status"], "ready")
        self.assertEqual(self.manager._index.load(), {})

    def test_owner_version_changed_binding_and_cancel(self):
        path, headers = self.create()
        upload_id = path.rsplit("/", 1)[1]
        with self.assertRaises(UploadError) as wrong_owner:
            self.uploads.status(upload_id, "another-owner")
        self.assertEqual(wrong_owner.exception.status, 404)
        self.assertEqual(self.api.head(path).status_code, 412)
        self.assertEqual(self.api.head(path, headers={"Tus-Resumable": "1.0.0"}).status_code, 401)
        changed = {**headers, "Upload-Length": "2"}
        self.assertEqual(self.api.post("/api/files/uploads", headers=changed).status_code, 409)
        self.uploads.claim(upload_id, "owner", 0, "writer")
        self.assertEqual(self.api.delete(path, headers=self.headers).status_code, 409)
        self.uploads.release(upload_id, "someone-else")
        self.assertEqual(self.chunk(path, b"x").status_code, 409)
        self.uploads.release(upload_id, "writer")
        self.assertEqual(self.api.delete(path, headers=self.headers).status_code, 204)
        self.assertEqual(self.api.head(path, headers=self.headers).status_code, 410)

    def test_restart_truncates_unacknowledged_tail_and_preserves_partial(self):
        path, _ = self.create(b"abcdef")
        self.assertEqual(self.chunk(path, b"abc").status_code, 204)
        upload_id = path.rsplit("/", 1)[1]
        self.uploads._path(upload_id).write_bytes(b"abcUNACKNOWLEDGED")
        self.uploads.shutdown()
        restarted = upload_module["UploadStore"](self.manager)
        self.addCleanup(restarted.shutdown)
        self.uploads = restarted
        self.upload_namespace["upload_store"] = restarted
        self.assertEqual(self.api.head(path, headers=self.headers).headers["upload-offset"], "3")
        self.assertEqual(self.chunk(path, b"def", offset=3).status_code, 204)
        self.assertEqual(self.completed(path)["state"], "complete")

    def test_concurrent_creation_returns_one_upload(self):
        path, headers = self.create()
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: self.api.post("/api/files/uploads", headers=headers), range(12)))
        self.assertEqual({response.headers["location"] for response in responses}, {path})
        with self.manager._index.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM uploads").fetchone()[0], 1)

    def test_full_checksum_failure_never_publishes(self):
        path, _ = self.create(checksum="0" * 64)
        self.assertEqual(self.chunk(path, b"content").status_code, 204)
        self.assertEqual(self.completed(path)["state"], "failed")
        self.assertEqual(self.manager._index.load(), {})

    def test_expired_creation_key_cannot_resurrect_an_evicted_receipt(self):
        path, headers = self.create()
        upload_id = path.rsplit("/", 1)[1]
        with patch.object(upload_module["time"], "time", return_value=time.time() + 3 * 86400):
            self.uploads.cleanup()
            self.assertEqual(self.api.post("/api/files/uploads", headers=headers).status_code, 410)
        self.assertFalse(self.uploads._path(upload_id).exists())

    def test_failed_directory_sync_does_not_accept_creation(self):
        with patch.dict(upload_module["UploadStore"].create.__globals__, {"_sync_directory": lambda path: (_ for _ in ()).throw(OSError("disk error"))}):
            with self.assertRaises(OSError):
                self.uploads.create("owner", f"{int(time.time())}-{uuid.uuid4().hex}", 3, {"filename": "data", "options": {}})
        with self.manager._index.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM uploads").fetchone()[0], 0)
        self.assertEqual(list(self.uploads.root.iterdir()), [])

    def test_tus_sha1_and_method_override(self):
        path, _ = self.create(b"abc")
        result = self.api.post(path, content=b"abc", headers={
            **self.headers, "X-HTTP-Method-Override": "PATCH",
            "Content-Type": "application/offset+octet-stream", "Upload-Offset": "0",
            "Upload-Checksum": "sha1 " + base64.b64encode(hashlib.sha1(b"abc").digest()).decode(),
        })
        self.assertEqual(result.status_code, 204, result.text)
        self.assertEqual(self.completed(path)["state"], "complete")
        self.assertEqual(self.api.head(path, headers={"Tus-Resumable": "1.0.0"}).headers["tus-resumable"], "1.0.0")

    def test_idempotency_key_requires_content_binding(self):
        response = self.api.post("/api/files/uploads", headers={
            **self.headers, "Upload-Length": "3", "Idempotency-Key": f"{int(time.time())}-{uuid.uuid4().hex}",
        })
        self.assertEqual(response.status_code, 400)
        unkeyed = self.api.post("/api/files/uploads", headers={**self.headers, "Upload-Length": "3"})
        self.assertEqual(unkeyed.status_code, 201)

    def test_worker_failure_and_failed_retry_record_can_be_recovered(self):
        path, _ = self.create(b"abc")
        upload_id = path.rsplit("/", 1)[1]
        row = self.uploads.claim(upload_id, "owner", 0, "writer")
        self.uploads.append(row, b"abc", None)
        self.uploads.release(upload_id, "writer")
        row = self.uploads._read(upload_id, "owner")
        with self.manager._index.transaction() as db:
            db.execute("UPDATE uploads SET state='finalizing' WHERE id=?", (upload_id,))
        self.uploads.busy[upload_id] = "publication"
        with patch.object(self.uploads, "_path", side_effect=OSError("unavailable")), patch.object(self.manager._index, "transaction", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                self.uploads._publish(row)
        self.assertNotIn(upload_id, self.uploads.busy)
        self.assertEqual(self.completed(path)["state"], "complete")

    def test_cancelled_waiter_releases_its_claim_after_the_worker_finishes(self):
        path, _ = self.create(b"abc")
        upload_id = path.rsplit("/", 1)[1]
        acquired = threading.Event()
        resume = threading.Event()
        original = self.uploads.claim
        def delayed(*args):
            result = original(*args)
            acquired.set()
            if not resume.wait(3):
                raise TimeoutError()
            return result
        async def run():
            from starlette.requests import Request
            from types import SimpleNamespace
            request = Request({"type": "http", "headers": [(b"content-type", b"application/offset+octet-stream"), (b"upload-offset", b"0")]})
            task = asyncio.create_task(self.upload_namespace["upload_chunk"](upload_id, request, SimpleNamespace(id="owner")))
            await asyncio.to_thread(acquired.wait, 2)
            task.cancel()
            resume.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with patch.object(self.uploads, "claim", delayed):
            asyncio.run(run())
        self.assertNotIn(upload_id, self.uploads.busy)
        self.assertEqual(self.chunk(path, b"abc").status_code, 204)

    def test_old_custody_index_and_manager_leave_partial_uploads_intact(self):
        path, _ = self.create(b"abcdef")
        self.assertEqual(self.chunk(path, b"abc").status_code, 204)
        upload_id = path.rsplit("/", 1)[1]
        self.uploads.shutdown()
        # Load the actual previous implementation, rather than a compatibility stub.
        namespace = dict(self.namespace)
        for name in ("_index.py", "_manager.py"):
            source = subprocess.check_output(["git", "show", "7676810:back_end/server/_file_custody_manager/" + name], text=True)
            tree = ast.parse(source)
            tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom) and (node.level or node.module == "library"))]
            exec(compile(tree, name, "exec"), namespace)
        previous = namespace["FileCustodyManager"]()
        self.assertEqual(self.uploads._path(upload_id).read_bytes(), b"abc")
        self.assertIsInstance(previous.store_file("legacy.bin", io.BytesIO(b"legacy")), str)
        previous.shutdown()


if __name__ == "__main__":
    unittest.main()
