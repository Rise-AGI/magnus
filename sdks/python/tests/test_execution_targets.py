"""Opt-in target routing and the original positional SDK contract."""
import asyncio
import importlib
import inspect
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import httpx


PACKAGE = "_magnus_target_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / "src/magnus")]
sys.modules[PACKAGE] = package
client_module = importlib.import_module(f"{PACKAGE}.client")
public_spec = importlib.util.spec_from_file_location(
    PACKAGE,
    Path(package.__path__[0]) / "__init__.py",
)
public_module = importlib.util.module_from_spec(public_spec)
with patch.object(
    client_module,
    "_get_current_site",
    return_value={},
), patch.dict(
    os.environ,
    {"MAGNUS_ADDRESS": "https://example.invalid", "MAGNUS_TOKEN": "test"},
):
    public_spec.loader.exec_module(public_module)


class ExecutionTargetTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.supported = True
        self.client = client_module.MagnusClient.__new__(client_module.MagnusClient)
        self.client.address = "https://example.invalid"
        self.client._client = httpx.Client(base_url="https://example.invalid/api", transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.client._client.close)

    def handle(self, request):
        self.requests.append(request)
        if request.url.path.endswith("capabilities"):
            return httpx.Response(200, json={"execution_targets": self.supported, "dockerfile": True, "image_build_enabled": True})
        if request.url.path.endswith("/submit"):
            return httpx.Response(200, json={"id": "submitted"})
        if request.url.path.endswith("/targets"):
            return httpx.Response(200, json=[{"id": "first"}, {"id": "second"}])
        return httpx.Response(200, json={"resources": {}})

    def test_old_positional_arguments_and_no_target_never_add_a_capability_request(self):
        result = self.client.submit_job(
            "task", "true", "repo", "branch", "revision", "cpu", 0, "owner", "A2",
            "description", "docker://image", 2, "4G", 2, "10G", "runner", "", 7.0,
        )
        self.assertEqual(result, "submitted")
        self.assertEqual(len(self.requests), 1)
        payload = json.loads(self.requests[0].content)
        self.assertNotIn("execution_target", payload)
        self.assertEqual(payload["cpu_count"], 2)
        self.assertEqual(payload["time_limit"], 2)
        self.assertEqual(payload["ephemeral_storage"], "10G")
        self.assertNotIn("node_count", payload)
        self.assertNotIn("tasks_per_node", payload)
        self.assertEqual(self.requests[0].extensions["timeout"]["read"], 7.0)
        parameter = inspect.signature(self.client.submit_job).parameters["execution_target"]
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_published_positionals_reach_sync_async_and_functional_entry_points(self):
        # Positional suffix from the published 0.8.2 wheel, before MPI fields existed.
        arguments = (
            "task", "true", "repo", "branch", "revision", "cpu", 0, "owner", "A2",
            "description", "docker://image", 2, "4G", 23, "10G", "runner", "setup", 7.0,
        )
        with patch.object(
            public_module,
            "default_client",
            self.client,
        ), patch.object(
            self.client,
            "_poll_job_completion",
            return_value="finished",
        ) as poll, patch.object(
            self.client,
            "_poll_job_completion_async",
            new_callable=AsyncMock,
            return_value="finished",
        ) as async_poll:
            for entry in (self.client, public_module):
                for name in ("submit_job", "submit_job_async", "execute_job", "execute_job_async"):
                    for topology in ({}, {"node_count": 2, "tasks_per_node": 4}):
                        with self.subTest(
                            entry=type(entry).__name__,
                            method=name,
                            topology=topology,
                        ):
                            self.requests.clear()
                            poll.reset_mock()
                            async_poll.reset_mock()
                            execute = name.startswith("execute")
                            positional = arguments + (0.5, False) if execute else arguments
                            method = getattr(
                                entry,
                                name,
                            )
                            result = method(
                                *positional,
                                **topology,
                            )
                            if name.endswith("_async"):
                                result = asyncio.run(result)
                            self.assertEqual(
                                result,
                                "finished" if execute else "submitted",
                            )
                            self.assertEqual(
                                len(self.requests),
                                1,
                            )
                            payload = json.loads(self.requests[0].content)
                            expected = {
                                "memory_demand": "4G",
                                "time_limit": 23,
                                "ephemeral_storage": "10G",
                                "runner": "runner",
                                "system_entry_command": "setup",
                                "node_count": topology.get("node_count"),
                                "tasks_per_node": topology.get("tasks_per_node"),
                            }
                            for field, value in expected.items():
                                self.assertEqual(
                                    payload.get(field),
                                    value,
                                    field,
                                )
                            if execute:
                                observer = async_poll if name.endswith("_async") else poll
                                observer.assert_called_once_with(
                                    "submitted",
                                    7.0,
                                    0.5,
                                    False,
                                )
                            else:
                                self.assertEqual(
                                    self.requests[0].extensions["timeout"]["read"],
                                    7.0,
                                )

    def test_new_submission_fields_are_keyword_only(self):
        for entry in (self.client, public_module):
            for name in ("submit_job", "submit_job_async", "execute_job", "execute_job_async"):
                method = getattr(
                    entry,
                    name,
                )
                signature = inspect.signature(method)
                for field in ("node_count", "tasks_per_node", "dockerfile", "execution_target"):
                    with self.subTest(
                        method=name,
                        field=field,
                    ):
                        self.assertEqual(
                            signature.parameters[field].kind,
                            inspect.Parameter.KEYWORD_ONLY,
                        )

    def test_target_and_dockerfile_share_one_capability_check(self):
        self.client.submit_job("task", "true", execution_target="second", dockerfile="FROM scratch")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(json.loads(self.requests[-1].content)["execution_target"], "second")

    def test_old_server_cannot_silently_submit_to_the_wrong_cluster(self):
        self.supported = False
        with self.assertRaises(client_module.MagnusError):
            self.client.submit_job("task", "true", execution_target="second")
        self.assertEqual([request.method for request in self.requests], ["GET"])

    def test_async_submission_and_cluster_query_preserve_target(self):
        asyncio.run(self.client.submit_job_async("task", "true", execution_target="second"))
        self.assertEqual(json.loads(self.requests[-1].content)["execution_target"], "second")
        asyncio.run(self.client.get_cluster_stats_async(execution_target="second"))
        self.assertEqual(self.requests[-1].url.params["execution_target"], "second")
        self.assertEqual([item["id"] for item in self.client.get_execution_targets()], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
