"""Resource display regressions without application startup or a production DB."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import BaseModel
from sqlalchemy import create_engine, inspect, text


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_resources():
    namespace = {"__name__": __name__}
    for path in (SERVER / "_size_utils.py", SERVER / "_job_resources.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        exec(compile(tree, str(path), "exec"), namespace)
    return namespace


def load_schemas():
    namespace = load_resources()
    namespace["__package__"] = "resource_display_tests.schemas"
    path = SERVER / "models" / "_job.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    enums = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in {"JobType", "JobStatus"}]
    namespace["enum"] = enum
    exec(compile(ast.Module(body=enums, type_ignores=[]), str(path), "exec"), namespace)
    path = SERVER / "schemas" / "_user.py"
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    path = SERVER / "schemas" / "_job.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)
    for name in ("UserInfo", "JobSubmission", "JobResourceSummary", "JobListItem", "JobResponse", "PagedJobResponse"):
        namespace[name].model_rebuild(_types_namespace=namespace)
    return namespace


class JobResourceTests(unittest.TestCase):
    def setUp(self):
        self.module = load_resources()

    def request(
        self,
        **overrides,
    ):
        fields = dict(cpu_count=1, memory_demand="4000M", gpu_count=0, node_count=2, tasks_per_node=64, mode="slurm_per_cpu")
        fields.update(overrides)
        return self.module["requested_job_resources"](**fields)

    def test_multi_node_per_cpu_memory_is_per_task_after_normalization(self):
        summary = self.request()
        self.assertEqual(summary["cpu_count"], 128)
        self.assertEqual(summary["memory_bytes"], 500 * 1024**3)
        self.assertEqual((summary["node_count"], summary["tasks_per_node"]), (2, 64))
        self.assertEqual(summary["basis"], "request")

    def test_explicit_memory_and_gpus_are_per_node_not_per_task(self):
        summary = self.request(cpu_count=2, memory_demand="8G", gpu_count=3, mode="slurm_per_node")
        self.assertEqual((summary["cpu_count"], summary["gpu_count"]), (256, 6))
        self.assertEqual(summary["memory_bytes"], 16 * 1024**3)

    def test_local_ignores_parallel_metadata_and_is_only_a_request(self):
        summary = self.request(cpu_count=4, gpu_count=2, mode="local")
        self.assertEqual((summary["cpu_count"], summary["gpu_count"]), (4, 2))
        self.assertEqual(summary["memory_bytes"], 4000 * 1024**2)
        self.assertEqual((summary["node_count"], summary["tasks_per_node"]), (1, 1))
        self.assertEqual(summary["basis"], "request")

    def test_defaults_and_unknown_memory_remain_honest(self):
        for memory in (None, "", "0", "0M", "-1G", "nan", "inf", "broken", 123):
            with self.subTest(memory=memory):
                summary = self.request(cpu_count=None, memory_demand=memory, node_count=None, tasks_per_node=None)
                self.assertIsNone(summary["cpu_count"])
                self.assertIsNone(summary["memory_bytes"])
                self.assertEqual((summary["node_count"], summary["tasks_per_node"]), (1, 1))

    def test_unsuffixed_slurm_memory_is_mebibytes(self):
        summary = self.request(memory_demand="1024", mode="slurm_per_node")
        self.assertEqual(summary["memory_bytes"], 2 * 1024**3)

    def test_allocation_is_never_multiplied_again(self):
        summary = self.module["allocated_job_resources"](
            {"cpu_count": 128, "memory_mb": 512000, "gpu_count": 6, "node_count": 2},
            mode="slurm_per_cpu",
        )
        self.assertEqual((summary["cpu_count"], summary["gpu_count"]), (128, 6))
        self.assertEqual(summary["memory_bytes"], 500 * 1024**3)
        self.assertEqual(summary["basis"], "allocation")
        self.assertFalse(summary["mode_inferred"])
        self.assertIsNone(summary["tasks_per_node"])

    def test_unknown_allocation_does_not_invent_nodes_or_memory(self):
        summary = self.module["allocated_job_resources"]({}, mode="slurm_per_node")
        self.assertIsNone(summary["node_count"])
        self.assertIsNone(summary["memory_bytes"])
        self.assertIsNone(summary["cpu_count"])


class JobResourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_schemas()
        self.config = {"execution": {"backend": "slurm", "slurm": {"mem_mode": "per_cpu"}, "image_build": {"enabled": True}}}
        config_module = ModuleType("resource_display_tests._magnus_config")
        config_module.magnus_config = self.config
        self.modules = patch.dict("sys.modules", {config_module.__name__: config_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def fields(
        self,
        **overrides,
    ):
        values = dict(
            id="job", task_name="parallel", user_id="user", namespace="example", repo_name="repo",
            cpu_count=1, memory_demand="4000M", gpu_count=0, gpu_type="cpu", node_count=2, tasks_per_node=64,
            job_type="A2", status="Running", created_at=datetime.now(timezone.utc),
        )
        values.update(overrides)
        return values

    def test_legacy_inference_and_snapshot_survive_config_changes(self):
        schema = self.namespace["JobListItem"]
        legacy = schema(**self.fields())
        self.assertTrue(legacy.resource_summary.mode_inferred)
        self.assertEqual(legacy.resource_summary.memory_bytes, 500 * 1024**3)
        self.config["execution"]["slurm"]["mem_mode"] = "explicit"
        snapshotted = schema(**self.fields(resource_mode="slurm_per_cpu"))
        self.assertFalse(snapshotted.resource_summary.mode_inferred)
        self.assertEqual(snapshotted.resource_summary.memory_bytes, 500 * 1024**3)
        legacy_now = schema(**self.fields())
        self.assertEqual(legacy_now.resource_summary.memory_bytes, 8000 * 1024**2)
        self.assertTrue(legacy_now.resource_summary.mode_inferred)

    def test_lightweight_list_and_detail_share_summary_without_touching_payload(self):
        class Metadata(SimpleNamespace):
            @property
            def entry_command(self):
                raise AssertionError("list projection must not load command payload")

        fields = self.fields(resource_mode="slurm_per_cpu")
        listed = self.namespace["JobListItem"].model_validate(Metadata(**fields))
        detailed = self.namespace["JobResponse"](**fields, entry_command="true")
        self.assertEqual(listed.resource_summary, detailed.resource_summary)
        payload = listed.model_dump()
        self.assertEqual(payload["cpu_count"], 1)
        self.assertEqual(payload["memory_demand"], "4000M")
        self.assertNotIn("resource_mode", payload)
        self.assertNotIn("entry_command", payload)

    def test_shared_create_stamps_mode_and_ignores_client_summary_injection(self):
        path = SERVER / "routers" / "jobs.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_job")
        namespace = dict(self.namespace)
        namespace.update(
            magnus_config=self.config,
            models=SimpleNamespace(Job=lambda **values: SimpleNamespace(**values)),
            is_local_mode=True,
            apply_cluster_defaults=lambda value: None,
            normalize_per_cpu_resources=lambda value: None,
            validate_cluster_limits=lambda value: None,
            enforce_field_size_limits=lambda *args: None,
        )
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        db = SimpleNamespace(add=lambda value: None, commit=lambda: None, refresh=lambda value: None)
        job = namespace["create_job"](
            {"task_name": "injected", "entry_command": "true", "resource_mode": "local", "resource_summary": {"cpu_count": 999}},
            "user",
            db,
        )
        self.assertEqual(job.resource_mode, "slurm_per_cpu")
        self.assertFalse(hasattr(job, "resource_summary"))

    def test_nullable_migration_is_idempotent_and_preserves_old_rows(self):
        path = SERVER / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        migration = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_migrations")
        column_loop = next(node for node in migration.body if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple) and any(isinstance(value, ast.Constant) and value.value == "resource_mode" for value in node.iter.elts))
        code = compile(ast.Module(body=[column_loop], type_ignores=[]), str(path), "exec")
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE jobs (id TEXT PRIMARY KEY, memory_demand TEXT)"))
            conn.execute(text("INSERT INTO jobs VALUES ('old', '4000M')"))
        for _ in range(2):
            namespace = {"engine": engine, "text": text, "job_columns": [column["name"] for column in inspect(engine).get_columns("jobs")]}
            exec(code, namespace)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT id, memory_demand, resource_mode FROM jobs")).all(), [("old", "4000M", None)])

    def test_cluster_native_and_external_rows_share_actual_allocation_totals(self):
        from test_gpu_resource_query import Field

        path = SERVER / "routers" / "cluster.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_cluster_stats")
        route.decorator_list = []
        job = SimpleNamespace(**self.fields(slurm_job_id="native", resource_mode="slurm_per_cpu"))
        pending = SimpleNamespace(**self.fields(id="pending", status="Pending"))
        results = iter([[job], [pending], []])
        query = SimpleNamespace()
        query.options = lambda *args: query
        query.filter = lambda *args: query
        query.all = lambda: next(results)
        tasks = [
            {"id": identifier, "name": identifier, "user": "owner", "start_time": datetime.now(timezone.utc).isoformat(),
             "cpu_count": 128, "memory_mb": 512000, "gpu_count": 0, "gpu_type": "cpu", "node_count": 2}
            for identifier in ("native", "external")
        ]
        manager = SimpleNamespace(
            get_all_running_tasks=lambda **kwargs: tasks,
            get_node_snapshot=lambda: SimpleNamespace(total_gpus=0, cpu_total=256, mem_total_mb=1024000),
        )
        self.config.update(cluster={"scheduling": {"mode": "authoritative"}}, server={"scheduler": {"cluster_stats_cache_ttl": 0}})
        namespace = dict(self.namespace)
        namespace.update(
            magnus_config=self.config,
            is_local_mode=False,
            SlurmManager=lambda: manager,
            _cached_slurm_view=lambda key, ttl, producer: producer(),
            _node_name="test",
            _gpu_model="test",
            timezone=timezone,
            models=SimpleNamespace(Job=SimpleNamespace(slurm_job_id=Field(), status=Field()), job_list_load_options=lambda: []),
            Depends=lambda dependency: None,
            database=SimpleNamespace(get_db=None),
            get_current_user=None,
            or_=lambda *args: None,
            _scheduler_sort_key=lambda value: 0,
        )
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(path), "exec"), namespace)
        response = namespace["get_cluster_stats"](db=SimpleNamespace(query=lambda *args: query))
        for row in response["running_jobs"]:
            self.assertEqual(row.resource_summary.cpu_count, 128)
            self.assertEqual(row.resource_summary.memory_bytes, 500 * 1024**3)
            self.assertEqual(row.resource_summary.basis, "allocation")
            self.assertEqual(row.resource_summary.node_count, 2)
            self.assertIsNone(row.resource_summary.tasks_per_node)
        self.assertEqual(response["running_jobs"][0].cpu_count, 1)
        self.assertEqual(response["running_jobs"][1].cpu_count, 128)
        self.assertEqual(response["pending_jobs"][0].resource_summary.basis, "request")
        self.assertEqual(response["pending_jobs"][0].resource_summary.cpu_count, 128)


if __name__ == "__main__":
    unittest.main()
