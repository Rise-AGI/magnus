"""Exercise emitted metrics, without containers, GPUs or a running service."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import mock_open


SERVER = Path(__file__).resolve().parents[2] / "server"


class MetricCoverageTests(unittest.TestCase):
    def test_generated_sidecar_labels_scope_and_does_not_clip_overquota(self):
        source = SERVER / "_scheduler" / "_wrapper_template.py"
        spec = importlib.util.spec_from_file_location("metric_test_wrapper", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for nodes, tasks, coverage in ((1, 1, "single_task"), (2, 64, "partial")):
            with self.subTest(nodes=nodes, tasks=tasks):
                generated = module._build_wrapper_content(
                    job_working_table = "/work",
                    job_ephemeral_table = "/tmp/work",
                    repo_dir = "/repo",
                    sif_path = "/image.sif",
                    system_entry_command = "",
                    user_token = "test-token",
                    magnus_address = "https://example.invalid",
                    job_id = "example",
                    ephemeral_storage = "1G",
                    allow_root = False,
                    entry_command = "true",
                    effective_runner = "example",
                    container_runtime = "apptainer",
                    node_count = nodes,
                    tasks_per_node = tasks,
                )
                tree = ast.parse(generated)
                function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_metrics_sidecar")
                clocks, counters, waits = iter((1.0, 6.0)), iter((0, 15000000)), iter((False, True))
                writer = mock_open()
                namespace = {
                    "json": json,
                    "os": SimpleNamespace(path=SimpleNamespace(join=lambda *parts: "/".join(parts)), environ={"CUDA_VISIBLE_DEVICES": "0"}),
                    "time": SimpleNamespace(time=lambda: next(clocks)),
                    "subprocess": SimpleNamespace(check_output=lambda *args, **kwargs: "0, 25, 30", DEVNULL=None),
                    "_allocated_cpus": lambda: 2,
                    "_metric_cgroup_scope": lambda controller: "batch_step",
                    "_read_cpu_usage_usec": lambda: next(counters),
                    "_read_memory_used_bytes": lambda: 4096,
                    "open": writer,
                }
                exec(compile(ast.Module(body=[function], type_ignores=[]), "sidecar", "exec"), namespace)
                namespace["_metrics_sidecar"]("/metrics", SimpleNamespace(wait=lambda interval: next(waits)))
                records = [json.loads(line) for call in writer().write.call_args_list for line in call.args[0].splitlines()]
                self.assertEqual(len(records), 4)
                by_name = {record["name"]: record for record in records}
                cpu = by_name["system.cpu.utilization"]
                self.assertEqual(cpu["value"], 150.0)
                self.assertEqual(cpu["labels"]["scope"], "batch_step")
                self.assertEqual(cpu["labels"]["normalization_cpus"], "2.0")
                self.assertTrue(all(record["labels"]["coverage"] == coverage for record in records))
                self.assertEqual(by_name["system.gpu.utilization"]["labels"]["scope"], "device")
                scope_function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_metric_cgroup_scope")
                namespace["os"].environ["SLURM_JOB_ID"] = "123"
                namespace["_is_pure_v2"] = lambda: True
                exec(compile(ast.Module(body=[scope_function], type_ignores=[]), "scope", "exec"), namespace)
                for path, expected in (
                    ("/system.slice/slurmd.service", "unverified_cgroup"),
                    ("/slurm/uid_1/job_123/step_batch", "batch_step"),
                    ("/slurm/uid_1/job_999/step_batch", "unverified_cgroup"),
                    ("/slurm/uid_1/job_123", "job_node"),
                    ("/slurm/uid_1/job_123/step_0", "slurm_step"),
                ):
                    namespace["_cg_paths"] = lambda: (path, None, None)
                    self.assertEqual(namespace["_metric_cgroup_scope"]("cpu"), expected)

    def test_docker_metrics_have_container_scope_and_device_gpu_scope(self):
        source = SERVER / "_metrics_collector" / "_collector.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        writer = mock_open()
        namespace = {
            "__name__": __name__,
            "magnus_config": {"server": {"root": "/unused"}},
            "_read_cpu_usage_usec": lambda pid: 15000000,
            "_read_memory_used_bytes": lambda pid: 4096,
            "open": writer,
        }
        exec(compile(tree, str(source), "exec"), namespace)
        collector = namespace["DockerMetricsCollector"]()
        collector._states["example"] = SimpleNamespace(
            pid=1, alloc_cpus=2.0, prev_usage_usec=0, prev_wall_ms=1000, visible_gpus=["0"],
        )
        collector._sample_job("example", lambda: {"0": (25.0, 4096.0)}, 6000)
        records = [json.loads(line) for call in writer().write.call_args_list for line in call.args[0].splitlines()]
        cpu = next(record for record in records if record["name"] == "system.cpu.utilization")
        self.assertEqual(cpu["value"], 150.0)
        self.assertEqual(cpu["labels"]["scope"], "container")
        self.assertTrue(all(record["labels"]["coverage"] == "single_container" for record in records))
        gpu = next(record for record in records if record["name"] == "system.gpu.utilization")
        self.assertEqual(gpu["labels"]["scope"], "device")


if __name__ == "__main__":
    unittest.main()
