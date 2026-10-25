"""Admission/preemption regressions without scheduler startup or real jobs."""
from __future__ import annotations

import ast
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest


BACKEND = Path(__file__).resolve().parents[2]


def load_accounting():
    namespace = {"__name__": __name__}
    for path in (
        BACKEND / "library/fundamental/scheduling.py",
        BACKEND / "server/_size_utils.py",
        BACKEND / "server/_job_resources.py",
        BACKEND / "server/_scheduler/_resource_accounting.py",
        BACKEND / "server/_slurm_manager/_control.py",
        BACKEND / "server/_scheduler/_decisions.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [
            node for node in tree.body
            if not isinstance(node, ast.ImportFrom) or (
                not node.level
                and not (node.module or "").startswith(("library.", "sqlalchemy"))
            )
        ]
        exec(compile(tree, str(path), "exec"), namespace)
    path = BACKEND / "server/_slurm_manager/_resource_query.py"
    visibility = next(node for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name == "validate_allocation_visibility")
    exec(compile(ast.Module(body=[visibility], type_ignores=[]), str(path), "exec"), namespace)
    namespace["logger"] = logging.getLogger("scheduler_resource_accounting_tests")
    for name in ("_ControlMixin", "_DecisionsMixin"):
        namespace[name].config = property(lambda self: namespace["magnus_config"])
    namespace["_DecisionsMixin"]._job_query = staticmethod(lambda db, *entities: db.query(*entities))
    return namespace


def configuration(
    mode="explicit",
):
    return {
        "execution": {"backend": "slurm", "slurm": {
            "mem_mode": mode, "mem_per_cpu_mb": 4000, "partition": "compute",
        }},
        "cluster": {"gpus": [
            {"value": "a100-alias", "gres_type": "a100", "partition": "compute"},
            {"value": "untyped", "gres_type": None, "partition": "compute"},
        ]},
    }


