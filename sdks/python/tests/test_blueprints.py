"""Submission retries and actions using isolated HTTP and child processes."""

import ast
import asyncio
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from test_execution_targets import client_module, public_module

actions = importlib.import_module(
    f"{client_module.__package__}.actions",
)
errors = importlib.import_module(
    f"{client_module.__package__}.exceptions",
)


class BlueprintClientTests(unittest.TestCase):
    def setUp(
        self,
    ):
        self.client = client_module.MagnusClient.__new__(
            client_module.MagnusClient,
        )
        self.client.address = "https://producer.invalid"
        self.client.token = "synthetic-token"
        self.requests = []

    def transport(
        self,
        handler,
    ):
        def handle(
            request,
        ):
            self.requests.append(
                request,
            )
            return handler(
                request,
            )

        self.client._client = httpx.Client(
            base_url="https://producer.invalid/api",
            transport=httpx.MockTransport(
                handle,
            ),
        )
        self.addCleanup(
            self.client._client.close,
        )

    def test_lost_reply_retries_same_payload_and_uploads_only_once(
        self,
    ):
        posts = []
        secret = "magnus-secret:7919-calm-boat-fire"

        def handler(
            request,
        ):
            if request.url.path.endswith(
                "schema",
            ):
                return httpx.Response(
                    200,
                    json=[{"key": "inputs", "type": "file_secret", "is_list": True}],
                    headers={
                        "X-Magnus-Blueprint-Revision": "a" * 64,
                        "X-Magnus-Submission-Idempotency": "1",
                    },
                )
            posts.append(
                request,
            )
            if (
                len(
                    posts,
                )
                == 1
            ):
                raise httpx.ReadTimeout(
                    "reply lost",
                )
            return httpx.Response(
                200,
                json={"job_id": "accepted"},
            )

        self.transport(
            handler,
        )
        self.client._upload_file = Mock(
            return_value=secret,
        )
        with patch.object(
            client_module.time,
            "sleep",
        ):
            result = self.client.launch_blueprint(
                "sample",
                {"inputs": [None, "input.dat", secret]},
            )
        self.assertEqual(
            result,
            "accepted",
        )
        self.client._upload_file.assert_called_once_with(
            "input.dat",
            60,
            1,
        )
        self.assertEqual(
            posts[0].content,
            posts[1].content,
        )
        self.assertEqual(
            posts[0].headers["Idempotency-Key"],
            posts[1].headers["Idempotency-Key"],
        )
        body = json.loads(
            posts[1].content,
        )
        self.assertEqual(
            body["parameters"]["inputs"],
            [None, secret, secret],
        )
        self.assertEqual(
            body["blueprint_revision"],
            "a" * 64,
        )

    def test_old_server_uncertain_submission_is_not_retried(
        self,
    ):
        def handler(
            request,
        ):
            if request.url.path.endswith(
                "schema",
            ):
                return httpx.Response(
                    200,
                    json=[],
                )
            raise httpx.ReadTimeout(
                "uncertain",
            )

        self.transport(
            handler,
        )
        with self.assertRaises(
            errors.SubmissionOutcomeUnknown,
        ) as caught:
            self.client.launch_blueprint(
                "sample",
                idempotency_key="stable-key",
            )
        self.assertEqual(
            caught.exception.idempotency_key,
            "stable-key",
        )
        self.assertEqual(
            len(
                self.requests,
            ),
            2,
        )

    def test_job_retry_discovers_capability_only_after_uncertain_reply(
        self,
    ):
        count = [0]

        def handler(
            request,
        ):
            if request.url.path.endswith(
                "capabilities",
            ):
                return httpx.Response(
                    200,
                    json={"submission_idempotency": True},
                )
            count[0] += 1
            return httpx.Response(
                502 if count[0] == 1 else 200,
                json={"id": "accepted"},
            )

        self.transport(
            handler,
        )
        with patch.object(
            client_module.time,
            "sleep",
        ):
            self.assertEqual(
                self.client.submit_job(
                    "task",
                    "true",
                    idempotency_key="stable",
                ),
                "accepted",
            )
        self.assertEqual(
            [r.url.path for r in self.requests],
            ["/api/jobs/submit", "/api/jobs/capabilities", "/api/jobs/submit"],
        )
        self.assertEqual(
            self.requests[0].content,
            self.requests[2].content,
        )

    def test_error_after_ambiguous_reply_keeps_uncertain_outcome(
        self,
    ):
        count = [0]

        def handler(
            request,
        ):
            if request.url.path.endswith(
                "capabilities",
            ):
                return httpx.Response(
                    200,
                    json={"submission_idempotency": True},
                )
            count[0] += 1
            return httpx.Response(
                502 if count[0] == 1 else 400,
                json={"detail": "later precheck failed"},
            )

        self.transport(
            handler,
        )
        with (
            patch.object(
                client_module.time,
                "sleep",
            ),
            self.assertRaises(
                errors.SubmissionOutcomeUnknown,
            ) as caught,
        ):
            self.client.submit_job(
                "task",
                "true",
                idempotency_key="stable",
            )
        self.assertEqual(
            caught.exception.idempotency_key,
            "stable",
        )

    def test_client_errors_do_not_retry_and_lookup_has_explicit_identity(
        self,
    ):
        self.transport(
            lambda request: httpx.Response(
                409,
                json={"detail": "conflict"},
            ),
        )
        with self.assertRaises(
            errors.ConflictError,
        ):
            self.client.submit_job(
                "task",
                "true",
            )
        self.assertEqual(
            len(
                self.requests,
            ),
            1,
        )
        with self.assertRaises(
            errors.ConflictError,
        ):
            self.client.get_accepted_submission(
                "stable",
            )
        self.assertEqual(
            self.requests[-1].url.path,
            "/api/jobs/submissions/stable",
        )

    def test_rewrite_preserves_aliases_typing_unicode_and_adjacent_statements(
        self,
    ):
        source = """from typing import Annotated as A
label = "中文"; from magnus import submit_job as submit; untouched = 7
from magnus import (FileSecret as Secret, JobType)
def blueprint(x: A[int, {"min": 1}] = 2):
    submit(task_name=label, entry_command=str(untouched + x))
"""
        code = client_module.strip_imports(
            source,
        )
        namespace = {"submit_job": Mock(), "FileSecret": str, "JobType": str}
        exec(
            code,
            namespace,
        )
        namespace["blueprint"]()
        namespace["submit_job"].assert_called_once_with(
            task_name="中文",
            entry_command="9",
        )
        self.assertIs(
            namespace["Secret"],
            str,
        )
        self.assertIn(
            "import os",
            client_module.strip_imports(
                "import os\ndef blueprint(): pass",
            ),
        )

    def test_cancelled_async_submission_joins_and_preserves_identity(
        self,
    ):
        async def scenario(
            method,
            outcome,
        ):
            entered = asyncio.Event()
            release = threading.Event()
            loop = asyncio.get_running_loop()
            seen = []

            def accept(
                *args,
                **kwargs,
            ):
                seen.append(
                    kwargs["idempotency_key"],
                )
                loop.call_soon_threadsafe(
                    entered.set,
                )
                if not release.wait(
                    3,
                ):
                    raise AssertionError(
                        "submission was abandoned",
                    )
                if outcome == "unknown":
                    raise errors.SubmissionOutcomeUnknown(
                        kwargs["idempotency_key"],
                    )
                return "accepted-job"

            sync_name = "launch_blueprint" if method == "launch_blueprint_async" else "submit_job"
            arguments = ("sample",) if method == "launch_blueprint_async" else ("task", "true")
            with patch.object(
                self.client,
                sync_name,
                side_effect=accept,
            ):
                task = asyncio.create_task(
                    getattr(
                        self.client,
                        method,
                    )(
                        *arguments,
                    ),
                )
                await entered.wait()
                task.cancel()
                await asyncio.sleep(
                    0.01,
                )
                self.assertFalse(
                    task.done(),
                )
                task.cancel()
                release.set()
                with self.assertRaises(
                    asyncio.CancelledError,
                ):
                    await task
            self.assertEqual(
                self.client.last_submission_key,
                seen[0],
            )
            self.assertEqual(
                self.client.last_job_id,
                "accepted-job" if outcome == "accepted" else None,
            )
            if outcome == "accepted":
                self.assertEqual(
                    self.client.last_job_id,
                    "accepted-job",
                )

        for method in ("launch_blueprint_async", "submit_job_async"):
            for outcome in ("accepted", "unknown"):
                with self.subTest(
                    method=method,
                    outcome=outcome,
                ):
                    asyncio.run(
                        scenario(
                            method,
                            outcome,
                        ),
                    )

    def test_original_blueprint_positionals_and_new_keyword_wrappers(
        self,
    ):
        with (
            patch.object(
                public_module,
                "default_client",
                self.client,
            ),
            patch.object(
                self.client,
                "launch_blueprint",
                return_value="job",
            ) as launch,
        ):
            public_module.launch_blueprint(
                "sample",
                {},
                True,
                False,
                30,
                2,
                7.0,
                idempotency_key="stable",
            )
        self.assertEqual(
            launch.call_args.kwargs["timeout"],
            7.0,
        )
        self.assertEqual(
            launch.call_args.kwargs["idempotency_key"],
            "stable",
        )
        self.assertEqual(
            public_module.FileSecret(
                "7919-calm-boat-fire",
            ).token,
            "7919-calm-boat-fire",
        )


