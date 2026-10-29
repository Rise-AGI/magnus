"""Allocation/availability/history regressions with isolated Slurm fixtures."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import HTTPException

from test_gpu_resource_query import FakeTransport, Field, load_queries


SERVER = Path(__file__).resolve().parents[2] / "server"


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_queries()
        self.manager = self.module._ResourceQueryMixin()

    def task(
        self,
        **fields,
    ):
        job = {"job_id": 12, "job_state": ["RUNNING"], "node_count": 2, "cpus": 128, **fields}
        self.manager._transport = FakeTransport("", [job])
        return self.manager.get_all_running_tasks(_raise_on_error=True)[0]

    def test_integer_wrappers_and_sentinels_are_not_allocations(self):
        for value in (None, True, -1, "32", 2**32-1, 2**64-1, {"set": False, "number": 64}, {"set": True, "infinite": True, "number": 64}, {"set": True, "number": "64"}):
            with self.subTest(value=value):
                self.assertEqual(self.module._unwrap_slurm_int(value), 0)
        self.assertEqual(self.module._unwrap_slurm_int({"set": True, "infinite": False, "number": 64}), 64)

    def test_native_tres_memory_units_and_allocated_cpu_precedence(self):
        row = self.task(tres_alloc_str="cpu=256,mem=512000,node=2,gres/gpu=4,gres/gpu:a100=4")
        self.assertEqual((row["cpu_count"], row["memory_mb"]), (256, 512000))
        self.assertEqual(row["gpu_by_type"], {"a100": 4})
        for raw, expected in (("0", 0), ("1K", 1), ("1.5G", 1536), ("1T", 1048576)):
            self.assertEqual(self.module._parse_tres_mem_mb("mem="+raw), expected)
        self.assertIsNone(self.module._parse_tres_mem_mb("cpu=8"))

    def test_zero_tres_memory_does_not_trigger_request_fallback(self):
        row = self.task(tres_alloc_str="cpu=128,mem=0", memory_per_node=64000)
        self.assertEqual(row["memory_mb"], 0)
        self.assertEqual(row["memory_source"], "allocated_tres")
        self.assertTrue(row["accounting_complete"])
        from test_job_resources import load_resources
        summary = load_resources()["allocated_job_resources"](row, mode="slurm_per_cpu")
        self.assertEqual(summary["memory_bytes"], 0)
        summary = load_resources()["allocated_job_resources"]({"memory_mb": 0}, mode="slurm_per_cpu")
        self.assertIsNone(summary["memory_bytes"])

    def test_memory_fallbacks_have_explicit_provenance(self):
        row = self.task(memory_per_cpu={"set": True, "infinite": False, "number": 4000}, memory_per_node={"set": False, "number": 2**64-1})
        self.assertEqual(row["memory_mb"], 512000)
        self.assertEqual(row["memory_source"], "per_cpu_fallback")
        self.assertTrue(row["allocation_inferred"])
        from test_job_resources import load_resources
        summary = load_resources()["allocated_job_resources"](row, mode="slurm_per_cpu")
        self.assertTrue(summary["allocation_inferred"])
        self.assertEqual(summary["memory_source"], "per_cpu_fallback")
        row = self.task(memory_per_node=256000)
        self.assertEqual(row["memory_mb"], 512000)
        self.assertEqual(row["memory_source"], "per_node_fallback")

    def test_missing_memory_or_unknown_gpu_nodes_fail_strict_queries(self):
        for fields in ({"memory_per_node": 0}, {"tres_alloc_str": "cpu=128"}, {"tres_alloc_str": "cpu=128,mem=1G,gres/gpu=bad"}, {"node_count": None, "tres_alloc_str": "cpu=128,mem=1G", "tres_per_node": "gpu:a100:2"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.task(**fields)
        self.manager._transport = FakeTransport("", [{"job_id": 12, "job_state": ["RUNNING"]}])
        row = self.manager.get_all_running_tasks()[0]
        self.assertFalse(row["accounting_complete"])
        self.assertEqual(row["memory_source"], "unknown")

    def test_malformed_gpu_fallbacks_and_contradictory_totals_are_not_zero(self):
        for fields in (
            {"gres_detail": ["gpu:a100:bad"]},
            {"gres_detail": ["gpu:a100:2", "gpu:rtx:bad"]},
            {"gres_detail": {"gpu": 2}},
            {"gres_detail": [None]},
            {"tres_per_node": "gpu:a100:bad"},
            {"tres_alloc_str": "cpu=128,mem=1G,gres/gpu=1,gres/gpu:a100=2"},
            {"tres_alloc_str": "cpu=4,mem=8G,gres/gpu:a100=bad,gres/gpu:rtx5090=1"},
        ):
            with self.subTest(fields=fields), self.assertRaises((ValueError, TypeError)):
                self.task(**{"tres_alloc_str": "cpu=128,mem=1G", **fields})

    def test_nonrunning_resource_holders_remain_counted(self):
        for state in ("SUSPENDED", "STOPPED", "CONFIGURING", "POWER_UP_NODE", "RESIZING", "STAGE_OUT", "SIGNALING"):
            with self.subTest(state=state):
                row = self.task(job_state=[state], tres_alloc_str="cpu=128,mem=1G,gres/gpu=4")
                self.assertEqual((row["gpu_count"], row["memory_mb"]), (4, 1024))
                self.assertEqual(row["job_state"], state)
                self.assertEqual(row["usage_is_upper_bound"], state == "SUSPENDED")
        for state in ("PD", "R", "S", "ST", "CF", "RS", "SO", "SI"):
            self.manager._transport = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=state))
            self.assertEqual(self.manager.check_job_status("123"), "PENDING" if state == "PD" else "RUNNING")

    def test_partition_union_deduplicates_nodes_and_exposes_unavailable(self):
        nodes = "\n".join([
            "NodeName=one CPUTot=64 RealMemory=256000 Gres=gpu:a100:4 Partitions=cpu,gpu State=IDLE",
            "NodeName=two CPUTot=32 RealMemory=128000 Gres=gpu:rtx:2 Partitions=gpu State=MIXED+DRAIN",
            "NodeName=three CPUTot=64 RealMemory=256000 Gres=(null) Partitions=other State=IDLE",
            "NodeName=one CPUTot=64 RealMemory=256000 Gres=gpu:a100:4 Partitions=cpu,gpu State=IDLE",
        ])
        self.manager._transport = FakeTransport(nodes, [])
        snapshot = self.manager.get_node_snapshot(partitions=["cpu", "gpu"], _raise_on_error=True)
        self.assertEqual((snapshot.total_gpus, snapshot.cpu_total, snapshot.mem_total_mb, snapshot.node_count), (6, 96, 384000, 2))
        self.assertEqual(snapshot.gpu_by_type, {"a100": 4, "rtx": 2})
        self.assertEqual((snapshot.unavailable_gpus, snapshot.unavailable_cpus, snapshot.unavailable_mem_mb), (2, 32, 128000))
        self.assertEqual(snapshot.unavailable_gpu_by_type, {"rtx": 2})
        self.assertEqual(len(self.manager._transport.commands), 1)

    def test_partition_scope_splits_comma_separated_targets_without_mutation(self):
        config = {
            "execution": {"slurm": {"partition": "cpu,shared"}},
            "cluster": {"scheduling": {"mode": "tenant"}, "gpus": [{"partition": " shared, gpu "}, {"partition": "gpu,extra"}]},
        }
        self.assertEqual(self.module.resource_partitions(config), ["cpu", "shared", "gpu", "extra"])
        self.assertEqual(config["execution"]["slurm"]["partition"], "cpu,shared")

    def test_reserved_capacity_is_not_advertised_as_schedulable(self):
        self.manager._transport = FakeTransport("NodeName=one CPUTot=64 CPUEfctv=60 RealMemory=256000 MemSpecLimit=4000 Gres=gpu:2 State=IDLE", [])
        snapshot = self.manager.get_node_snapshot(_raise_on_error=True)
        self.assertEqual((snapshot.cpu_total, snapshot.mem_total_mb), (64, 256000))
        self.assertEqual((snapshot.unavailable_cpus, snapshot.unavailable_mem_mb), (4, 4000))
        self.assertEqual((snapshot.nodes[0].cpu_total, snapshot.nodes[0].mem_total_mb), (60, 252000))
        self.assertEqual(snapshot.gpu_by_type, {"unknown": 2})

    def test_availability_flags_do_not_block_queueing_on_healthy_reserved_nodes(self):
        for state in ("IDLE+POWER_DOWN", "IDLE+NO_RESPOND", "ALLOCATED+NOT_RESPONDING", "IDLE+POWERING_UP"):
            with self.subTest(state=state):
                self.manager._transport = FakeTransport(f"NodeName=one State={state} CPUTot=32 RealMemory=64000 Gres=gpu:a100:2", [])
                snapshot = self.manager.get_node_snapshot(_raise_on_error=True)
                self.assertEqual((snapshot.unavailable_gpus, snapshot.unavailable_cpus, snapshot.unavailable_mem_mb), (2, 32, 64000))
                self.assertFalse(self.manager.has_schedulable_node())
        for state in ("IDLE+PLANNED", "IDLE+RESERVED", "MIXED+BLOCKED", "PLANNED", "RESERVED"):
            with self.subTest(state=state):
                self.manager._transport = FakeTransport(f"NodeName=one State={state} CPUTot=32 RealMemory=64000 Gres=gpu:a100:2", [])
                snapshot = self.manager.get_node_snapshot(_raise_on_error=True)
                self.assertEqual((snapshot.unavailable_gpus, snapshot.unavailable_cpus), (2, 32))
                self.assertTrue(self.manager.has_schedulable_node())

    def test_public_partition_snapshot_normalizes_comma_lists(self):
        self.manager._transport = FakeTransport("\n".join([
            "NodeName=one State=IDLE CPUTot=32 RealMemory=64000 Gres=(null) Partitions=p1,p2",
            "NodeName=two State=IDLE CPUTot=32 RealMemory=64000 Gres=(null) Partitions=p2",
        ]), [])
        snapshot = self.manager.get_partition_snapshot(" p1, p2,p1 ", _raise_on_error=True)
        self.assertEqual((snapshot.node_count, snapshot.cpu_total), (2, 64))

    def test_hidden_allocation_gap_fails_but_cleared_node_counters_do_not(self):
        self.manager._transport = FakeTransport("NodeName=one State=IDLE CPUTot=32 RealMemory=64000 Gres=gpu:a100:4 AllocTRES=cpu=8,mem=8G,gres/gpu=2", [])
        snapshot = self.manager.get_node_snapshot(_raise_on_error=True)
        with self.assertRaises(ValueError):
            self.module.validate_allocation_visibility(snapshot, [])
        self.module.validate_allocation_visibility(snapshot, [{"gpu_count": 2, "cpu_count": 8, "memory_mb": 8192}])
        self.module.validate_allocation_visibility(self.module.NodeSnapshot(4, 32, 64000, observed_allocated_gpus=0), [{"gpu_count": 2}])
        gpu_snapshot = self.module.NodeSnapshot(4, 32, 64000, observed_allocated_gpus=2, observed_allocated_cpus=16)
        with self.assertRaisesRegex(ValueError, "cpu_count"):
            self.module.validate_allocation_visibility(gpu_snapshot, [{"gpu_count": 2}])
        self.module.validate_allocation_visibility(gpu_snapshot, [{"gpu_count": 2}], resource_fields=("gpu_count",))
        with self.assertRaisesRegex(ValueError, "gpu_count"):
            self.module.validate_allocation_visibility(gpu_snapshot, [], resource_fields=("gpu_count",))

    def test_untyped_actual_gpu_does_not_inherit_ui_alias(self):
        row = self.task(tres_alloc_str="cpu=128,mem=1G,gres/gpu=4")
        self.assertEqual(row["gpu_by_type"], {"unknown": 4})

    def test_hostlists_scope_proof_and_node_filter_include_other_partitions(self):
        expand = self.module._expand_node_names
        self.assertEqual(expand("rack[1-2]n[01-02],other"), ["rack1n01", "rack1n02", "rack2n01", "rack2n02", "other"])
        self.assertIsNone(expand("node[bad]"))
        self.manager._transport = FakeTransport("", [
            {"job_id": 12, "job_state": ["RUNNING"], "partition": "other", "nodes": "node[01-02]", "node_count": 2, "tres_alloc_str": "cpu=128,mem=1G"},
        ])
        row = self.manager.get_all_running_tasks(node_names=["node01"], _raise_on_error=True)[0]
        self.assertFalse(row["scope_is_subset"])
        self.assertIn("--nodelist=node01", self.manager._transport.commands[-1])
        self.assertFalse(any(arg.startswith("--partition") for arg in self.manager._transport.commands[-1]))
        row = self.manager.get_all_running_tasks(node_names=["node01", "node02"], _raise_on_error=True)[0]
        self.assertTrue(row["scope_is_subset"])

    def test_empty_or_invalid_node_capacity_fails_strict(self):
        for nodes in ("", "NodeName=one CPUTot=bad RealMemory=32000", "NodeName=one CPUTot=32 RealMemory=32000 Gres=gpu:bad", "NodeName=one CPUTot=32 RealMemory=32000 Gres=gpu:a100:bad,gpu:rtx5090:4"):
            self.manager._transport = FakeTransport(nodes, [])
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                self.manager.get_node_snapshot(_raise_on_error=True)

    def test_ssh_exit_255_empty_stdout_is_unknown_not_completed(self):
        self.manager._transport = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(returncode=255, stdout=""))
        self.assertEqual(self.manager.check_job_status("123"), "UNKNOWN")
        self.manager._transport = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))
        self.assertEqual(self.manager.check_job_status("123"), "COMPLETED")


class ClusterCardAccountingTests(unittest.TestCase):
    def setUp(self):
        self.module = load_queries()
        self.manager = self.module._ResourceQueryMixin()
        source = SERVER / "routers" / "cluster.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if getattr(node, "name", None) == "_cluster_resource_totals")
        namespace = {}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)
        self.totals = namespace["_cluster_resource_totals"]

    def snapshot(self, nodes):
        self.manager._transport = FakeTransport(nodes, [])
        return self.manager.get_node_snapshot(_raise_on_error=True)

    def test_draining_allocations_are_counted_once(self):
        snapshot = self.snapshot(
            "NodeName=one State=MIXED+DRAIN CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 AllocTRES=cpu=12,mem=32G,gres/gpu=3\n"
            "NodeName=two State=IDLE CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 GresUsed=gpu:a100:0(IDX:N/A) CPUAlloc=0 AllocMem=0 AllocTRES=\n"
        )
        self.assertEqual(snapshot.unavailable_gpus, 4)
        result = self.totals(snapshot)
        self.assertEqual((result["gpu_used"], result["free"], result["total"]), (3, 5, 8))
        self.assertEqual((result["cpu_used"], result["cpu_free"], result["cpu_total"]), (12, 20, 32))
        self.assertEqual((result["mem_used_mb"], result["mem_free_mb"]), (32768, 98304))
        self.assertIs(result["allocation_tracked"], True)

    def test_unknown_dimension_does_not_hide_known_dimensions_or_become_zero(self):
        snapshot = self.snapshot(
            "NodeName=one State=MIXED CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 AllocTRES=cpu=4,mem=16G,gres/gpu=2\n"
            "NodeName=two State=IDLE CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 GresUsed=gpu:a100:0(IDX:N/A) AllocMem=0 AllocTRES=\n"
        )
        result = self.totals(snapshot)
        self.assertIsNone(result["cpu_used"])
        self.assertEqual(result["gpu_used"], 2)
        self.assertEqual(result["mem_used_mb"], 16384)
        self.assertIs(result["allocation_tracked"], False)

    def test_reserved_idle_capacity_is_not_misreported_as_allocated(self):
        snapshot = self.snapshot("NodeName=one State=RESERVED CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 GresUsed=gpu:a100:0(IDX:N/A) CPUAlloc=0 AllocMem=0 AllocTRES=")
        result = self.totals(snapshot)
        self.assertEqual((result["gpu_used"], result["cpu_used"], result["mem_used_mb"]), (0, 0, 0))
        self.assertEqual((result["free"], result["cpu_free"], result["mem_free_mb"]), (4, 16, 65536))
        self.assertFalse(snapshot.nodes[0].schedulable)

    def test_empty_node_observation_is_unknown_not_fully_free(self):
        result = self.totals(self.module.NodeSnapshot(8, 128, 65536))
        self.assertIsNone(result["gpu_used"])
        self.assertIsNone(result["cpu_used"])
        self.assertIsNone(result["mem_used_mb"])
        self.assertIs(result["allocation_tracked"], False)

    def test_gres_used_counts_busy_gpus_without_gpu_tres_accounting(self):
        snapshot = self.snapshot(
            "NodeName=one State=MIXED CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 "
            "GresUsed=gpu:a100:2(IDX:0,2) AllocTRES=cpu=8,mem=16G"
        )
        self.assertIn("--details", self.manager._transport.commands[0])
        result = self.totals(snapshot)
        self.assertEqual(result["gpu_used"], 2)
        self.assertIs(result["allocation_tracked"], True)

    def test_untyped_gres_used_preserves_null_model_placeholder(self):
        snapshot = self.snapshot(
            "NodeName=one State=ALLOCATED CPUTot=40 RealMemory=321200 Gres=gpu:4 "
            "GresUsed=gpu:(null):4(IDX:0-3) AllocTRES=cpu=40,mem=321200M,gres/gpu=4"
        )
        result = self.totals(snapshot)
        self.assertEqual((result["gpu_used"], result["free"]), (4, 0))
        self.assertIs(result["allocation_tracked"], True)

    def test_missing_gpu_evidence_is_unknown_while_other_dimensions_are_known(self):
        snapshot = self.snapshot(
            "NodeName=one State=MIXED CPUTot=16 RealMemory=65536 Gres=gpu:a100:4 "
            "AllocTRES=cpu=8,mem=16G"
        )
        result = self.totals(snapshot)
        self.assertIsNone(result["gpu_used"])
        self.assertEqual((result["cpu_used"], result["mem_used_mb"]), (8, 16384))
        self.assertIs(result["allocation_tracked"], False)

    def test_explicit_zero_gpu_tres_and_gpu_less_nodes_are_known_zero(self):
        for resources in ("Gres=gpu:a100:4 AllocTRES=cpu=8,mem=16G,gres/gpu=0", "Gres=(null) AllocTRES=cpu=8,mem=16G"):
            with self.subTest(resources=resources):
                snapshot = self.snapshot("NodeName=one State=MIXED CPUTot=16 RealMemory=65536 " + resources)
                result = self.totals(snapshot)
                self.assertEqual(result["gpu_used"], 0)
                self.assertIs(result["allocation_tracked"], True)


class CacheAndHistoryTests(unittest.TestCase):
    def test_cluster_query_failure_is_503_before_returning_fake_free_resources(self):
        path = SERVER / "routers" / "cluster.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_cluster_stats")
        route.decorator_list = []
        def failed(*args):
            raise OSError("SSH disconnected")
        report = Mock()
        namespace = {
            "Depends": lambda dependency: None, "database": SimpleNamespace(get_db=None), "get_current_user": None,
            "SlurmManager": lambda **kwargs: object(), "is_local_mode": False, "resource_partitions": lambda config: [],
            "_resolve_target": lambda target_id: SimpleNamespace(id="default", config={}),
            "magnus_config": {"server": {"scheduler": {"cluster_stats_cache_ttl": 0}}},
            "_cached_slurm_view": failed, "HTTPException": HTTPException,
            "_report_cluster_query_failure": report,
        }
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(path), "exec"), namespace)
        with self.assertRaises(HTTPException) as context:
            namespace["get_cluster_stats"]()
        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(
            context.exception.detail,
            "Slurm resource accounting is temporarily unavailable",
        )
        report.assert_called_once_with(
            "default",
            context.exception.__cause__,
        )

    def test_failure_diagnostics_are_safe_and_rate_limited_per_target(self):
        path = SERVER / "routers" / "cluster.py"
        tree = ast.parse(
            path.read_text(
                encoding="utf-8",
            ),
        )
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_report_cluster_query_failure"
        )
        clock = SimpleNamespace(
            value=0,
        )
        logger = Mock()
        namespace = {
            "time": SimpleNamespace(monotonic=lambda: clock.value),
            "os": os,
            "logger": logger,
            "_cluster_failure_log_times": {},
            "_cluster_failure_log_lock": threading.Lock(),
        }
        exec(
            compile(
                ast.Module(
                    body=[function],
                    type_ignores=[],
                ),
                str(
                    path,
                ),
                "exec",
            ),
            namespace,
        )
        report = namespace["_report_cluster_query_failure"]
        try:
            raise subprocess.CalledProcessError(
                255,
                ["ssh", "synthetic-secret"],
                output="private-response",
                stderr="private-response",
            )
        except subprocess.CalledProcessError as error:
            failure = error
        threads = [
            threading.Thread(
                target=report,
                args=("first", failure),
            )
            for _ in range(16)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(
                timeout=2,
            )
            self.assertFalse(
                thread.is_alive(),
            )
        logger.warning.assert_called_once()
        report(
            "second",
            failure,
        )
        clock.value = 60
        report(
            "first",
            failure,
        )
        self.assertEqual(
            logger.warning.call_count,
            3,
        )
        template, *arguments = logger.warning.call_args.args
        message = template % tuple(
            arguments,
        )
        self.assertIn(
            "error=CalledProcessError returncode=255",
            message,
        )
        self.assertIn(
            "test_cluster_accounting.py:",
            message,
        )
        for private in ("synthetic-secret", "private-response"):
            self.assertNotIn(
                private,
                message,
            )

    def test_cache_has_bounded_staleness_and_query_failures_propagate(self):
        path = SERVER / "routers" / "cluster.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_cached_slurm_view")
        clock = SimpleNamespace(value=0)
        lock = threading.Lock()
        namespace = {"time": SimpleNamespace(monotonic=lambda: clock.value), "_slurm_view_cache": {}, "_slurm_view_cache_lock": threading.Lock(), "_slurm_view_refresh_locks": {"site": lock}, "threading": threading}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        cached = namespace["_cached_slurm_view"]
        self.assertEqual(cached("site", 5, lambda: "sample"), "sample")
        lock.acquire()
        try:
            clock.value = 10
            self.assertEqual(cached("site", 5, lambda: "unreachable"), "sample")
            clock.value = 36
            with self.assertRaises(RuntimeError):
                cached("site", 5, lambda: "unreachable")
        finally:
            lock.release()
        def failed():
            raise OSError("SSH disconnected")
        with self.assertRaises(OSError):
            cached("site", 5, failed)

    def test_history_matches_actual_allocation_by_id_without_status_filter(self):
        path = SERVER / "_scheduler" / "_sync.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_SyncMixin")
        function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_record_snapshot")
        module = load_queries()
        config = {"execution": {"slurm": {"partition": "cpu"}}, "cluster": {"scheduling": {"mode": "tenant"}, "gpus": [{"partition": "gpu"}]}, "server": {"scheduler": {"snapshot_interval": 1}}}
        added = []
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def query(self, field): return self
            def filter(self, *args): return self
            def distinct(self): return self
            def all(self): return [("queued",), ("releasing",)]
            def add(self, value): added.append(value)
            def commit(self): pass
        calls = []
        def tasks(**kwargs):
            calls.append(kwargs)
            return [{"id": "queued", "gpu_count": 6}, {"id": "releasing", "gpu_count": 2}, {"id": "external", "gpu_count": 4}]
        manager = SimpleNamespace(get_all_running_tasks=tasks, get_node_snapshot=lambda **kwargs: module.NodeSnapshot(16, 128, 512000, nodes=(SimpleNamespace(name="one", gpu_by_type={"a100": 16}, allocated_gpus=12),), observed_allocated_gpus=12, observed_allocated_cpus=128, observed_allocated_mem_mb=512000))
        instance = SimpleNamespace(config=config, target=SimpleNamespace(id="default"), _job_query=lambda db, *entities: db.query(*entities), slurm_manager=manager, last_snapshot_time=datetime.now(timezone.utc)-timedelta(seconds=10))
        namespace = {"datetime": datetime, "timezone": timezone, "is_local_mode": False, "magnus_config": config, "resource_partitions": module.resource_partitions, "validate_allocation_visibility": module.validate_allocation_visibility, "SessionLocal": Session, "Job": SimpleNamespace(slurm_job_id=Field()), "ClusterSnapshot": lambda **fields: SimpleNamespace(**fields), "logger": logging.getLogger("history-test")}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        namespace["_record_snapshot"](instance)
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0].total_gpus, added[0].slurm_used_gpus, added[0].magnus_used_gpus), (16, 12, 8))
        self.assertEqual(calls[0], {"node_names": ["one"], "_raise_on_error": True})
        previous = instance.last_snapshot_time = datetime.now(timezone.utc)-timedelta(seconds=10)
        failed_calls = []
        def failed(**kwargs):
            failed_calls.append(kwargs)
            raise OSError("SSH disconnected")
        manager.get_all_running_tasks = failed
        with self.assertLogs("history-test", level="ERROR"):
            namespace["_record_snapshot"](instance)
        self.assertEqual(len(added), 1)
        self.assertGreater(instance.last_snapshot_time, previous)
        self.assertEqual(len(failed_calls), 1)
        namespace["_record_snapshot"](instance)
        self.assertEqual(len(failed_calls), 1)
        instance.last_snapshot_time = previous
        manager.get_all_running_tasks = lambda **kwargs: [{"id": "crossing", "gpu_count": 12, "scope_is_subset": False}]
        with self.assertLogs("history-test", level="WARNING") as captured:
            namespace["_record_snapshot"](instance)
        self.assertIn("cross the selected node scope", captured.output[0])
        self.assertEqual(captured.records[0].levelname, "WARNING")
        self.assertEqual(len(added), 1)
        self.assertGreater(instance.last_snapshot_time, previous)
        manager.get_all_running_tasks = lambda **kwargs: []
        unknown = SimpleNamespace(name="hidden", gpu_by_type={"a100": 4}, allocated_gpus=None)
        known = SimpleNamespace(name="known", gpu_by_type={"a100": 4}, allocated_gpus=0)
        for nodes in ((unknown,), (known, unknown)):
            with self.subTest(nodes=len(nodes)):
                manager.get_node_snapshot = lambda **kwargs: module.NodeSnapshot(4 * len(nodes), 128, 512000, nodes=nodes, observed_allocated_gpus=0, observed_allocated_cpus=128)
                instance.last_snapshot_time = previous
                with self.assertLogs("history-test", level="WARNING") as captured:
                    namespace["_record_snapshot"](instance)
                self.assertIn("GPU node allocation is unknown", captured.output[0])
                self.assertEqual(len(added), 1)
        cpu_node = SimpleNamespace(name="cpu", gpu_by_type={}, allocated_gpus=0)
        manager.get_node_snapshot = lambda **kwargs: module.NodeSnapshot(0, 128, 512000, nodes=(cpu_node,), observed_allocated_gpus=0, observed_allocated_cpus=128)
        instance.last_snapshot_time = previous
        namespace["_record_snapshot"](instance)
        self.assertEqual((added[-1].total_gpus, added[-1].slurm_used_gpus, added[-1].magnus_used_gpus), (0, 0, 0))
        self.assertEqual(len(added), 2)


if __name__ == "__main__":
    unittest.main()
