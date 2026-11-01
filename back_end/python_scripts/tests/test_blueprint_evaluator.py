"""Pure protocol regressions; hostile code only runs in the opt-in OS sandbox."""

import ast
import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from ruamel.yaml import YAML
from test_job_resources import load_schemas

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[3]
)
SERVER = ROOT / "back_end/server"
for name, path in (
    ("blueprint_test_server", SERVER),
    ("blueprint_test_server._blueprint_manager", SERVER / "_blueprint_manager"),
):
    package = ModuleType(
        name,
    )
    package.__path__ = [
        str(
            path,
        )
    ]
    sys.modules.setdefault(
        name,
        package,
    )
ENGINE = importlib.import_module(
    "blueprint_test_server._blueprint_manager._engine",
)
RUNNER = importlib.import_module(
    "blueprint_test_server._blueprint_manager._runner",
)
REQUESTS = importlib.import_module(
    "blueprint_test_server._blueprint_requests",
)
TYPES = {"A1": "A1", "A2": "A2", "B1": "B1", "B2": "B2"}


class BlueprintProtocolTests(unittest.TestCase):
    def test_late_create_is_reaped_even_after_admission_was_blocked(
        self,
    ):
        runtime = RUNNER.BlueprintRuntime(
            RUNNER.RuntimeSettings(
                backend="docker",
            ),
        )
        runtime._blocked.set()
        runtime._containers.add(
            "active",
        )
        listing = SimpleNamespace(
            returncode=0,
            stdout=b"late-created\nactive\n",
        )
        with (
            patch.object(
                RUNNER.subprocess,
                "run",
                return_value=listing,
            ),
            patch.object(
                runtime,
                "_remove_container",
            ) as remove,
        ):
            runtime.cleanup_orphans()
        remove.assert_called_once_with(
            "late-created",
        )
        self.assertTrue(
            runtime._blocked.is_set(),
        )

    def test_file_secret_subclasses_keep_their_protocol(
        self,
    ):
        evaluator = self.evaluator(
            """
Data = type("Data", (FileSecret,), {})
def blueprint(file: Data):
    submit_job(entry_command=file.token)
""",
        )
        self.assertEqual(
            evaluator.schema()[0]["type"],
            "file_secret",
        )
        self.assertEqual(
            evaluator.execute(
                {"file": "magnus-secret:7919-calm-boat-fire"},
            )["entry_command"],
            "7919-calm-boat-fire",
        )

    def test_parent_request_limits_precede_json_encoding(
        self,
    ):
        runtime = RUNNER.BlueprintRuntime(
            RUNNER.RuntimeSettings(
                max_input_bytes=64,
            ),
        )
        nested = []
        for _ in range(
            150,
        ):
            nested = [nested]
        for request in ({"code": "x" * 65}, {"parameters": nested}):
            with patch.object(
                RUNNER.json,
                "dumps",
                side_effect=AssertionError(
                    "encoded too early",
                ),
            ):
                with self.assertRaises(
                    ValueError,
                ):
                    runtime.evaluate(
                        request,
                    )

    def test_failed_cli_kill_still_removes_container_and_blocks_reuse(
        self,
    ):
        runtime = RUNNER.BlueprintRuntime(
            RUNNER.RuntimeSettings(
                backend="docker",
            ),
        )
        process = Mock()
        process.poll.return_value = None
        with (
            patch.object(
                runtime,
                "_docker_command",
                return_value=["docker"],
            ),
            patch.object(
                runtime,
                "_claim_container",
            ),
            patch.object(
                RUNNER.subprocess,
                "Popen",
                return_value=process,
            ),
            patch.object(
                runtime,
                "_exchange",
                side_effect=ValueError(
                    "failed read",
                ),
            ),
            patch.object(
                runtime,
                "_kill",
                side_effect=subprocess.TimeoutExpired(
                    "docker",
                    5,
                ),
            ),
            patch.object(
                runtime,
                "_remove_container",
            ) as remove,
        ):
            with self.assertRaises(
                RUNNER.BlueprintUnavailable,
            ):
                runtime.evaluate(
                    {},
                )
        remove.assert_called_once()
        self.assertTrue(
            runtime._blocked.is_set(),
        )
        with self.assertRaisesRegex(
            RUNNER.BlueprintUnavailable,
            "cleanup",
        ):
            runtime.evaluate(
                {},
            )

    def test_controller_rejects_misspelled_job_fields(
        self,
    ):
        namespace = load_schemas()
        namespace.update(
            BlueprintRuntime=RUNNER.BlueprintRuntime,
            RuntimeSettings=RUNNER.RuntimeSettings,
        )
        path = SERVER / "_blueprint_manager/_manager.py"
        tree = ast.parse(
            path.read_text(),
        )
        tree.body = [
            node
            for node in tree.body
            if not isinstance(
                node,
                ast.ImportFrom,
            )
            or not node.level
        ]
        exec(
            compile(
                tree,
                str(
                    path,
                ),
                "exec",
            ),
            namespace,
        )
        manager = namespace["BlueprintManager"]()
        payload = {
            "task_name": "example",
            "entry_command": "true",
            "execution_targte": "remote",
        }
        with patch.object(
            manager.runtime,
            "evaluate",
            return_value={"job": payload},
        ):
            with self.assertRaisesRegex(
                ValueError,
                "execution_targte",
            ):
                manager.execute(
                    "unused",
                    {},
                )
        payload.pop(
            "execution_targte",
        )
        payload["dockerfile"] = "FROM python:3.14-slim"
        with patch.object(
            manager.runtime,
            "evaluate",
            return_value={"job": payload},
        ):
            job = manager.execute(
                "unused",
                {},
            )
        self.assertEqual(
            job.repo_name,
            "magnus",
        )
        self.assertEqual(
            job.dockerfile,
            payload["dockerfile"],
        )

    def evaluator(
        self,
        code,
    ):
        return ENGINE.BlueprintEvaluator(
            code,
            TYPES,
        )

    def test_bundled_blueprints_and_typed_files(
        self,
    ):
        paths = sorted(
            (ROOT / "sdks/python/src/magnus/bundled/blueprints").glob(
                "*.yaml",
            ),
        )
        self.assertTrue(
            paths,
        )
        for path in paths:
            with self.subTest(
                path=path.name,
            ):
                evaluator = self.evaluator(
                    YAML(
                        typ="safe",
                    ).load(
                        path.read_text(),
                    )["code"],
                )
                json.dumps(
                    evaluator.schema(),
                    allow_nan=False,
                )
        evaluator = self.evaluator(
            """
from typing import Annotated as A, Optional, List, Literal
from magnus import FileSecret, submit_job as submit, JobType
def blueprint(files: List[Optional[FileSecret]], n: Literal[1, 2], x: A[int, {"min": 1, "max": 4}] = 2):
    submit(entry_command=str([f.token if f else None for f in files]), num_tasks=n, cpu_count=x, job_type=JobType.A2)
""",
        )
        job = evaluator.execute(
            {"files": [None, "magnus-secret:7919-calm-boat-fire"], "n": "2"},
        )
        self.assertEqual(
            job["entry_command"],
            "[None, '7919-calm-boat-fire']",
        )
        self.assertEqual(
            job["num_tasks"],
            2,
        )
        self.assertEqual(
            job["job_type"],
            "A2",
        )

    def test_invalid_signatures_fail_before_run(
        self,
    ):
        for code in (
            "async def blueprint(): pass",
            "def blueprint(x, /): pass",
            "def blueprint(*args): pass",
            "def blueprint(**kwargs): pass",
            "def blueprint(): yield 1",
            "def blueprint(x: dict): pass",
            "def blueprint(x: Annotated[int, {'min': 3}] = 1): pass",
        ):
            with (
                self.subTest(
                    code=code,
                ),
                self.assertRaises(
                    ValueError,
                ),
            ):
                self.evaluator(
                    code,
                )

    def test_constraints_are_enforced_without_the_ui(
        self,
    ):
        evaluator = self.evaluator(
            """
def blueprint(x: Annotated[float, {"min": 1, "max": 3}], text: Annotated[str, {"allow_empty": False}], file: FileSecret):
    submit_job(entry_command=text)
""",
        )
        valid = {"x": 2, "text": "hello", "file": "magnus-secret:7919-calm-boat-fire"}
        self.assertEqual(
            evaluator.execute(
                valid,
            )["entry_command"],
            "hello",
        )
        for field, value in (
            ("x", 0),
            ("x", 4),
            (
                "x",
                float(
                    "inf",
                ),
            ),
            ("text", " "),
            ("file", "magnus-secret:"),
        ):
            with (
                self.subTest(
                    field=field,
                    value=value,
                ),
                self.assertRaises(
                    ValueError,
                ),
            ):
                evaluator.execute(
                    {**valid, field: value},
                )
        with self.assertRaisesRegex(
            ValueError,
            "Unknown parameter",
        ):
            evaluator.execute(
                {**valid, "misspelled": 2},
            )

    def test_admission_is_bounded_without_a_wait_queue(
        self,
    ):
        runtime = RUNNER.BlueprintRuntime(
            RUNNER.RuntimeSettings(
                max_concurrent=1,
            ),
        )
        runtime._slots.acquire()
        try:
            with self.assertRaisesRegex(
                RUNNER.BlueprintUnavailable,
                "busy",
            ):
                runtime.evaluate(
                    {},
                )
        finally:
            runtime._slots.release()

    def test_bubblewrap_requires_user_namespace(
        self,
    ):
        runtime = RUNNER.BlueprintRuntime()
        with patch.object(
            RUNNER.shutil,
            "which",
            return_value="/usr/bin/bwrap",
        ):
            command = runtime._bubblewrap_command()
        for option in (
            "--unshare-user",
            "--unshare-all",
            "--clearenv",
            "--die-with-parent",
            "--new-session",
        ):
            self.assertIn(
                option,
                command,
            )
        sources = [
            command[i + 1]
            for i, value in enumerate(
                command,
            )
            if value == "--ro-bind"
        ]
        self.assertNotIn(
            str(
                Path.home(),
            ),
            sources,
        )
        self.assertNotIn(
            str(
                ROOT,
            ),
            sources,
        )