class ActionTests(unittest.TestCase):
    def setUp(
        self,
    ):
        self.client = SimpleNamespace(
            address="https://producer.invalid",
            token="synthetic-token",
        )

    def test_context_and_receipt_wait_do_not_mutate_default_profile(
        self,
    ):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with (
            patch.object(
                actions.subprocess,
                "Popen",
                return_value=process,
            ) as spawn,
            patch.dict(
                os.environ,
                {"MAGNUS_ADDRESS": "https://other.invalid"},
            ),
        ):
            actions.execute_action(
                "magnus receive -- secret",
                client=self.client,
                job_id="producer",
            )
            self.assertEqual(
                os.environ["MAGNUS_ADDRESS"],
                "https://other.invalid",
            )
        command = spawn.call_args.args[0]
        self.assertEqual(
            command,
            ["magnus", "receive", "--job", "producer", "--wait", "600", "--", "secret"],
        )
        self.assertEqual(
            spawn.call_args.kwargs["env"]["MAGNUS_ADDRESS"],
            self.client.address,
        )
        self.assertEqual(
            spawn.call_args.kwargs["env"]["MAGNUS_TOKEN"],
            self.client.token,
        )
        self.assertNotIn(
            self.client.token,
            command,
        )

    def test_explicit_wait_and_job_preserved_and_all_commands_validated_before_side_effects(
        self,
    ):
        command = "magnus receive secret --job=another --wait 12"
        self.assertEqual(
            actions._commands(
                command,
                "producer",
            )[0],
            command.split(),
        )
        with (
            patch.object(
                actions.subprocess,
                "Popen",
            ) as spawn,
            self.assertRaises(
                errors.ExecutionError,
            ),
        ):
            actions.execute_action(
                "magnus print okay\nrm -rf x",
            )
        spawn.assert_not_called()

    def test_success_poll_action_uses_producing_client(
        self,
    ):
        instance = client_module.MagnusClient.__new__(
            client_module.MagnusClient,
        )
        instance._poll_once = Mock(
            return_value={
                "status": "Success",
                "action": "magnus receive secret",
                "result": "done",
            },
        )
        with patch.object(
            client_module,
            "execute_action",
        ) as action:
            self.assertEqual(
                instance._poll_job_completion(
                    "producer",
                ),
                "done",
            )
        self.assertIs(
            action.call_args.kwargs["client"],
            instance,
        )
        self.assertEqual(
            action.call_args.kwargs["job_id"],
            "producer",
        )

    def test_manual_cli_action_uses_job_context(
        self,
    ):
        path = (
            Path(
                __file__,
            ).parents[1]
            / "src/magnus/cli/_jobs.py"
        )
        node = next(
            n
            for n in ast.parse(
                path.read_text(
                    encoding="utf-8",
                ),
            ).body
            if isinstance(
                n,
                ast.FunctionDef,
            )
            and n.name == "_do_job_action"
        )
        package = sys.modules[client_module.__package__]
        action = Mock()
        bindings = {
            "__package__": client_module.__package__ + ".cli",
            "_resolve_job_ref": lambda ref: "resolved-job",
            "api_get_job_action": lambda job: "magnus receive secret",
            "print_msg": Mock(),
            "print_error": Mock(),
            "run_action": action,
            "ExecutionError": errors.ExecutionError,
            "MagnusError": errors.MagnusError,
        }
        with patch.object(
            package,
            "default_client",
            self.client,
            create=True,
        ):
            exec(
                compile(
                    ast.Module(
                        body=[node],
                        type_ignores=[],
                    ),
                    str(
                        path,
                    ),
                    "exec",
                ),
                bindings,
            )
            bindings["_do_job_action"](
                "-1",
                execute=True,
            )
        self.assertIs(
            action.call_args.kwargs["client"],
            self.client,
        )
        self.assertEqual(
            action.call_args.kwargs["job_id"],
            "resolved-job",
        )

    def test_async_cancellation_reaps_real_child_and_keeps_event_loop_live(
        self,
    ):
        async def scenario():
            spawned = []
            started = asyncio.Event()
            real_spawn = asyncio.create_subprocess_exec
            real_stop = actions._stop_async

            async def spawn(
                *args,
                **kwargs,
            ):
                process = await real_spawn(
                    sys.executable,
                    "-c",
                    "import time; time.sleep(60)",
                    **kwargs,
                )
                spawned.append(
                    process,
                )
                started.set()
                return process

            async def delayed_stop(
                process,
            ):
                await asyncio.sleep(
                    0.03,
                )
                await real_stop(
                    process,
                )

            with (
                patch.object(
                    actions.asyncio,
                    "create_subprocess_exec",
                    side_effect=spawn,
                ),
                patch.object(
                    actions,
                    "_stop_async",
                    side_effect=delayed_stop,
                ),
            ):
                task = asyncio.create_task(
                    actions.execute_action_async(
                        "magnus print waiting",
                        client=self.client,
                    ),
                )
                await started.wait()
                task.cancel()
                await asyncio.sleep(
                    0.01,
                )
                task.cancel()
                with self.assertRaises(
                    asyncio.CancelledError,
                ):
                    await task
            self.assertIsNotNone(
                spawned[0].returncode,
            )

        asyncio.run(
            scenario(),
        )

    def test_async_success_does_not_call_sync_action(
        self,
    ):
        async def scenario():
            instance = client_module.MagnusClient.__new__(
                client_module.MagnusClient,
            )

            async def poll(
                job_id,
            ):
                return {
                    "status": "Success",
                    "action": "magnus print done",
                    "result": "done",
                }

            instance._poll_once_async = poll

            async def action(
                *args,
                **kwargs,
            ):
                await asyncio.sleep(
                    0,
                )

            with (
                patch.object(
                    client_module,
                    "execute_action",
                    side_effect=AssertionError(
                        "blocking action",
                    ),
                ),
                patch.object(
                    client_module,
                    "execute_action_async",
                    side_effect=action,
                ) as called,
            ):
                self.assertEqual(
                    await instance._poll_job_completion_async(
                        "producer",
                    ),
                    "done",
                )
                self.assertEqual(
                    called.call_args.kwargs["job_id"],
                    "producer",
                )

        asyncio.run(
            scenario(),
        )


