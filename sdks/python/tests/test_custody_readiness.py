"""Opt-in readiness must never spend a download before bytes are available."""
import asyncio
from contextlib import nullcontext
import ast
import importlib
import re
from pathlib import Path
import sys
import tempfile
import types
from typing import Optional
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
import typer
from typer.testing import CliRunner


PACKAGE = "_magnus_readiness_tests"
SOURCE = Path(__file__).resolve().parents[1] / "src" / "magnus"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SOURCE)]
sys.modules[PACKAGE] = package
client_module = importlib.import_module(f"{PACKAGE}.client")
download = importlib.import_module(f"{PACKAGE}.http_download")
errors = importlib.import_module(f"{PACKAGE}.exceptions")


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "result"
        self.target.write_bytes(b"original")
        self.secret = "magnus-secret:relay-" + "a" * 32
        self.client = client_module.MagnusClient.__new__(client_module.MagnusClient)
        self.client.address = "https://example.invalid"
        self.client._client = Mock()
        self.patchers = [
            patch.object(package, "default_client", self.client, create=True),
            patch.object(download, "_read_staged_dropin", return_value=None),
            patch.object(download, "_download_http", return_value=self.target),
        ]
        self.mocks = [p.start() for p in self.patchers]
        for p in self.patchers:
            self.addCleanup(p.stop)

    def statuses(self, responses):
        self.requests = []
        replies = iter(responses)

        def handler(request):
            self.requests.append(request)
            status, body = next(replies)
            return httpx.Response(status, json=body)

        self.client._client = httpx.Client(
            base_url = "https://example.invalid/api/",
            transport = httpx.MockTransport(handler),
        )
        self.addCleanup(self.client._client.close)

    def receive(self, **kwargs):
        return download.download_file(
            self.secret,
            str(self.target),
            300,
            False,
            **kwargs,
        )

    def test_default_positional_call_never_checks_readiness(self):
        self.client.get_file_status = Mock(side_effect=AssertionError("unexpected query"))
        self.assertEqual(self.receive(), self.target)
        self.mocks[2].assert_called_once_with(self.secret.removeprefix("magnus-secret:"), str(self.target), 300, False)

    def test_unknown_inflight_then_ready_makes_one_download(self):
        self.statuses([
            (200, {"status": "unknown", "job_transfers_pending": True}),
            (200, {"status": "pending", "job_transfers_pending": True}),
            (200, {"status": "ready", "job_transfers_pending": True}),
        ])
        with patch.object(download.time, "sleep"):
            self.receive(job_id="producer", wait_timeout=30)
        self.assertEqual(len(self.requests), 3)
        self.assertTrue(all(r.url.params["job_id"] == "producer" for r in self.requests))
        self.assertTrue(all("/files/status/relay-" in r.url.path for r in self.requests))
        self.mocks[2].assert_called_once()

    def test_unavailable_and_unknown_without_context_fail_without_download(self):
        for status in ("unavailable", "unknown"):
            with self.subTest(status=status):
                self.statuses([(200, {"status": status, "job_transfers_pending": None})])
                with self.assertRaises(errors.MagnusError):
                    self.receive(wait_timeout=30)
        self.mocks[2].assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"original")

    def test_wait_deadline_does_not_change_download_timeout_or_existing_target(self):
        clock = [10.0]

        def status(*args, **kwargs):
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 3)
            return {"status": "unknown", "job_transfers_pending": True}

        def sleep(seconds):
            clock[0] += seconds

        self.client.get_file_status = Mock(side_effect=status)
        with patch.object(download.time, "monotonic", side_effect=lambda: clock[0]), patch.object(download.time, "sleep", side_effect=sleep):
            with self.assertRaisesRegex(errors.MagnusError, "Timed out waiting"):
                self.receive(job_id="producer", wait_timeout=3, poll_interval=2)
        self.assertEqual(clock[0], 13)
        self.assertEqual(self.client.get_file_status.call_count, 2)
        self.mocks[2].assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"original")

    def test_auth_old_server_and_unknown_job_are_not_retried(self):
        for code in (401, 403, 404):
            with self.subTest(code=code):
                self.statuses([(code, {"detail": "unavailable endpoint or job"})])
                with self.assertRaises(errors.APIError) as caught:
                    self.receive(job_id="producer", wait_timeout=30)
                self.assertEqual(caught.exception.status_code, code)
                self.assertEqual(len(self.requests), 1)
        self.mocks[2].assert_not_called()

    def test_invalid_status_response_cannot_start_download(self):
        for payload in ({"status": "ready", "job_transfers_pending": "true"}, {"status": "not-a-state"}, []):
            with self.subTest(payload=payload):
                self.statuses([(200, payload)])
                with self.assertRaisesRegex(errors.MagnusError, "Invalid file status"):
                    self.receive(wait_timeout=30)
        self.mocks[2].assert_not_called()

    def test_status_network_and_non_json_errors_preserve_target(self):
        for error in (httpx.ConnectError("offline"), httpx.ReadTimeout("slow")):
            with self.subTest(error=type(error).__name__):
                self.client._client = Mock()
                self.client._client.get.side_effect = error
                with self.assertRaises(errors.MagnusError):
                    self.receive(wait_timeout=30)
        self.client._client = Mock()
        self.client._client.get.return_value = httpx.Response(200, content=b"not-json")
        with self.assertRaisesRegex(errors.MagnusError, "Invalid file status"):
            self.receive(wait_timeout=30)
        self.mocks[2].assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"original")

    def test_staged_offline_input_works_without_status_api(self):
        self.mocks[1].return_value = self.target
        self.client.get_file_status = Mock(side_effect=AssertionError("unexpected network"))
        self.assertEqual(self.receive(job_id="producer", wait_timeout=30), self.target)
        self.mocks[2].assert_not_called()

    def test_invalid_wait_bounds_fail_before_files_or_network(self):
        for options in ({"wait_timeout": -1}, {"wait_timeout": float("inf")}, {"wait_timeout": float("nan")}, {"poll_interval": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.receive(**options)
        self.mocks[1].assert_not_called()
        self.mocks[2].assert_not_called()

    def test_async_wait_cancellation_cannot_start_later_download(self):
        async def check():
            entered = asyncio.Event()

            async def status(*args, **kwargs):
                entered.set()
                return {"status": "pending", "job_transfers_pending": True}

            self.client.get_file_status_async = status
            task = asyncio.create_task(download.download_file_async(self.secret, wait_timeout=30))
            await asyncio.wait_for(entered.wait(), timeout=3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)

        asyncio.run(check())
        self.mocks[2].assert_not_called()

    def test_async_ready_preserves_download_options(self):
        self.client.get_file_status_async = AsyncMock(return_value={"status": "ready", "job_transfers_pending": False})
        result = asyncio.run(download.download_file_async(self.secret, str(self.target), 90, False, job_id="producer", wait_timeout=30))
        self.assertEqual(result, self.target)
        self.mocks[2].assert_called_once_with(self.secret.removeprefix("magnus-secret:"), str(self.target), 90, False)


class FileSecretCompatibilityTests(unittest.TestCase):
    def test_relay_and_original_secret_formats_are_accepted(self):
        source = SOURCE / "__init__.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        definition = next(n for n in tree.body if getattr(n, "name", None) == "FileSecret")
        namespace = {"re": re}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)
        secret_type = namespace["FileSecret"]
        for token in ("7919-calm-boat-fire", "relay-" + "a" * 32):
            self.assertEqual(secret_type(token), "magnus-secret:" + token)
        for token in ("relay-" + "a" * 31, "relay-" + "A" * 32, "relay-../outside", "7918-calm-boat-fire"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                secret_type(token)


class ReceiveCommandTests(unittest.TestCase):
    def test_wait_and_transfer_timeout_are_forwarded_separately(self):
        app = typer.Typer()
        app.callback()(lambda: None)
        namespace = {
            "__name__": f"{PACKAGE}.cli.commands", "__package__": f"{PACKAGE}.cli",
            "app": app, "typer": typer, "Optional": Optional,
            "SignalSafeSpinner": lambda text: nullcontext(),
            "print_msg": Mock(), "print_error": Mock(), "rich_escape": str,
            "MagnusError": errors.MagnusError,
        }
        source = SOURCE / "cli" / "commands.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(n for n in tree.body if getattr(n, "name", None) == "receive_cmd")
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        with patch.object(download, "download_file", return_value=Path("result")) as receive:
            result = CliRunner().invoke(app, ["receive", "test", "--job", "producer", "--wait", "600", "--timeout", "300", "-o", "result"])
            self.assertEqual(result.exit_code, 0, result.output)
            receive.assert_called_once_with("test", target_path="result", timeout=300, job_id="producer", wait_timeout=600)


if __name__ == "__main__":
    unittest.main()