@unittest.skipUnless(
    os.environ.get(
        "MAGNUS_TEST_BLUEPRINT_ISOLATION",
    )
    == "1",
    "requires the prepared Docker evaluator image",
)
class BlueprintIsolationTests(unittest.TestCase):
    def test_late_container_replies_are_reaped_without_killing_concurrent_workers(
        self,
    ):
        runtime = RUNNER.BlueprintRuntime(
            RUNNER.RuntimeSettings(
                backend="docker",
            ),
        )
        name = "magnus-blueprint-test-orphan-" + str(
            os.getpid(),
        )
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    name,
                    "--network=none",
                    "--read-only",
                    "--label",
                    runtime._docker_owner,
                    runtime.settings.docker_image,
                    "python",
                    "-c",
                    "import time; time.sleep(60)",
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )
            request = {
                "code": 'def blueprint(): submit_job(entry_command="parallel")',
                "operation": "execute",
                "parameters": {},
                "job_types": TYPES,
            }
            with ThreadPoolExecutor(
                max_workers=4,
            ) as workers:
                results = list(
                    workers.map(
                        runtime.evaluate,
                        [request] * 4,
                    ),
                )
            self.assertTrue(
                all(result["job"]["entry_command"] == "parallel" for result in results),
            )
            inspection = subprocess.run(
                ["docker", "inspect", name],
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(
                inspection.returncode,
                0,
            )
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name],
                capture_output=True,
                timeout=10,
            )

    def run_code(
        self,
        code,
        **settings,
    ):
        runtime = RUNNER.BlueprintRuntime(
            RUNNER.RuntimeSettings(
                backend="docker",
                **settings,
            ),
        )
        return runtime.evaluate(
            {
                "code": code,
                "operation": "execute",
                "parameters": {},
                "job_types": TYPES,
            },
        )

    def test_normal_blueprint_and_independent_followup(
        self,
    ):
        self.assertEqual(
            self.run_code(
                'def blueprint(): submit_job(entry_command="hello")',
            )["job"]["entry_command"],
            "hello",
        )

    def test_reflection_does_not_expose_controller_files_or_environment(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            canary = (
                Path(
                    directory,
                )
                / "secret"
            )
            canary.write_text(
                "controller-private-canary",
            )
            code = f"""
b = submit_job.__globals__["__builtins__"]
os = b["__import__"]("os")
def blueprint():
    if os.path.exists({str(canary)!r}):
        raise ValueError("host file leaked")
    if os.environ.get("BLUEPRINT_TEST_CANARY"):
        raise ValueError("environment leaked")
    try:
        b["open"]("/tmp/forbidden", "w")
    except Exception:
        pass
    else:
        raise ValueError("writable root")
    submit_job(entry_command="isolated")
"""
            with patch.dict(
                os.environ,
                {"BLUEPRINT_TEST_CANARY": "private"},
            ):
                self.assertEqual(
                    self.run_code(
                        code,
                    )["job"]["entry_command"],
                    "isolated",
                )
            self.assertEqual(
                canary.read_text(),
                "controller-private-canary",
            )

    def test_kernel_rejects_process_ipc_network_and_deadline_changes(
        self,
    ):
        code = """
b = submit_job.__globals__["__builtins__"]
os = b["__import__"]("os")
signal = b["__import__"]("signal")
ctypes = b["__import__"]("ctypes")
libc = ctypes.CDLL(None, use_errno=True)
def blueprint():
    for operation in (
        lambda: os.fork(),
        lambda: os.memfd_create("forbidden"),
        lambda: signal.signal(signal.SIGALRM, signal.SIG_IGN),
        lambda: signal.setitimer(signal.ITIMER_REAL, 0),
        lambda: signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM]),
    ):
        try:
            operation()
        except Exception:
            pass
        else:
            raise ValueError("restricted operation succeeded")
    for result in (libc.shmget(0, 1048576, 1536), libc.socket(2, 1, 0)):
        if result != -1 or ctypes.get_errno() != 1:
            raise ValueError("IPC/network policy failed")
    submit_job(entry_command="restricted")
"""
        self.assertEqual(
            self.run_code(
                code,
            )["job"]["entry_command"],
            "restricted",
        )

    def test_deadlines_memory_and_output_are_bounded(
        self,
    ):
        for code, options in (
            ("while True: pass", {"timeout_seconds": 0.5}),
            (
                'b = submit_job.__globals__["__builtins__"]; b["__import__"]("time").sleep(120)',
                {"timeout_seconds": 0.5},
            ),
            ('value = "x" * (1024 * 1024 * 1024)', {}),
            ('while True: print("x" * 4096)', {"max_output_bytes": 16384}),
        ):
            with self.subTest(
                code=code,
            ):
                began = time.monotonic()
                with self.assertRaises(
                    ValueError,
                ):
                    self.run_code(
                        code,
                        **options,
                    )
                self.assertLess(
                    time.monotonic() - began,
                    15,
                )
        self.assertEqual(
            self.run_code(
                'def blueprint(): submit_job(entry_command="still works")',
            )["job"]["entry_command"],
            "still works",
        )


class BlueprintBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def exchange(
        self,
        chunks,
        headers=(),
    ):
        received = []
        parsed = []

        async def app(
            scope,
            receive,
            send,
        ):
            parsed.append(
                (await receive())["body"],
            )

        iterator = iter(
            chunks,
        )

        async def receive():
            return next(
                iterator,
            )

        async def send(
            message,
        ):
            received.append(
                message,
            )

        middleware = REQUESTS.BlueprintRequestLimits(
            app,
            8,
            1,
        )
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/blueprints/example/run",
                "headers": headers,
            },
            receive,
            send,
        )
        return parsed, received, middleware

    async def test_declared_or_streamed_oversize_is_rejected_before_parser(
        self,
    ):
        for chunks, headers in (
            ([], [(b"content-length", b"9999999999999")]),
            (
                [
                    {"type": "http.request", "body": b"12345", "more_body": True},
                    {"type": "http.request", "body": b"6789"},
                ],
                [],
            ),
        ):
            parsed, messages, middleware = await self.exchange(
                chunks,
                headers,
            )
            self.assertEqual(
                parsed,
                [],
            )
            self.assertEqual(
                messages[0]["status"],
                413,
            )
            self.assertTrue(
                middleware.slots.acquire(
                    blocking=False,
                ),
            )

    async def test_valid_body_and_timeout_release_admission(
        self,
    ):
        parsed, _, _ = await self.exchange(
            [{"type": "http.request", "body": b"{}"}],
        )
        self.assertEqual(
            parsed,
            [b"{}"],
        )

        async def wait():
            await asyncio.sleep(
                10,
            )

        messages = []

        async def send(
            message,
        ):
            messages.append(
                message,
            )

        middleware = REQUESTS.BlueprintRequestLimits(
            Mock(),
            8,
            1,
            timeout_seconds=0.01,
        )
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/blueprints",
                "headers": [],
            },
            wait,
            send,
        )
        self.assertEqual(
            messages[0]["status"],
            408,
        )
        self.assertTrue(
            middleware.slots.acquire(
                blocking=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