class LocalRuntimeTests(unittest.TestCase):
    def test_runtime_prepared_once_per_dockerfile_and_legacy_checkout_kept(
        self,
    ):
        path = (
            Path(
                __file__,
            ).parents[1]
            / "src/magnus/cli/_local.py"
        )
        tree = ast.parse(
            path.read_text(
                encoding="utf-8",
            ),
        )
        helper = next(
            n
            for n in tree.body
            if isinstance(
                n,
                ast.FunctionDef,
            )
            and n.name == "_prepare_blueprint_runtime"
        )
        import hashlib
        import subprocess

        namespace = {
            "Path": Path,
            "hashlib": hashlib,
            "subprocess": subprocess,
            "print_msg": Mock(),
            "LOCAL_OPERATION_TIMEOUT": 600,
        }
        exec(
            compile(
                ast.Module(
                    body=[helper],
                    type_ignores=[],
                ),
                str(
                    path,
                ),
                "exec",
            ),
            namespace,
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(
                folder,
            )
            self.assertFalse(
                namespace[helper.name](
                    root,
                ),
            )
            dockerfile = root / "back_end/containers/blueprint-runtime.Dockerfile"
            dockerfile.parent.mkdir(
                parents=True,
            )
            dockerfile.write_bytes(
                b"FROM python:3.14-slim\n",
            )
            digest = hashlib.sha256(
                dockerfile.read_bytes(),
            ).hexdigest()
            with patch.object(
                subprocess,
                "run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout=digest,
                ),
            ) as run:
                self.assertTrue(
                    namespace[helper.name](
                        root,
                    ),
                )
                self.assertEqual(
                    run.call_count,
                    1,
                )
            with patch.object(
                subprocess,
                "run",
                return_value=SimpleNamespace(
                    returncode=1,
                    stdout="",
                ),
            ) as run:
                self.assertTrue(
                    namespace[helper.name](
                        root,
                    ),
                )
                self.assertEqual(
                    run.call_count,
                    2,
                )
                self.assertIn(
                    "build",
                    run.call_args.args[0],
                )
                self.assertTrue(
                    run.call_args.kwargs["check"],
                )


if __name__ == "__main__":
    unittest.main()
