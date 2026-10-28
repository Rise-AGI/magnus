"""Source SDK against the real isolated tus/custody HTTP routes."""
import importlib
import io
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import patch

import httpx

from test_resumable_uploads import ResumableTests


package = types.ModuleType("_resumable_sdk_test")
package.__path__ = [str(Path(__file__).resolve().parents[3] / "sdks/python/src/magnus")]
sys.modules[package.__name__] = package
sdk = importlib.import_module(package.__name__ + "._resumable_upload")


class SDKUploadTests(unittest.TestCase):
    set_job = ResumableTests.set_job

    def setUp(self):
        ResumableTests.setUp(self)
        self.requests = []
        self.lost = set()
        self.drop = set()
        self.unsupported = False

        def transport(request):
            self.requests.append(request.method)
            if self.unsupported:
                return httpx.Response(405)
            response = self.api.request(
                request.method,
                "/api" + request.url.path,
                headers={**dict(request.headers), "Authorization": "Bearer test"},
                content=request.read(),
            )
            if request.method in self.drop and request.method not in self.lost:
                self.lost.add(request.method)
                raise httpx.ReadTimeout("reply lost", request=request)
            return httpx.Response(response.status_code, headers=response.headers, content=response.content)

        self.client = httpx.Client(transport=httpx.MockTransport(transport), base_url="https://test.invalid")
        self.addCleanup(self.client.close)

    def upload(self, payload=b"scientific evidence"):
        sleep = time.sleep
        with patch.object(sdk, "THRESHOLD", 0), patch.object(sdk.time, "sleep", side_effect=lambda _: sleep(0.01)):
            return sdk.resumable_upload(self.client, "answer.bin", io.BytesIO(payload), {"expire_minutes": "60"}, 2, lambda response: response.raise_for_status())

    def test_lost_creation_and_patch_responses_reconcile_without_duplicate_file(self):
        self.drop = {"POST", "PATCH"}
        response = self.upload()
        self.assertTrue(response.json()["file_secret"].startswith("magnus-secret:"))
        self.assertEqual(self.lost, {"POST", "PATCH"})
        self.assertEqual(len(self.manager._index.load()), 1)
        with self.manager._index.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM uploads").fetchone()[0], 1)
        self.assertNotIn("/files/upload", self.requests)

    def test_legacy_fallback_is_only_before_creation(self):
        self.unsupported = True
        self.assertIsNone(self.upload())
        self.assertEqual(self.requests, ["OPTIONS"])

    def test_response_failure_after_creation_never_falls_back(self):
        def fail(request):
            if request.method == "OPTIONS":
                return httpx.Response(204, headers={"Tus-Version": "1.0.0", "Magnus-Upload-Chunk-Size": "4096"})
            self.requests.append(request.method)
            raise httpx.ReadTimeout("uncertain creation", request=request)
        self.client.close()
        self.client = httpx.Client(transport=httpx.MockTransport(fail), base_url="https://test.invalid")
        self.addCleanup(self.client.close)
        with self.assertRaises(sdk.UploadOutcomeUnknown):
            self.upload()
        self.assertEqual(self.requests, ["POST"] * 3)


if __name__ == "__main__":
    unittest.main()
