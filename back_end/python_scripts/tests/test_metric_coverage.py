"""Exercise emitted metrics, without containers, GPUs or a running service."""
import ast
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch


SERVER = Path(__file__).resolve().parents[2] / "server"


class MetricCoverageTests(unittest.TestCase):
    def generated_wrapper(
        self,
        nodes,
        tasks,
    ):
        source = SERVER / "_scheduler" / "_wrapper_template.py"
        spec = importlib.util.spec_from_file_location("metric_test_wrapper", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._build_wrapper_content(
            job_working_table="/work",
            job_ephemeral_table="/tmp/work",
            repo_dir="/repo",
            sif_path="/image.sif",
            system_entry_command="",
            user_token="test-token",
            magnus_address="https://example.invalid",
            job_id="example",
            ephemeral_storage="1G",
            allow_root=False,
            entry_command="exit 99",
            effective_runner="example",
            container_runtime="apptainer",
            node_count=nodes,
            tasks_per_node=tasks,
        )

    def run_sidecar(
        self,
        generated,
        environment,
        samples,
        ticks,
    ):
        module = ModuleType("isolated_generated_metrics_wrapper")
        with patch.dict(sys.modules, {module.__name__: module}):
            exec(compile(generated, "generated_wrapper.py", "exec"), vars(module))
        clock = SimpleNamespace(now=0.0, ticks=0)
        accounting_calls = []
        gpu_calls = []
        outputs = iter(samples)
        writer = mock_open()

        def wait(
            interval,
        ):
            self.assertEqual(interval, 5.0)
            if clock.ticks >= ticks:
                return True
            clock.now += interval
            clock.ticks += 1
            return False

        def read_accounting(
            command,
            **kwargs,
        ):
            accounting_calls.append((clock.now, command, kwargs))
            result = next(outputs)
            if isinstance(result, Exception):
                raise result
            return subprocess.CompletedProcess(command, 0, result, "")

        def read_gpu(
            command,
            **kwargs,
        ):
            gpu_calls.append((clock.now, command, kwargs))
            return "0, 25, 30\n1, 99, 40"

        module.os = SimpleNamespace(
            path=SimpleNamespace(join=lambda *parts: "/".join(parts)),
            environ=environment,
        )
        module.time = SimpleNamespace(
            time=lambda: 1000.0 + clock.now,
            monotonic=lambda: clock.now,
        )
        module.subprocess = SimpleNamespace(
            run=read_accounting,
            check_output=read_gpu,
            DEVNULL=subprocess.DEVNULL,
            PIPE=subprocess.PIPE,
        )
        module.open = writer
        self.diagnostics = io.StringIO()
        module.sys = SimpleNamespace(stderr=self.diagnostics)
        module._metrics_sidecar("/metrics", SimpleNamespace(wait=wait, is_set=lambda: False))
        records = [
            json.loads(line)
            for call in writer().write.call_args_list
            for line in call.args[0].splitlines()
        ]
        return records, accounting_calls, gpu_calls

    def test_generated_sidecar_uses_whole_job_accounting_for_every_topology(self):
        for nodes, tasks, cpus, coverage in ((1, 1, 2, "single_task"), (2, 64, 128, "partial")):
            with self.subTest(nodes=nodes, tasks=tasks):
                samples = ["123.batch|1|cpu=0,mem=100", f"123.batch|1|cpu={cpus * 30},mem=200"]
                if nodes > 1:
                    samples = [
                        "123.extern|2|energy=0\n123.batch|1|cpu=0,mem=100\n123.0|128|cpu=0,mem=1000",
                        "123.extern|2|energy=0\n123.batch|1|cpu=1,mem=200\n123.0|128|cpu=3839,mem=2000",
                    ]
                records, accounting, gpu = self.run_sidecar(
                    generated=self.generated_wrapper(nodes, tasks),
                    environment={
                        "CUDA_VISIBLE_DEVICES": "0",
                        "SLURM_JOB_ID": "123",
                        "SLURM_JOB_CPUS_PER_NODE": "64(x2)" if nodes > 1 else "2",
                        "SLURM_JOB_NUM_NODES": str(nodes),
                        "SLURM_CPUS_PER_TASK": "1",
                    },
                    samples=samples,
                    ticks=7,
                )
                self.assertEqual([call[0] for call in accounting], [5.0, 35.0])
                self.assertEqual([call[0] for call in gpu], list(range(5, 36, 5)))
                for _, command, kwargs in accounting:
                    self.assertEqual(command[0], "sstat")
                    self.assertIn("--allsteps", command)
                    self.assertIn("--jobs=123", command)
                    self.assertIn("--noconvert", command)
                    self.assertEqual(kwargs["timeout"], 10.0)
                cpu = [point for point in records if point["name"] == "system.cpu.utilization"]
                memory = [point for point in records if point["name"] == "system.memory.used_bytes"]
                self.assertEqual(len(cpu), 1)
                self.assertEqual(cpu[0]["value"], 100.0)
                self.assertEqual(cpu[0]["labels"]["normalization_cpus"], str(cpus))
                self.assertEqual([point["value"] for point in memory], [1100, 2200] if nodes > 1 else [100, 200])
                for point in cpu + memory:
                    self.assertEqual(point["labels"]["scope"], "job")
                    self.assertEqual(point["labels"]["coverage"], "all_steps")
                    self.assertEqual(point["labels"]["source"], "slurm_jobacct")
                    self.assertNotIn("node", point["labels"])
                gpu_points = [point for point in records if point["name"].startswith("system.gpu.")]
                self.assertEqual(len(gpu_points), 14)
                for point in gpu_points:
                    self.assertEqual(point["labels"]["scope"], "device")
                    self.assertEqual(point["labels"]["coverage"], coverage)
                    self.assertEqual(point["labels"]["device"], "cuda:0")

    def test_generated_sidecar_resets_after_query_failure_without_zero_filling(self):
        records, accounting, gpu = self.run_sidecar(
            generated=self.generated_wrapper(1, 1),
            environment={"SLURM_JOB_ID": "123", "SLURM_JOB_CPUS_PER_NODE": "2"},
            samples=[
                "123.batch|1|cpu=0,mem=100",
                subprocess.TimeoutExpired("sstat", 10.0),
                "123.batch|1|cpu=120,mem=300",
                "123.batch|1|cpu=180,mem=400",
            ],
            ticks=19,
        )
        self.assertEqual([call[0] for call in accounting], [5.0, 35.0, 65.0, 95.0])
        self.assertEqual(gpu, [])
        cpu = [point for point in records if point["name"] == "system.cpu.utilization"]
        memory = [point for point in records if point["name"] == "system.memory.used_bytes"]
        self.assertEqual([(point["time_unix_ms"], point["value"]) for point in cpu], [(1095000, 100.0)])
        self.assertEqual([point["value"] for point in memory], [100, 300, 400])

    def test_generated_wrapper_loads_without_backend_or_site_packages(self):
        generated = self.generated_wrapper(2, 64)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "wrapper.py"
            source.write_text(generated, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    "import runpy,sys; values=runpy.run_path(sys.argv[1], run_name='isolated_wrapper'); "
                    "print(values['allocation_cpus']({'SLURM_JOB_CPUS_PER_NODE':'64(x2)'})); "
                    "print(values['SlurmJobMetricsReader'].__name__)",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
                cwd=directory,
            )
        self.assertEqual(result.stdout.splitlines(), ["128", "SlurmJobMetricsReader"])

    def test_sustained_empty_accounting_logs_bounded_diagnostic_and_recovers(self):
        records, accounting, _ = self.run_sidecar(
            generated=self.generated_wrapper(2, 64),
            environment={"SLURM_JOB_ID": "123", "SLURM_JOB_CPUS_PER_NODE": "64(x2)"},
            samples=["", "", "", "123.0|128|cpu=0,mem=2048", "123.0|128|cpu=3840,mem=4096"],
            ticks=25,
        )
        self.assertEqual(
            self.diagnostics.getvalue().splitlines(),
            ["[Magnus metrics] Slurm accounting unavailable: missing_steps",
             "[Magnus metrics] Slurm accounting recovered"],
        )
        self.assertEqual(
            [point["value"] for point in records if point["name"] == "system.cpu.utilization"],
            [100.0],
        )
        self.assertEqual(
            [point["value"] for point in records if point["name"] == "system.memory.used_bytes"],
            [2048, 4096],
        )
        self.assertEqual(
            len(accounting),
            5,
        )

    def test_sustained_query_error_logs_no_credentials_and_resumes_sampling(self):
        self.run_sidecar(
            generated=self.generated_wrapper(1, 1),
            environment={"SLURM_JOB_ID": "123", "SLURM_JOB_CPUS_PER_NODE": "2"},
            samples=[subprocess.TimeoutExpired("secret", 10)] * 3 + ["123.batch|1|cpu=0,mem=100"],
            ticks=19,
        )
        self.assertEqual(
            self.diagnostics.getvalue().splitlines(),
            ["[Magnus metrics] Slurm accounting unavailable: query_timeout",
             "[Magnus metrics] Slurm accounting recovered"],
        )

    def docker_collector(
        self,
    ):
        source = SERVER / "_metrics_collector" / "_collector.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        writer = mock_open()
        namespace = {
            "__name__": __name__,
            "logger": Mock(),
            "magnus_config": {"server": {"root": "/unused"}},
            "_read_cpu_usage_usec": lambda pid: 15000000,
            "_read_memory_used_bytes": lambda pid: 4096,
            "_read_process_starttime": Mock(return_value=100),
            "_read_allocated_cpus": lambda pid: 2.0,
            "_docker_inspect_container_pid": Mock(return_value=1),
            "_docker_inspect_visible_gpus": lambda name: ["0"],
            "_sample_nvidia_smi": lambda: {"0": (25.0, 4096.0)},
            "open_protocol_file": lambda *args: writer(),
        }
        exec(compile(tree, str(source), "exec"), namespace)
        collector = namespace["DockerMetricsCollector"]()
        collector._states["example"] = SimpleNamespace(
            pid=1, process_starttime=100, alloc_cpus=2.0, prev_usage_usec=0,
            prev_sample_time=1.0, visible_gpus=["0"],
            container_name="magnus-job-example", metadata_retry_at=60.0,
        )
        return collector, namespace, writer

    def sample_docker(
        self,
        collector,
        namespace,
        writer,
        *,
        now_ms=6000,
        monotonic=6.0,
    ):
        with patch.object(
            namespace["time"],
            "monotonic",
            return_value=monotonic,
        ):
            collector._sample_job(
                "example",
                lambda: {"0": (25.0, 4096.0)},
                now_ms,
            )
        records = [json.loads(line) for call in writer().write.call_args_list for line in call.args[0].splitlines()]
        writer().write.reset_mock()
        return records

    def tick_docker(
        self,
        collector,
        namespace,
        writer,
        *,
        monotonic,
        job_ids=("example",),
    ):
        collector._list_running_docker_jobs = lambda: list(job_ids)
        with patch.object(
            namespace["time"],
            "monotonic",
            return_value=monotonic,
        ):
            collector._tick()
        records = [
            json.loads(line)
            for call in writer().write.call_args_list
            for line in call.args[0].splitlines()
        ]
        writer().write.reset_mock()
        return records

    def test_docker_metrics_have_container_scope_and_device_gpu_scope(self):
        records = self.sample_docker(*self.docker_collector())
        cpu = next(record for record in records if record["name"] == "system.cpu.utilization")
        self.assertEqual(cpu["value"], 150.0)
        self.assertEqual(cpu["labels"]["scope"], "container")
        self.assertTrue(all(record["labels"]["coverage"] == "single_container" for record in records))
        gpu = next(record for record in records if record["name"] == "system.gpu.utilization")
        self.assertEqual(gpu["labels"]["scope"], "device")

    def test_docker_gpu_inspect_recovers_without_retrying_known_metadata(self):
        collector, namespace, writer = self.docker_collector()
        collector._states.clear()
        inspector = Mock(
            side_effect=[None, ["0"]],
        )
        namespace["_docker_inspect_visible_gpus"] = inspector
        point_counts = []
        for instant in (0.0, 5.0, 59.0, 60.0, 65.0, 120.0):
            records = self.tick_docker(
                collector,
                namespace,
                writer,
                monotonic=instant,
            )
            point_counts.append(
                sum(point["name"].startswith("system.gpu.") for point in records),
            )
        self.assertEqual(
            point_counts,
            [0, 0, 0, 0, 2, 2],
        )
        self.assertEqual(
            inspector.call_count,
            2,
        )

    def test_docker_quota_recovery_resets_normalization_interval(self):
        collector, namespace, writer = self.docker_collector()
        collector._states.clear()
        quota = Mock(
            side_effect=[None, 2.0],
        )
        namespace["_read_allocated_cpus"] = quota
        namespace["_read_cpu_usage_usec"] = Mock(
            side_effect=[0, 10_000_000, 120_000_000, 130_000_000, 140_000_000],
        )
        values = []
        with patch.object(
            namespace["os"],
            "cpu_count",
            return_value=8,
        ):
            for instant in (0.0, 5.0, 60.0, 65.0, 70.0):
                records = self.tick_docker(
                    collector,
                    namespace,
                    writer,
                    monotonic=instant,
                )
                values.extend(
                    (instant, point["value"], point["labels"]["normalized_by"])
                    for point in records if point["name"] == "system.cpu.utilization"
                )
        self.assertEqual(
            values,
            [(5.0, 25.0, "host_cpu_count"), (60.0, 25.0, "host_cpu_count"),
             (70.0, 100.0, "cgroup_quota")],
        )
        self.assertEqual(
            quota.call_count,
            2,
        )

    def test_docker_verified_empty_gpu_selection_is_cached(self):
        collector, namespace, writer = self.docker_collector()
        collector._states.clear()
        inspector = Mock(
            return_value=[],
        )
        namespace["_docker_inspect_visible_gpus"] = inspector
        for instant in (0.0, 60.0, 120.0):
            records = self.tick_docker(
                collector,
                namespace,
                writer,
                monotonic=instant,
            )
            self.assertFalse(
                any(point["name"].startswith("system.gpu.") for point in records),
            )
        self.assertEqual(
            inspector.call_count,
            1,
        )

    def test_metadata_timeouts_are_bounded_per_tick_and_fair_across_jobs(self):
        collector, namespace, writer = self.docker_collector()
        collector._states.clear()
        events = []
        for job_id in ("a", "b", "c"):
            state = namespace["_JobMetricsState"](
                "magnus-job-" + job_id,
                1,
                100,
            )
            state.alloc_cpus = 2.0
            collector._states[job_id] = state

        def read_memory(
            pid,
        ):
            events.append("sample")
            return 4096

        def failed_inspect(
            container_name,
        ):
            events.append(container_name)
            return None

        namespace["_read_memory_used_bytes"] = read_memory
        namespace["_docker_inspect_visible_gpus"] = failed_inspect
        for instant, job_id in ((60.0, "a"), (65.0, "b"), (70.0, "c")):
            events.clear()
            records = self.tick_docker(
                collector,
                namespace,
                writer,
                monotonic=instant,
                job_ids=("a", "b", "c"),
            )
            self.assertEqual(
                events,
                ["sample", "sample", "sample", "magnus-job-" + job_id],
            )
            self.assertEqual(
                sum(point["name"] == "system.memory.used_bytes" for point in records),
                3,
            )

    def test_docker_gpu_inspect_distinguishes_failed_and_ambiguous_responses(self):
        source = SERVER / "_metrics_collector" / "_docker_inspect.py"
        spec = importlib.util.spec_from_file_location(
            "isolated_docker_inspect",
            source,
        )
        module = importlib.util.module_from_spec(
            spec,
        )
        spec.loader.exec_module(
            module,
        )
        for value, expected in (
            ("all", []), ("void", []), ("none", []), ("", []), ("0,3", ["0", "3"]),
        ):
            with self.subTest(
                value=value,
            ), patch.object(
                module.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(["NVIDIA_VISIBLE_DEVICES=" + value]),
                ),
            ):
                self.assertEqual(
                    module._docker_inspect_visible_gpus(
                        "example",
                    ),
                    expected,
                )
        for failure in (
            subprocess.TimeoutExpired("docker", 5),
            subprocess.CompletedProcess([], 1, ""),
            subprocess.CompletedProcess([], 0, "{"),
        ):
            with patch.object(
                module.subprocess,
                "run",
                side_effect=failure if isinstance(failure, Exception) else None,
                return_value=failure,
            ):
                self.assertIsNone(
                    module._docker_inspect_visible_gpus(
                        "example",
                    ),
                )

    def test_docker_reused_pid_is_dropped_before_reading_cgroup_data(self):
        collector, namespace, writer = self.docker_collector()
        namespace["_read_process_starttime"].return_value = 200
        namespace["_read_cpu_usage_usec"] = Mock()
        records = self.sample_docker(
            collector,
            namespace,
            writer,
        )
        self.assertEqual(records, [])
        namespace["_read_cpu_usage_usec"].assert_not_called()
        self.assertNotIn("example", collector._states)

    def test_docker_process_exit_during_sample_discards_points(self):
        collector, namespace, writer = self.docker_collector()
        namespace["_read_process_starttime"].side_effect = [100, None]
        self.assertEqual(self.sample_docker(collector, namespace, writer), [])
        self.assertNotIn("example", collector._states)

    def test_docker_creation_rechecks_pid_after_slow_inspect(self):
        collector, namespace, writer = self.docker_collector()
        collector._states.clear()
        namespace["_docker_inspect_container_pid"].side_effect = [1, 2]
        self.assertEqual(self.sample_docker(collector, namespace, writer), [])
        self.assertNotIn("example", collector._states)

    def test_docker_cpu_delta_uses_elapsed_time_despite_wall_clock_adjustment(self):
        for timestamp in (-1000, 1_000_000):
            with self.subTest(timestamp=timestamp):
                records = self.sample_docker(
                    *self.docker_collector(),
                    now_ms=timestamp,
                )
                cpu = next(point for point in records if point["name"] == "system.cpu.utilization")
                self.assertEqual(cpu["value"], 150.0)
                self.assertEqual(cpu["time_unix_ms"], timestamp)

    def test_docker_missing_counter_resets_the_cpu_baseline(self):
        collector, namespace, writer = self.docker_collector()
        namespace["_read_cpu_usage_usec"] = Mock(side_effect=[None, 0, 10_000_000])
        cpu_values = []
        for instant in (6.0, 11.0, 16.0):
            records = self.sample_docker(
                collector,
                namespace,
                writer,
                monotonic=instant,
            )
            cpu_values.extend(point["value"] for point in records if point["name"] == "system.cpu.utilization")
        self.assertEqual(cpu_values, [100.0])

    def test_docker_without_host_proc_only_keeps_device_metrics(self):
        collector, namespace, writer = self.docker_collector()
        collector._states["example"].process_starttime = None
        namespace["_read_process_starttime"].return_value = None
        namespace["_read_cpu_usage_usec"] = Mock()
        records = self.sample_docker(
            collector,
            namespace,
            writer,
        )
        namespace["_read_cpu_usage_usec"].assert_not_called()
        self.assertEqual(
            {point["name"] for point in records},
            {"system.gpu.utilization", "system.gpu.memory.used_bytes"},
        )

    def test_process_starttime_handles_names_and_rejects_unavailable_processes(self):
        source = SERVER / "_metrics_collector" / "_cgroup.py"
        spec = importlib.util.spec_from_file_location("metrics_cgroup_test", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suffix = " ".join(["S"] + ["0"] * 18 + ["654321"])
        with patch("builtins.open", mock_open(read_data="123 (worker ) with ( spaces) " + suffix)):
            self.assertEqual(module._read_process_starttime(123), 654321)
        for content in ("", "123 bad stat", "123 (worker) S 0", "124 (worker) " + suffix, "123 (worker) " + suffix.replace("S", "Z")):
            with patch("builtins.open", mock_open(read_data=content)):
                self.assertIsNone(module._read_process_starttime(123))
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertIsNone(module._read_process_starttime(123))


if __name__ == "__main__":
    unittest.main()
