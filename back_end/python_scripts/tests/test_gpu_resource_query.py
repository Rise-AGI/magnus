"""Exercise GPU resource parsing and cluster allocation without a Slurm daemon."""
from __future__ import annotations

import ast
import __future__
from datetime import datetime, timezone
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_queries():
    namespace = {
        "__name__": __name__,
        "magnus_config": {"cluster": {"gpus": [{"value": "a100"}]}},
        "logger": logging.getLogger("slurm_gpu_resources_test"),
        "_Transport": object,
    }
    size_path = SERVER / "_size_utils.py"
    exec(compile(size_path.read_text(encoding="utf-8"), str(size_path), "exec"), namespace)
    source = SERVER / "_slurm_manager" / "_resource_query.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    # Remove only application imports, which initialize configuration/transports.
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(source), "exec"), namespace)
    return SimpleNamespace(**namespace)


class FakeTransport:
    def __init__(
        self,
        nodes: str,
        jobs: list,
    ):
        self.nodes = nodes
        self.jobs = jobs
        self.commands = []

    def run(
        self,
        command,
        **kwargs,
    ):
        self.commands.append(command)
        output = self.nodes if command[0] == "scontrol" else json.dumps({"jobs": self.jobs})
        return SimpleNamespace(stdout=output)


class SlurmGpuTests(unittest.TestCase):
    def setUp(self):
        self.module = load_queries()
        self.manager = self.module._ResourceQueryMixin()

    def allocation(
        self,
        **job,
    ):
        return self.module._job_gpu_allocation(job, "unknown")

    def test_mixed_types_and_annotation_commas(self):
        self.assertEqual(
            self.module._parse_gpu_resources(
                "gpu:rtx5090:4(S:0-1),gpu:a100:2(IDX:0,2),nic:1,mps:100,gres/gpumem=123",
            ),
            [("rtx5090", 4), ("a100", 2)],
        )

    def test_untyped_and_invalid_entries(self):
        for value in (None, "", "(null)", "gpu:a100:-2", "gpu:a100:bad", "gpu::2", "gres/gpuutil=99"):
            with self.subTest(value=value):
                self.assertEqual(self.module._parse_gpu_resources(value), [])
        self.assertEqual(self.module._parse_gpu_resources("gpu:2,gres/gpu:3"), [(None, 2), (None, 3)])

    def test_null_type_in_detailed_gres_is_an_untyped_allocation(self):
        for value, count in (("gpu:(null):4(IDX:0-3)", 4), ("gpu:(null):0(IDX:N/A)", 0)):
            with self.subTest(value=value):
                self.assertEqual(self.module._parse_gpu_resources(value), [(None, count)])
                self.assertTrue(self.module._gpu_source_valid(value))

    def test_allocated_total_is_not_added_to_typed_tres(self):
        self.assertEqual(
            self.allocation(
                tres_alloc_str="cpu=8,gres/gpu=6,gres/gpu:a100=2,gres/gpu:rtx5090=4",
                tres_per_node="gpu:a100:1",
                node_count=3,
            ),
            (6, "mixed"),
        )

    def test_typed_allocations_without_aggregate_are_summed(self):
        self.assertEqual(
            self.allocation(tres_alloc_str="gres/gpu:a100=2,gres/gpu:rtx5090=4"),
            (6, "mixed"),
        )

    def test_explicit_zero_allocation_does_not_fall_back_to_request(self):
        self.assertEqual(
            self.allocation(tres_alloc_str="gres/gpu=0", tres_per_node="gpu:a100:2")[0],
            0,
        )

    def test_node_details_are_already_allocations(self):
        self.assertEqual(
            self.allocation(
                gres_detail=["gpu:a100:2(IDX:0,2)", "gpu:a100:1(IDX:3)"],
                tres_per_node="gpu:a100:2",
                node_count=2,
            ),
            (3, "a100"),
        )

    def test_model_can_come_from_detail_when_total_is_untyped(self):
        self.assertEqual(
            self.allocation(tres_alloc_str="gres/gpu=1", gres_detail=["gpu:a100:1(IDX:0)"]),
            (1, "a100"),
        )

    def test_per_node_fallback_uses_old_and_new_node_count_schema(self):
        for node_count in (3, {"set": True, "infinite": False, "number": 3}):
            with self.subTest(node_count=node_count):
                self.assertEqual(
                    self.allocation(tres_per_node="gres/gpu:a100:2", node_count=node_count),
                    (6, "a100"),
                )

    def test_untyped_model_is_not_guessed_on_mixed_station(self):
        self.assertEqual(self.module._resolve_default_gpu_model(), "a100")
        self.module.magnus_config["cluster"]["gpus"].append({"value": "rtx5090"})
        self.assertEqual(self.module._resolve_default_gpu_model(), "unknown")

    def test_capacity_and_usage_share_whole_job_allocation(self):
        nodes = """NodeName=one
   CPUTot=32
   Gres=gpu:rtx5090:4(S:0-1),gpu:a100:2(S:0)
   RealMemory=64000
"""
        jobs = [
            {"job_id": 1, "job_state": ["RUNNING"], "tres_alloc_str": "cpu=4,mem=8G,gres/gpu=3,gres/gpu:a100=3"},
            {"job_id": 2, "job_state": "COMPLETING", "tres_alloc_str": "cpu=2,mem=4G", "gres_detail": ["gpu:rtx5090:1(IDX:3)"]},
            {"job_id": 3, "job_state": ["COMPLETED"], "tres_alloc_str": "gres/gpu=6"},
        ]
        self.manager._transport = FakeTransport(nodes, jobs)
        snapshot = self.manager.get_node_snapshot()
        self.assertEqual((snapshot.total_gpus, snapshot.cpu_total, snapshot.mem_total_mb), (6, 32, 64000))
        self.assertEqual(self.manager.get_resource_snapshot(), {"total_gpus": 6, "slurm_used_gpus": 4})
        self.assertEqual(self.manager.get_cluster_free_gpus(), 2)
        tasks = self.manager.get_all_running_tasks(partition="compute")
        self.assertEqual([task["gpu_count"] for task in tasks], [3, 1])
        self.assertIn("--partition=compute", self.manager._transport.commands[-1])
        self.assertIn("--states=all", self.manager._transport.commands[-1])

    def test_partition_capacity_filters_nodes_and_keeps_all_gpu_types(self):
        nodes = "\n".join([
            "NodeName=one Gres=gpu:rtx5090:4,gpu:a100:2 CPUTot=32 RealMemory=64000 Partitions=gpu,shared",
            "NodeName=two Gres=gpu:2 CPUTot=16 RealMemory=32000 Partitions=shared",
            "NodeName=three Gres=gpu:8 CPUTot=64 RealMemory=128000 Partitions=other",
        ])
        self.manager._transport = FakeTransport(nodes, [])
        snapshot = self.manager.get_partition_snapshot("shared")
        self.assertEqual((snapshot.total_gpus, snapshot.cpu_total, snapshot.mem_total_mb), (8, 48, 96000))

    def test_usage_query_failure_cannot_report_all_gpus_free(self):
        for response in (OSError("squeue unavailable"), "invalid JSON"):
            with self.subTest(response=response):
                def run(
                    command,
                    **kwargs,
                ):
                    if command[0] == "scontrol":
                        return SimpleNamespace(stdout="Gres=gpu:a100:6")
                    if isinstance(response, Exception):
                        raise response
                    return SimpleNamespace(stdout=response)

                self.manager._transport = SimpleNamespace(run=run)
                with self.assertLogs("slurm_gpu_resources_test", level="ERROR"):
                    self.assertEqual(self.manager.get_resource_snapshot(), {"total_gpus": 0, "slurm_used_gpus": 0})
                with self.assertLogs("slurm_gpu_resources_test", level="ERROR"):
                    self.assertEqual(self.manager.get_cluster_free_gpus(), 0)
                with self.assertLogs("slurm_gpu_resources_test", level="ERROR"):
                    self.assertEqual(self.manager.get_all_running_tasks(), [])

    def test_strict_usage_does_not_skip_unparseable_running_job(self):
        self.manager._transport = FakeTransport(
            "Gres=gpu:a100:6",
            [{"job_id": 1, "job_state": ["RUNNING"], "tres_alloc_str": "gres/gpu=4", "start_time": 10**30}],
        )
        with self.assertLogs("slurm_gpu_resources_test", level="ERROR"):
            self.assertEqual(self.manager.get_resource_snapshot(), {"total_gpus": 0, "slurm_used_gpus": 0})


