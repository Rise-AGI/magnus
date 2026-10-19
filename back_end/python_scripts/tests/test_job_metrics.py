"""Slurm step accounting parsing and transport without a daemon or service."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "server" / "_metrics_collector" / "_job_accounting.py"
SPEC = importlib.util.spec_from_file_location("slurm_job_metrics_test_subject", SOURCE)
METRICS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = METRICS
SPEC.loader.exec_module(METRICS)


class SlurmJobMetricsTests(unittest.TestCase):
    def test_mpi_job_includes_batch_and_all_ranks_without_counting_empty_extern(self):
        output = (
            "123.extern|2|energy=0\n"
            "123.batch|1|cpu=00:00:02,energy=0,mem=14929920\n"
            "123.0|128|cpu=4-18:39:42,mem=349786619904\n"
        )
        job = METRICS.parse_job_metrics(output, ["123"])["123"]
        self.assertEqual(set(job.steps), {"123.batch", "123.0"})
        self.assertEqual(job.steps["123.0"].cpu_seconds, 412782)
        self.assertEqual(job.steps["123.0"].task_count, 128)
        self.assertEqual(job.memory_bytes, 349801549824)
        self.assertTrue(job.cpu_complete)
        self.assertTrue(job.memory_complete)

    def test_batch_only_job_and_accounted_extern_are_real_steps(self):
        job = METRICS.parse_job_metrics(
            "123.batch|1|cpu=00:01:00,mem=8192\n123.extern|1|cpu=00:00:01,mem=4096\n",
            ["123"],
        )["123"]
        self.assertEqual(job.memory_bytes, 12288)
        self.assertEqual(len(job.steps), 2)

    def test_missing_job_is_unknown_not_zero(self):
        jobs = METRICS.parse_job_metrics("123.batch|1|cpu=0,mem=0\n", ["123", "124"])
        self.assertEqual(jobs["123"].memory_bytes, 0)
        self.assertTrue(jobs["123"].cpu_complete)
        self.assertIsNone(jobs["124"].memory_bytes)
        self.assertFalse(jobs["124"].cpu_complete)
        self.assertEqual(jobs["124"].steps, {})

    def test_missing_cpu_or_memory_invalidates_only_that_metric(self):
        jobs = METRICS.parse_job_metrics(
            "123.batch|1|cpu=0,mem=100\n123.0|8|mem=800\n124.batch|1|cpu=5\n",
            ["123", "124"],
        )
        self.assertFalse(jobs["123"].cpu_complete)
        self.assertEqual(jobs["123"].memory_bytes, 900)
        self.assertTrue(jobs["124"].cpu_complete)
        self.assertIsNone(jobs["124"].memory_bytes)

    def test_regular_empty_rows_and_nonempty_extern_are_not_discarded(self):
        for row in ("123.0|128|energy=0", "123.batch|1|", "123.extern|1|cpu=0"):
            with self.subTest(row=row):
                job = METRICS.parse_job_metrics(row, ["123"])["123"]
                self.assertEqual(len(job.steps), 1)
                self.assertIsNone(job.memory_bytes)

    def test_raw_and_elapsed_cpu_units(self):
        for value, expected in (
            ("0", 0.0),
            ("3.25", 3.25),
            ("02:03.5", 123.5),
            ("25:00:00", 90000.0),
            ("2-03:04:05", 183845.0),
            ("00:00:00.125", 0.125),
        ):
            with self.subTest(value=value):
                self.assertEqual(METRICS._cpu_seconds(value), expected)

    def test_unknown_and_sentinel_cpu_values_stay_unknown(self):
        for value in (
            "", "Unknown", "N/A", "-1", "nan", "inf", "00:00:60", "00:60:00",
            "1-24:00:00", "1-00:00", "1K", "18446744073709551615",
            "213503982334-14:25:51",
        ):
            with self.subTest(value=value):
                self.assertIsNone(METRICS._cpu_seconds(value))

    def test_invalid_memory_does_not_silently_change_units(self):
        for value in ("", "Unknown", "-1", "NaN", "1G", "1.5", "18446744073709551615"):
            with self.subTest(value=value):
                job = METRICS.parse_job_metrics(f"123.0|1|cpu=1,mem={value}", ["123"])["123"]
                self.assertIsNone(job.memory_bytes)
                self.assertTrue(job.cpu_complete)

    def test_duplicate_steps_are_not_counted_twice(self):
        row = "123.batch|1|cpu=2,mem=4096\n"
        job = METRICS.parse_job_metrics(row + row, ["123"])["123"]
        self.assertEqual(job.memory_bytes, 4096)
        with self.assertRaises(ValueError):
            METRICS.parse_job_metrics(row + "123.batch|1|cpu=3,mem=4096", ["123"])

    def test_invalid_identity_or_broken_rows_reject_the_snapshot(self):
        for row in (
            "124.0|1|cpu=1,mem=2",
            "1234.0|1|cpu=1,mem=2",
            "123|1|cpu=1,mem=2",
            "123.0suffix|1|cpu=1,mem=2",
            "123.0|1",
            "123.0|1|cpu=1,mem=2|unexpected",
            "123.0|1|cpu=1,mem=2,mem=3",
            "123.0|1|cpu=1,broken",
        ):
            with self.subTest(row=row), self.assertRaises(ValueError):
                METRICS.parse_job_metrics(row, ["123"])

    def test_query_has_one_exact_job_and_forwards_timeout(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "123.batch|1|cpu=2,mem=4096\n", "")
        reader = METRICS.SlurmJobMetricsReader(run=run)
        job = reader.read("123", timeout=7.5)
        self.assertEqual(len(calls), 1)
        command, arguments = calls[0]
        self.assertIn("--jobs=123", command)
        self.assertIn("--allsteps", command)
        self.assertIn("--noconvert", command)
        self.assertEqual(arguments, {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "universal_newlines": True,
            "check": True,
            "timeout": 7.5,
        })
        self.assertEqual(job.memory_bytes, 4096)

    def test_query_never_expands_empty_or_invalid_scope(self):
        reader = METRICS.SlurmJobMetricsReader(run=lambda *args, **kwargs: self.fail("unexpected query"))
        self.assertEqual(METRICS.parse_job_metrics("", []), {})
        with self.assertRaises(ValueError):
            METRICS.parse_job_metrics("", "123")
        for job_id in ("", "0", "-1", "123.0", "123,124", "all", "123;true", " 123", 123):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                reader.read(job_id)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                reader.read("123", timeout=timeout)

    def test_query_error_or_partial_response_never_becomes_zero(self):
        for returncode, stderr in ((1, ""), (0, "sstat: error: unable to contact one node")):
            with self.subTest(returncode=returncode, stderr=stderr):
                reader = METRICS.SlurmJobMetricsReader(
                    run=lambda *args, **kwargs: subprocess.CompletedProcess(
                        args[0], returncode, "123.batch|1|cpu=2,mem=4096\n", stderr,
                    ),
                )
                with self.assertRaises(RuntimeError):
                    reader.read("123")

    def test_timeout_remains_a_query_failure(self):
        def run(*args, **kwargs):
            raise subprocess.TimeoutExpired("sstat", kwargs["timeout"])
        reader = METRICS.SlurmJobMetricsReader(run=run)
        with self.assertRaises(subprocess.TimeoutExpired):
            reader.read("123", timeout=1)


class SlurmAllocationCpuTests(unittest.TestCase):
    def test_allocated_node_list_includes_repetitions_and_heterogeneous_nodes(self):
        for allocation, nodes, expected in (
            ("64(x2)", "2", 128),
            ("64(x2),32", "3", 160),
            ("8,16", "2", 24),
            ("16", "1", 16),
        ):
            with self.subTest(allocation=allocation):
                self.assertEqual(
                    METRICS.allocation_cpus({
                        "SLURM_JOB_CPUS_PER_NODE": allocation,
                        "SLURM_JOB_NUM_NODES": nodes,
                        "SLURM_CPUS_PER_TASK": "1",
                    }),
                    expected,
                )

    def test_allocation_list_itself_is_sufficient_without_optional_node_counts(self):
        self.assertEqual(METRICS.allocation_cpus({"SLURM_JOB_CPUS_PER_NODE": "64(x2),32"}), 160)

    def test_wrong_node_count_and_conflicting_aliases_are_unknown(self):
        for environment in (
            {"SLURM_JOB_CPUS_PER_NODE": "64(x2)", "SLURM_JOB_NUM_NODES": "1"},
            {"SLURM_JOB_CPUS_PER_NODE": "64(x2)", "SLURM_NNODES": "3"},
            {"SLURM_JOB_NUM_NODES": "1", "SLURM_NNODES": "2", "SLURM_CPUS_ON_NODE": "64"},
            {"SLURM_JOB_CPUS_PER_NODE": "64", "SLURM_NNODES": "invalid"},
        ):
            with self.subTest(environment=environment):
                self.assertIsNone(METRICS.allocation_cpus(environment))

    def test_batch_or_task_cpu_counts_never_replace_the_job_allocation(self):
        for environment in (
            {},
            {"SLURM_CPUS_PER_TASK": "8"},
            {"SLURM_CPUS_ON_NODE": "64"},
            {"SLURM_NNODES": "1", "SLURM_CPUS_ON_NODE": "64"},
            {"SLURM_JOB_NUM_NODES": "2", "SLURM_CPUS_ON_NODE": "64"},
            {"SLURM_JOB_NUM_NODES": "1", "SLURM_CPUS_PER_TASK": "8"},
        ):
            with self.subTest(environment=environment):
                self.assertIsNone(METRICS.allocation_cpus(environment))

    def test_malformed_primary_allocation_never_falls_back(self):
        for value in (
            "", "0", "64(x0)", "64(x2),", "64garbage", "64(x2)garbage", " 64",
            "64, 64", "-1", "18446744073709551615", "18446744073709551613(x2)",
        ):
            with self.subTest(value=value):
                self.assertIsNone(METRICS.allocation_cpus({
                    "SLURM_JOB_CPUS_PER_NODE": value,
                    "SLURM_JOB_NUM_NODES": "1",
                    "SLURM_CPUS_ON_NODE": "64",
                }))


class SlurmJobSamplerTests(unittest.TestCase):
    def snapshot(
        self,
        rows: str,
        job_id: str = "123",
    ):
        return METRICS.parse_job_metrics(rows, [job_id])[job_id]

    def by_name(
        self,
        points,
    ):
        return {point["name"]: point for point in points}

    def test_whole_job_cpu_and_exact_memory_use_all_steps(self):
        sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=128)
        first = self.snapshot("123.batch|1|cpu=2,mem=100\n123.0|128|cpu=1000,mem=20000")
        second = self.snapshot("123.batch|1|cpu=3,mem=101\n123.0|128|cpu=4839,mem=30000")
        points = self.by_name(sampler.sample(first, 10.0, 10000))
        self.assertEqual(set(points), {"system.memory.used_bytes"})
        points = self.by_name(sampler.sample(second, 40.0, 40000))
        self.assertEqual(points["system.cpu.utilization"]["value"], 100.0)
        self.assertEqual(points["system.memory.used_bytes"]["value"], 30101)
        self.assertIsInstance(points["system.memory.used_bytes"]["value"], int)
        self.assertEqual(points["system.cpu.utilization"]["labels"], {
            "scope": "job",
            "coverage": "all_steps",
            "source": "slurm_jobacct",
            "normalized_by": "allocated_cpus",
            "normalization_cpus": "128",
        })
        self.assertEqual(points["system.memory.used_bytes"]["labels"]["memory_basis"], "slurm_tres_mem")
        self.assertNotIn("node", points["system.memory.used_bytes"]["labels"])

    def test_unchanged_counters_are_zero_but_above_quota_is_not_clipped(self):
        sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=2)
        first = self.snapshot("123.batch|1|cpu=0,mem=0")
        sampler.sample(first, 0.0, 0)
        points = self.by_name(sampler.sample(first, 10.0, 10000))
        self.assertEqual(points["system.cpu.utilization"]["value"], 0)
        second = self.snapshot("123.batch|1|cpu=30,mem=0")
        points = self.by_name(sampler.sample(second, 20.0, 20000))
        self.assertEqual(points["system.cpu.utilization"]["value"], 150)

    def test_step_start_finish_task_count_change_and_reset_each_leave_one_gap(self):
        initial = self.snapshot("123.batch|1|cpu=100,mem=100\n123.0|4|cpu=400,mem=400")
        for changed, next_snapshot in (
            (
                "123.batch|1|cpu=101,mem=100",
                "123.batch|1|cpu=141,mem=100",
            ),
            (
                "123.batch|1|cpu=101,mem=100\n123.0|4|cpu=401,mem=400\n123.1|4|cpu=20,mem=40",
                "123.batch|1|cpu=102,mem=100\n123.0|4|cpu=420,mem=400\n123.1|4|cpu=40,mem=40",
            ),
            (
                "123.batch|1|cpu=101,mem=100\n123.0|8|cpu=401,mem=400",
                "123.batch|1|cpu=102,mem=100\n123.0|8|cpu=440,mem=400",
            ),
            (
                "123.batch|1|cpu=101,mem=100\n123.0|4|cpu=1,mem=400",
                "123.batch|1|cpu=102,mem=100\n123.0|4|cpu=40,mem=400",
            ),
        ):
            with self.subTest(changed=changed):
                sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=4)
                sampler.sample(initial, 0.0, 0)
                points = self.by_name(sampler.sample(self.snapshot(changed), 10.0, 10000))
                self.assertNotIn("system.cpu.utilization", points)
                points = self.by_name(sampler.sample(self.snapshot(next_snapshot), 20.0, 20000))
                self.assertEqual(points["system.cpu.utilization"]["value"], 100)

    def test_missing_cpu_or_tasks_breaks_baseline_without_zero_filling(self):
        good = self.snapshot("123.batch|1|cpu=100,mem=100")
        for row in ("123.batch|1|mem=100", "123.batch||cpu=110,mem=100", ""):
            with self.subTest(row=row):
                sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=1)
                sampler.sample(good, 0.0, 0)
                points = self.by_name(sampler.sample(self.snapshot(row), 10.0, 10000))
                self.assertNotIn("system.cpu.utilization", points)
                points = self.by_name(sampler.sample(good, 20.0, 20000))
                self.assertNotIn("system.cpu.utilization", points)

    def test_missing_memory_does_not_hide_valid_cpu(self):
        sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=1)
        sampler.sample(self.snapshot("123.batch|1|cpu=0,mem=100"), 0.0, 0)
        points = self.by_name(sampler.sample(self.snapshot("123.batch|1|cpu=10"), 10.0, 10000))
        self.assertNotIn("system.memory.used_bytes", points)
        self.assertEqual(points["system.cpu.utilization"]["value"], 100)

    def test_read_failure_reset_prevents_bridging_an_unknown_interval(self):
        sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=1)
        sampler.sample(self.snapshot("123.batch|1|cpu=0,mem=100"), 0.0, 0)
        sampler.reset()
        points = self.by_name(sampler.sample(self.snapshot("123.batch|1|cpu=10,mem=100"), 10.0, 10000))
        self.assertNotIn("system.cpu.utilization", points)

    def test_unknown_denominator_never_uses_task_or_host_cpu_count(self):
        sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=None)
        snapshot = self.snapshot("123.batch|1|cpu=0,mem=100")
        sampler.sample(snapshot, 0.0, 0)
        points = self.by_name(sampler.sample(snapshot, 10.0, 10000))
        self.assertEqual(set(points), {"system.memory.used_bytes"})

    def test_job_identity_or_invalid_time_never_creates_cpu_delta(self):
        for job_id, timestamp in (("124", 10.0), ("123", 0.0), ("123", -1.0), ("123", float("nan"))):
            with self.subTest(job_id=job_id, timestamp=timestamp):
                sampler = METRICS.SlurmJobMetricsSampler(allocated_cpus=1)
                sampler.sample(self.snapshot("123.batch|1|cpu=0,mem=100"), 0.0, 0)
                snapshot = self.snapshot(f"{job_id}.batch|1|cpu=10,mem=100", job_id=job_id)
                points = self.by_name(sampler.sample(snapshot, timestamp, 10000))
                self.assertNotIn("system.cpu.utilization", points)


if __name__ == "__main__":
    unittest.main()