def job(
    **overrides,
):
    values = dict(
        id="head", cpu_count=1, memory_demand="4000M", gpu_count=0,
        gpu_type="cpu", node_count=1, tasks_per_node=1, job_type="B1",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def node(
    name="one",
    **overrides,
):
    values = dict(
        name=name, partitions=("compute",), schedulable=True,
        cpu_total=8, mem_total_mb=32000, gpu_by_type={"a100": 2, "rtx5090": 4},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def allocation(
    **overrides,
):
    values = dict(
        id="running", cpu_count=4, memory_mb=16000, gpu_count=0,
        gpu_by_type={}, node_count=1, accounting_complete=True,
    )
    values.update(overrides)
    return values


class ResourceVectorTests(unittest.TestCase):
    def setUp(self):
        self.module = load_accounting()
        self.config = configuration()
        self.vector = self.module["ResourceVector"]
        self.candidate = self.module["BackfillCandidate"]

    def request(
        self,
        request,
    ):
        return self.module["job_resource_vector"](request, self.config, ("a100", "rtx5090"))

    def test_parallel_per_cpu_request_matches_the_128_task_job(self):
        self.config = configuration("per_cpu")
        self.assertEqual(
            self.request(job(node_count=2, tasks_per_node=64)).components,
            (0, 128, 512000, 0, 0),
        )

    def test_explicit_memory_and_gpus_multiply_nodes_only(self):
        self.assertEqual(
            self.request(job(
                node_count=3, tasks_per_node=4, cpu_count=2,
                memory_demand="16G", gpu_count=2, gpu_type="a100-alias",
            )).components,
            (6, 24, 49152, 6, 0),
        )

    def test_current_per_cpu_policy_is_applied_before_admission(self):
        self.config = configuration("per_cpu")
        self.assertEqual(
            self.request(job(
                cpu_count=1, memory_demand="6000M", node_count=2, tasks_per_node=3,
                resource_mode="slurm_per_node",
            )).components,
            (0, 12, 48000, 0, 0),
        )

    def test_gres_alias_and_untyped_partition_are_not_ui_names(self):
        self.assertEqual(self.request(job(gpu_count=1, gpu_type="a100-alias")).components[3:], (1, 0))
        self.assertEqual(self.request(job(gpu_count=1, gpu_type="untyped")).components[3:], (0, 0))
        self.config["cluster"]["gpus"][0]["partition"] = "accelerated"
        target = self.module["job_resource_target"](job(gpu_count=1, gpu_type="a100-alias"), self.config)
        self.assertEqual((target.partition, target.gpu_type), ("accelerated", "a100"))

    def test_unknown_or_all_memory_cannot_be_a_zero_demand(self):
        for memory in (None, "0", "0M"):
            with self.subTest(memory=memory):
                self.assertIsNone(self.request(job(memory_demand=memory)))

    def test_mib_rounding_never_undercounts_a_fractional_request(self):
        self.assertEqual(self.request(job(memory_demand="4000.5M")).components[2], 4001)

    def test_actual_allocation_is_not_multiplied_again(self):
        result = self.module["allocation_resource_vector"](
            allocation(cpu_count=128, memory_mb=512000, node_count=2),
            ("a100", "rtx5090"),
        )
        self.assertEqual(result.components, (0, 128, 512000, 0, 0))

    def test_unknown_gpu_can_reduce_free_but_cannot_prove_typed_recovery(self):
        task = allocation(gpu_count=1, gpu_by_type={"unknown": 1})
        recover = self.module["allocation_resource_vector"](task, ("a100", "rtx5090"))
        charge = self.module["allocation_resource_vector"](task, ("a100", "rtx5090"), charge_unknown_types=True)
        self.assertEqual(recover.components[3:], (0, 0))
        self.assertEqual(charge.components[3:], (1, 1))

    def test_preemption_does_not_kill_the_wrong_gpu_model(self):
        head = self.vector((1, 1, 1, 1, 0))
        total = self.vector((6, 8, 32000, 2, 4))
        free = self.vector((0, 7, 30000, 0, 0))
        candidates = [
            self.candidate("wrong-type", self.vector((4, 1, 1000, 0, 4))),
            self.candidate("right-type", self.vector((2, 1, 1000, 2, 0))),
        ]
        self.assertEqual(
            self.module["select_preemption_victims"](head, total, free, candidates),
            ["right-type"],
        )

    def test_preemption_requires_all_cpu_memory_and_gpu_deficits(self):
        head = self.vector((1, 8, 16000, 1))
        total = self.vector((2, 16, 32000, 2))
        free = self.vector((0, 0, 0, 0))
        gpu = self.candidate("gpu", self.vector((1, 1, 1000, 1)))
        cpu = self.candidate("cpu", self.vector((0, 7, 15000, 0)))
        select = self.module["select_preemption_victims"]
        self.assertEqual(select(head, total, free, [gpu]), [])
        self.assertEqual(select(head, total, free, [gpu, cpu]), ["gpu", "cpu"])

    def test_down_nodes_are_not_available_capacity(self):
        result = self.module["node_resource_vector"](
            [node(), node("down", schedulable=False)],
            ("a100", "rtx5090"),
        )
        self.assertEqual(result.components, (6, 8, 32000, 2, 4))


class Field:
    def __eq__(
        self,
        other,
    ):
        return self

    def in_(
        self,
        values,
    ):
        return self


class QueueDB:
    def __init__(
        self,
        queued,
    ):
        self.queued = queued

    def query(self, *args):
        return self

    def filter(self, *args):
        return self

    def all(self):
        return [(job_id,) for job_id in self.queued]


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_accounting()
        self.module.update(
            magnus_config=configuration(),
            Job=SimpleNamespace(slurm_job_id=Field(), status=Field()),
            JobStatus=SimpleNamespace(QUEUED="Queued"),
            JobType=SimpleNamespace(A1="A1", A2="A2"),
        )
        self.manager = self.module["_DecisionsMixin"]()
        self.preempted = False

        def preempt(**kwargs):
            self.preempted = True
            return True

        self.manager._handle_preemption_for_job = preempt

    def choose(
        self,
        jobs,
        *,
        nodes=None,
        tasks=None,
        queued=(),
        error=None,
        observed_cpus=0,
    ):
        nodes = nodes if nodes is not None else [node()]
        tasks = tasks if tasks is not None else []
        types = {}
        for item in nodes:
            for kind, count in item.gpu_by_type.items():
                types[kind] = types.get(kind, 0) + count
        snapshot = SimpleNamespace(
            nodes=nodes, node_count=len(nodes), gpu_by_type=types,
            observed_allocated_cpus=observed_cpus,
            observed_allocated_gpus=0, observed_allocated_mem_mb=0,
        )

        def get_snapshot(**kwargs):
            self.assertTrue(kwargs["_raise_on_error"])
            if error is not None:
                raise error
            return snapshot

        def get_tasks(**kwargs):
            self.assertTrue(kwargs["_raise_on_error"])
            return tasks

        self.manager.slurm_manager = SimpleNamespace(
            get_node_snapshot=get_snapshot,
            get_all_running_tasks=get_tasks,
        )
        return self.manager._select_authoritative_jobs(QueueDB(queued), jobs)

    def test_submitted_but_unobserved_job_blocks_new_promises(self):
        self.assertEqual(self.choose([job()], queued=["already-submitted"]), [])
        self.assertFalse(self.preempted)

    def test_hidden_allocations_cannot_authorize_admission(self):
        with self.assertLogs("scheduler_resource_accounting_tests", level="WARNING"):
            self.assertEqual(self.choose([job()], observed_cpus=4), [])
        self.assertFalse(self.preempted)

    def test_healthy_reserved_nodes_can_queue_head_without_advertising_free(self):
        from test_gpu_resource_query import load_queries
        queries = load_queries()
        for state in ("IDLE+PLANNED", "IDLE+RESERVED", "IDLE+BLOCKED", "IDLE+POWER_DOWN"):
            with self.subTest(state=state):
                query = queries._ResourceQueryMixin()
                query._transport = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(
                    returncode=0,
                    stdout=f"NodeName=one Partitions=compute State={state} CPUTot=8 RealMemory=32000 Gres=gpu:a100:2,gpu:rtx5090:4",
                ))
                snapshot = query.get_node_snapshot(_raise_on_error=True)
                self.assertEqual(self.module["node_resource_vector"](snapshot.nodes, ()).components, (0, 0, 0))
                selected = self.choose([job(job_type="A1"), job(id="later")], nodes=snapshot.nodes)
                self.assertEqual(selected, [] if state.endswith("POWER_DOWN") else ["head"])
                self.assertFalse(self.preempted)

    def test_observed_queued_allocation_is_counted_once(self):
        self.assertEqual(
            self.choose([job(cpu_count=4)], tasks=[allocation()], queued=["running"]),
            ["head"],
        )
        self.assertEqual(
            self.choose([job(cpu_count=5)], tasks=[allocation()], queued=["running"]),
            [],
        )

    def test_failed_or_incomplete_queries_never_mean_free(self):
        with self.assertLogs("scheduler_resource_accounting_tests", level="WARNING"):
            self.assertEqual(self.choose([job()], error=OSError("offline")), [])
        with self.assertLogs("scheduler_resource_accounting_tests", level="WARNING"):
            self.assertEqual(self.choose([job()], tasks=[allocation(accounting_complete=False)]), [])

    def test_multinode_topology_delegates_only_head_without_killing(self):
        jobs = [job(job_type="A1", cpu_count=8), job(id="later")]
        self.assertEqual(self.choose(jobs, nodes=[node(), node("two")]), ["head"])
        self.assertFalse(self.preempted)

    def test_inferred_allocation_cannot_authorize_backfill_or_preemption(self):
        jobs = [job(job_type="A1"), job(id="later")]
        self.assertEqual(
            self.choose(jobs, tasks=[allocation(allocation_inferred=True)]),
            ["head"],
        )
        self.assertFalse(self.preempted)

    def test_oversubscribed_cpus_do_not_credit_a_victim_without_resource_debt(self):
        jobs = [job(job_type="A1", cpu_count=4), job(id="later")]
        tasks = [
            allocation(id="first", cpu_count=8, memory_mb=1000),
            allocation(id="second", cpu_count=8, memory_mb=1000),
        ]
        self.assertEqual(self.choose(jobs, tasks=tasks), ["head"])
        self.assertFalse(self.preempted)

    def test_conservative_unknown_gpu_charge_cannot_authorize_kills(self):
        jobs = [job(job_type="A1", gpu_count=1, gpu_type="a100-alias"), job(id="later")]
        tasks = [allocation(gpu_count=3, gpu_by_type={"unknown": 3})]
        self.assertEqual(self.choose(jobs, tasks=tasks), ["head"])
        self.assertFalse(self.preempted)

    def test_held_nonrunning_allocation_does_not_authorize_speculative_kills(self):
        for state in ("SUSPENDED", "STOPPED", "CONFIGURING", "COMPLETING"):
            with self.subTest(state=state):
                self.assertEqual(
                    self.choose([job(job_type="A1"), job(id="later")], tasks=[allocation(job_state=state)]),
                    ["head"],
                )
                self.assertFalse(self.preempted)

    def test_partition_capacity_cannot_be_borrowed_from_another_partition(self):
        self.assertEqual(
            self.choose([job(cpu_count=9)], nodes=[node(), node("other", partitions=("other",))]),
            [],
        )

    def test_unknown_memory_sends_only_head_and_never_preempts(self):
        self.assertEqual(self.choose([job(memory_demand="0", job_type="A1"), job(id="later")]), ["head"])
        self.assertFalse(self.preempted)

    def test_request_for_more_nodes_than_partition_is_not_admitted(self):
        self.assertEqual(self.choose([job(node_count=2)]), [])


class DecisionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_accounting()
        self.module.update(
            magnus_config=configuration(),
            Job=SimpleNamespace(status=Field(), job_type=Field()),
            JobStatus=SimpleNamespace(PENDING="Pending", RUNNING="Running"),
            JobType=SimpleNamespace(A1="A1", A2="A2", B1="B1", B2="B2"),
            job_metadata_load_options=lambda: (),
        )
        self.manager = self.module["_DecisionsMixin"]()
        self.vector = self.module["ResourceVector"]

    def test_preemption_reads_victim_allocation_not_requested_gpu_count(self):
        head = job(status="Pending", job_type="A1")
        victim = job(
            id="victim", gpu_count=99, cpu_count=99, memory_demand="999G",
            slurm_job_id="running", status="Running", job_type="B2", start_time=None,
        )

        class Session:
            def query(self, *args):
                return self

            def options(self, *args):
                return self

            def filter(self, *args):
                return self

            def all(self):
                return [victim]

            def refresh(self, *args, **kwargs):
                pass

        killed = []
        self.manager._kill_and_pause = lambda db, row: killed.append(row.id)
        args = dict(
            db=Session(), job=head, demand=self.vector((0, 8, 24000)),
            total=self.vector((6, 8, 32000)), free=self.vector((6, 0, 0)),
            tasks=[allocation(cpu_count=1, memory_mb=4000)], gpu_types=(),
        )
        self.assertFalse(self.manager._handle_preemption_for_job(**args))
        self.assertEqual(killed, [])
        args["tasks"] = [allocation(cpu_count=8, memory_mb=32000)]
        self.assertTrue(self.manager._handle_preemption_for_job(**args))
        self.assertEqual(killed, ["victim"])
        killed.clear()

        def reprioritize(
            row,
            **kwargs,
        ):
            if row is victim:
                row.job_type = "A1"

        args["db"].refresh = reprioritize
        self.assertFalse(self.manager._handle_preemption_for_job(**args))
        self.assertEqual(killed, [])

    def test_tenant_and_local_dispatch_do_not_enter_authoritative_audit(self):
        jobs = [
            job(id="lower", job_type="B2", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)),
            job(id="higher", job_type="A1", created_at=datetime(2020, 1, 2, tzinfo=timezone.utc)),
        ]

        class Session:
            def query(self, *args):
                return self

            def options(self, *args):
                return self

            def filter(self, *args):
                return self

            def all(self):
                return jobs[:]

        self.module["SessionLocal"] = lambda **kwargs: nullcontext(Session())
        self.module["magnus_config"]["cluster"]["scheduling"] = {"mode": "tenant"}
        submitted = []
        self.manager._submit_jobs = lambda ids: submitted.append(ids)
        self.manager._select_authoritative_jobs = lambda *args: self.fail("unexpected authoritative path")
        self.module["is_local_mode"] = False
        self.manager._schedule_pending_jobs()
        self.assertEqual(submitted, [["higher", "lower"]])
        self.module["is_local_mode"] = True
        self.manager._schedule_pending_jobs()
        self.assertEqual(submitted[-1], ["higher"])


