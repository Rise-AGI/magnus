"""Exercise the real multipart route without production singletons."""
import asyncio
import gc
import io
import runpy
import unittest
import weakref
from unittest.mock import patch

from fastapi import HTTPException, Request
from starlette import formparsers

from test_custody_status import CustodyStatusTests
from test_file_custody_lifecycle import SERVER


receive_module = runpy.run_path(str(SERVER / "_upload_receive.py"))
UploadAdmission = receive_module["UploadAdmission"]


class UploadRouteTests(CustodyStatusTests):
    def test_unauthorized_request_does_not_start_multipart_spooling(self):
        with patch.object(formparsers, "SpooledTemporaryFile", side_effect=AssertionError("parsed before auth")):
            response = self.http.post("/api/files/upload", files={"file": ("test", b"x" * 2048)})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.manager._entries)

    def test_legacy_file_and_metadata_roundtrip(self):
        response = self.http.post(
            "/api/files/upload",
            files={"file": ("example.bin", b"original bytes")},
            data={"expire_minutes": "5", "max_downloads": "2", "is_directory": "false"},
            headers={"Authorization": "Bearer test"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["file_secret"].split(":", 1)[1]
        entry = self.manager.get_entry(token)
        self.assertEqual(entry.max_downloads, 2)
        self.assertEqual(entry.file_size, 14)
        self.assertEqual(self.http.get(f"/api/files/download/{token}").content, b"original bytes")

    def test_declared_limit_rejects_before_parser(self):
        self.router_namespace["_upload_admission"].max_body_bytes = 100
        with patch.object(formparsers, "SpooledTemporaryFile", side_effect=AssertionError("unexpected spool")):
            response = self.http.post(
                "/api/files/upload",
                files={"file": ("test", b"x" * 256)},
                headers={"Authorization": "Bearer test"},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.router_namespace["_upload_admission"]._active, 0)

    def test_disconnect_closes_partial_spool_without_gc(self):
        references = []
        original = formparsers.SpooledTemporaryFile

        def spool(*args, **kwargs):
            value = original(*args, **kwargs)
            references.append(weakref.ref(value))
            return value

        async def exchange():
            events = iter([
                {"type": "http.request", "body": b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="test"\r\n\r\n' + b"x" * (2 * 1024 * 1024), "more_body": True},
                {"type": "http.disconnect"},
            ])
            messages = []

            async def receive():
                return next(events)

            async def send(message):
                messages.append(message)

            await self.http.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "POST", "scheme": "http", "path": "/api/files/upload",
                "raw_path": b"/api/files/upload", "query_string": b"", "root_path": "",
                "headers": [(b"authorization", b"Bearer test"), (b"content-type", b"multipart/form-data; boundary=boundary")],
                "client": ("test", 1), "server": ("test", 80),
            }, receive, send)
            return messages

        gc.disable()
        try:
            with patch.object(formparsers, "SpooledTemporaryFile", spool):
                messages = asyncio.run(exchange())
            self.assertTrue(references)
            self.assertTrue(all(ref() is None or ref().closed for ref in references))
            self.assertEqual(messages[0]["status"], 400)
            self.assertEqual(self.router_namespace["_upload_admission"]._active, 0)
        finally:
            gc.enable()


class UploadBudgetTests(unittest.IsolatedAsyncioTestCase):
    def request(self, chunks):
        events = iter(chunks)

        async def receive():
            return {"type": "http.request", "body": next(events), "more_body": True}

        return Request({"type": "http", "headers": []}, receive)

    async def test_unknown_length_stops_at_budget_and_releases_reservation(self):
        admission = UploadAdmission(max_spool_bytes=5)
        request = self.request([b"1234", b"56"])
        with self.assertRaises(HTTPException) as error:
            async with admission.receive(request):
                await request.receive()
                self.assertEqual(admission._reserved, 4)
                await request.receive()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual((admission._active, admission._reserved), (0, 0))

    async def test_concurrent_receivers_share_the_budget(self):
        admission = UploadAdmission(max_concurrent=1)
        async with admission.receive(self.request([])):
            with self.assertRaises(HTTPException) as error:
                async with admission.receive(self.request([])):
                    self.fail("second receiver admitted")
            self.assertEqual(error.exception.status_code, 429)
        self.assertEqual((admission._active, admission._reserved), (0, 0))


if __name__ == "__main__":
    unittest.main()