class Field:
    """Query predicates are irrelevant to this route's supplied result fixtures."""
    def in_(
        self,
        value,
    ):
        return self

    def is_(
        self,
        value,
    ):
        return self

    def __invert__(self):
        return self


class ClusterGpuTests(unittest.TestCase):
    def response(self, node_gpus=6):
        source = SERVER / "routers" / "cluster.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_cluster_stats")
        route.decorator_list = []
        model_assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_gpu_model" for target in node.targets)
        )
        job = SimpleNamespace(
            slurm_job_id="123", job_type="A2", gpu_count=2, node_count=3,
            start_time=datetime.now(timezone.utc),
        )
        results = iter([[job], [], []])
        query = SimpleNamespace()
        query.options = lambda *args: query
        query.filter = lambda *args: query
        query.all = lambda: next(results)
        manager = SimpleNamespace(
            get_all_running_tasks=lambda **kwargs: [{"id": "123", "gpu_count": 6, "cpu_count": 3, "memory_mb": 3072}],
            get_node_snapshot=lambda **kwargs: load_queries().NodeSnapshot(
                6, 32, 64000,
                nodes=(SimpleNamespace(allocated_gpus=node_gpus, allocated_cpus=3, allocated_mem_mb=3072),),
            ),
        )
        config = {"execution": {"backend": "slurm"}, "cluster": {"scheduling": {"mode": "authoritative"}, "gpus": [
            {"label": "RTX 5090"}, {"label": "A100"}, {"label": "A100"},
        ]}, "server": {"scheduler": {"cluster_stats_cache_ttl": 0}}}
        namespace = {
            "magnus_config": config, "is_local_mode": False, "SlurmManager": lambda: manager,
            "_cached_slurm_view": lambda key, ttl, producer: producer(), "_node_name": "test",
            "models": SimpleNamespace(Job=SimpleNamespace(slurm_job_id=Field(), status=Field()), job_list_load_options=lambda: []),
            "JobListItem": SimpleNamespace(model_validate=lambda value: value),
            "JobType": SimpleNamespace(EXTERNAL="external"),
            "JobStatus": SimpleNamespace(PENDING=1, QUEUED=2, PAUSED=3, PREPARING=4, BUILDING=5),
            "Depends": lambda dependency: None, "database": SimpleNamespace(get_db=None), "get_current_user": None,
            "datetime": datetime, "timezone": timezone, "or_": lambda *args: None,
            "_scheduler_sort_key": lambda value: 0,
            "_tenant_partitions": lambda: [],
            "time": time,
        }
        from test_job_resources import load_resources
        namespace.update(load_resources())
        namespace["JobResourceSummary"] = lambda **values: SimpleNamespace(**values)
        producer = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_query_cluster_view")
        totals = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_cluster_resource_totals")
        isolated = ast.Module(body=[model_assignment, totals, producer, route], type_ignores=[])
        exec(compile(isolated, str(source), "exec", flags=__future__.annotations.compiler_flag), namespace)
        return namespace["get_cluster_stats"](db=SimpleNamespace(query=lambda *args: query))

    def test_cluster_uses_allocation_instead_of_db_per_node_request(self):
        response = self.response()
        self.assertEqual(response["resources"]["used"], 6)
        self.assertEqual(response["resources"]["free"], 0)
        self.assertEqual(response["resources"]["gpu_model"], "RTX 5090 / A100")
        self.assertEqual(response["running_jobs"][0].gpu_count, 2)
        self.assertEqual(response["running_jobs"][0].resource_summary.gpu_count, 6)
        self.assertEqual(response["running_jobs"][0].resource_summary.basis, "allocation")

    def test_cluster_pool_allocation_is_independent_of_whole_job_rows(self):
        response = self.response(node_gpus=2)
        self.assertEqual(response["resources"]["gpu_used"], 2)
        self.assertEqual(response["resources"]["used"], 2)
        self.assertEqual(response["resources"]["free"], 4)
        self.assertEqual(response["running_jobs"][0].resource_summary.gpu_count, 6)


if __name__ == "__main__":
    unittest.main()