class SubmissionTargetTests(unittest.TestCase):
    def test_captured_sbatch_target_matches_admission_for_legacy_and_typed_routes(self):
        cases = [
            (0, "a100-alias", {"value": "a100-alias", "gres_type": "A100", "partition": "gpu"}, "compute", None),
            (2, "a100-alias", {"value": "a100-alias", "gres_type": "A100", "partition": "gpu"}, "gpu", "gpu:A100:2"),
            (1, "untyped", {"value": "untyped", "gres_type": None, "partition": "gpu"}, "gpu", "gpu:1"),
            (1, "legacy", {"value": "legacy"}, "compute", "gpu:1"),
            (1, "normalized", {"value": "normalized", "gres_type": "normalized"}, "compute", "gpu:normalized:1"),
            (1, "fallback", None, "compute", "gpu:fallback:1"),
            (1, None, None, "compute", "gpu:1"),
            (1, "cpu", None, "compute", "gpu:1"),
        ]
        for count, kind, entry, partition, gres in cases:
            with self.subTest(count=count, kind=kind, entry=entry):
                module = load_accounting()
                config = configuration()
                config["cluster"]["gpus"] = [entry] if entry is not None else []
                config["execution"]["slurm"].update(module_loads=[], qos=None, account=None)
                module["magnus_config"] = config
                commands = []

                def run(
                    command,
                    **kwargs,
                ):
                    commands.append(command)
                    return SimpleNamespace(stdout="123\n")

                control = module["_ControlMixin"]()
                control._transport = SimpleNamespace(run=run)
                self.assertEqual(control.submit_job_simple(
                    entry_command="python3 wrapper.py",
                    gpus=count,
                    gpu_type=kind,
                    job_name="fixture",
                    runner="fixture",
                    token="fixture",
                ), "123")
                command = commands[0]
                self.assertIn(f"--partition={partition}", command)
                actual_gres = [value for value in command if value.startswith("--gres=")]
                self.assertEqual(actual_gres, [f"--gres={gres}"] if gres else [])
                target = module["job_resource_target"](job(gpu_count=count, gpu_type=kind), config)
                self.assertEqual(target.partition, partition)
                expected_type = None if gres is None or gres.count(":") == 1 else gres.split(":")[1]
                self.assertEqual(target.gpu_type, expected_type)
                vector = module["job_resource_vector"](
                    job(gpu_count=count, gpu_type=kind),
                    config,
                    ("a100", "fallback", "normalized"),
                )
                self.assertEqual(
                    sum(vector.components[3:]),
                    count if expected_type is not None else 0,
                )

    def test_unspecified_partition_emits_no_partition_override(self):
        module = load_accounting()
        config = configuration()
        config["execution"]["slurm"].update(partition=None, module_loads=[], qos=None, account=None)
        module["magnus_config"] = config
        commands = []

        def run(
            command,
            **kwargs,
        ):
            commands.append(command)
            return SimpleNamespace(stdout="123")

        control = module["_ControlMixin"]()
        control._transport = SimpleNamespace(run=run)
        control.submit_job_simple(
            entry_command="python3 wrapper.py",
            gpus=0,
            job_name="fixture",
            runner="fixture",
            token="fixture",
        )
        self.assertFalse(any(value.startswith("--partition=") for value in commands[0]))


if __name__ == "__main__":
    unittest.main()
