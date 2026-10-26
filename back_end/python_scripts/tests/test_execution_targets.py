"""Execution identities, colliding Slurm IDs and independent reconciliation."""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import typing
import unittest

from ruamel.yaml import YAML
from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


SERVER = Path(__file__).resolve().parents[2] / "server"
spec = importlib.util.spec_from_file_location("isolated_execution_targets", SERVER / "_execution_targets.py")
targets_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = targets_module
spec.loader.exec_module(targets_module)


def configuration():
    return {
        "execution": {
            "backend": "slurm",
            "target_id": "first",
            "slurm": {"partition": "one", "mem_mode": "explicit"},
            "targets": {
                "second": {
                    "name": "Second cluster",
                    "transport": {"mode": "ssh", "ssh": {"remote_root": "/second", "control_path": "second.sock"}},
                    "slurm": {"partition": "two", "mem_mode": "per_cpu"},
                    "cluster": {"max_cpu_count": 64},
                },
            },
        },
        "server": {"root": "/local", "ephemeral_root": "/scratch"},
        "transport": {"mode": "ssh", "ssh": {"remote_root": "/first", "control_path": "first.sock"}},
        "cluster": {"name": "First cluster", "max_cpu_count": 32},
    }


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_target: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_job_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)


def load_method(
    relative_path,
    name,
    namespace,
):
    path = SERVER / relative_path
    tree = ast.parse(path.read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def coordinator_class(
    targets,
    workers,
):
    for key, worker in workers.items():
        worker.config = targets[key].config
    path = SERVER / "_scheduler/_coordinator.py"
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    namespace = {
        "__name__": __name__,
        "configured_execution_targets": targets,
        "select_execution_target": targets_module.select_execution_target,
        "MagnusScheduler": lambda target: workers[target.id],
        "_WorkspaceGCMixin": type("GC", (), {"_reclaim_workspace_over_cap": lambda self: None}),
        "_run_blocking_io": asyncio.to_thread,
        "logger": logging.getLogger(__name__),
        "file_custody_manager": SimpleNamespace(pending_job_ids=lambda: set()),
    }
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["SchedulerCoordinator"]


class TargetConfigurationTests(unittest.TestCase):
    def test_complete_configuration_validates_each_target_and_connector(self):
        namespace = {name: getattr(typing, name) for name in ("Any", "Dict", "Set", "Type")}
        namespace.update(Path=Path, logger=logging.getLogger(__name__))
        exec(compile((SERVER / "_size_utils.py").read_text(), "sizes", "exec"), namespace)
        tree = ast.parse((SERVER / "_magnus_config.py").read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
            "_check_key", "_warn_extra_keys", "_check_one_secret_source", "_prepare_and_validate_magnus_config",
        }]
        exec(compile(ast.Module(body=functions, type_ignores=[]), "config", "exec"), namespace)
        validate = namespace["_prepare_and_validate_magnus_config"]
        config = YAML(typ="safe").load(SERVER.parents[1] / "configs/magnus_config.yaml.example")
        config["execution"].pop("container_image_mode", None)
        config["execution"].pop("container_prepare_timeout_seconds", None)
        config["execution"]["targets"] = configuration()["execution"]["targets"]
        config["execution"]["targets"]["second"]["cluster"]["scheduling"] = {"mode": "tenant"}
        settings = config["execution"]["targets"]["second"]
        settings.update(container_image_mode="sandbox", container_prepare_timeout_seconds=120)
        remote = config["execution"]["targets"]["second"]["transport"]["ssh"]
        remote.update(host="example.invalid", user="test", auto_connect={"command": ["connector", "second"]})
        validate(config)
        targets = targets_module.execution_targets(config, validate=validate)
        self.assertEqual(targets["second"].config["transport"]["ssh"]["auto_connect"]["timeout_seconds"], 45)
        self.assertIsNone(targets["second"].config["execution"]["slurm"]["module_init"])
        default = targets_module.select_execution_target(targets, None)
        self.assertEqual(default.config["execution"]["container_image_mode"], "native")
        self.assertEqual(default.config["execution"]["container_prepare_timeout_seconds"], 300)
        self.assertEqual(targets["second"].config["execution"]["container_image_mode"], "sandbox")
        self.assertEqual(targets["second"].config["execution"]["container_prepare_timeout_seconds"], 120)
        for field, value in (
            ("container_image_mode", "unknown"),
            ("container_prepare_timeout_seconds", 0),
            ("container_prepare_timeout_seconds", True),
            ("container_prepare_timeout_seconds", "120"),
        ):
            original = settings[field]
            settings[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                targets_module.execution_targets(config, validate=validate)
            settings[field] = original
        local = YAML(typ="safe").load(SERVER.parents[1] / "configs/magnus_config.yaml.example")
        local["server"]["auth"]["provider"] = "local"
        local["server"]["auth"].pop("feishu_client")
        local["execution"].update(backend="local", container_runtime="docker", container_image_mode="sandbox")
        with self.assertRaisesRegex(ValueError, "Slurm backend"):
            validate(local)
        remote["auto_connect"]["password"] = "must-not-mix"
        with self.assertRaisesRegex(ValueError, "cannot include password"):
            targets_module.execution_targets(config, validate=validate)
        remote["auto_connect"] = {"command": "unsafe shell string"}
        with self.assertRaisesRegex(ValueError, "argv list"):
            targets_module.execution_targets(config, validate=validate)

    def test_old_configuration_has_one_default_and_no_mutation(self):
        config = configuration()
        config["execution"].pop("targets")
        config["execution"].pop("target_id")
        targets = targets_module.execution_targets(config)
        self.assertEqual(list(targets), ["default"])
        self.assertIs(targets_module.select_execution_target(targets, None), targets["default"])
        self.assertNotIn("target_id", config["execution"])

    def test_target_configuration_and_authentication_are_isolated(self):
        config = configuration()
        config["transport"]["ssh"]["auto_connect"] = {"password_file": "/private/password"}
        seen = []
        targets = targets_module.execution_targets(config, validate=lambda value: seen.append(value))
        self.assertEqual(len(seen), 1)
        self.assertEqual(targets["second"].config["execution"]["slurm"]["mem_mode"], "per_cpu")
        self.assertNotIn("auto_connect", targets["second"].config["transport"]["ssh"])
        targets["second"].config["cluster"]["max_cpu_count"] = 128
        self.assertEqual(targets["first"].config["cluster"]["max_cpu_count"], 32)
        self.assertEqual(config["execution"]["targets"]["second"]["cluster"]["max_cpu_count"], 64)

    def test_invalid_or_unknown_target_never_falls_back(self):
        for target_id in ("first", "", "../other", "x" * 65):
            config = configuration()
            config["execution"]["targets"] = {target_id: {}}
            with self.subTest(target_id=target_id), self.assertRaises(ValueError):
                targets_module.execution_targets(config)
        targets = targets_module.execution_targets(configuration())
        with self.assertRaises(ValueError):
            targets_module.select_execution_target(targets, "removed")

    def test_colliding_slurm_ids_and_legacy_rows_are_scoped_in_sql(self):
        targets = targets_module.execution_targets(configuration())
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        namespace = {"Session": Session, "Job": Job}
        query = load_method("_scheduler/_core.py", "_job_query", namespace)
        with Session(engine) as db:
            db.add_all([
                Job(id="one", execution_target="first", slurm_job_id="42", status="Running"),
                Job(id="two", execution_target="second", slurm_job_id="42", status="Running"),
                Job(id="old", execution_target=None, slurm_job_id="41", status="Running"),
            ])
            db.commit()
            first = SimpleNamespace(target=targets["first"])
            second = SimpleNamespace(target=targets["second"])
            self.assertEqual({row.id for row in query(first, db, Job)}, {"one", "old"})
            self.assertEqual({row.id for row in query(second, db, Job)}, {"two"})
            query(second, db, Job).filter(Job.slurm_job_id == "42").update({Job.status: "Failed"})
            db.commit()
            self.assertEqual(db.get(Job, "one").status, "Running")
            self.assertEqual(db.get(Job, "two").status, "Failed")

    def test_cancel_and_signal_use_persisted_job_target(self):
        targets = targets_module.execution_targets(configuration())
        calls = []
        workers = {
            key: SimpleNamespace(
                terminate_job=lambda db, job, key=key: calls.append(("cancel", key, job.slurm_job_id)),
                signal_job=lambda job, key=key: calls.append(("signal", key, job.slurm_job_id)),
            ) for key in targets
        }
        scheduler = coordinator_class(targets, workers)()
        scheduler.terminate_job(None, SimpleNamespace(execution_target="second", slurm_job_id="42"))
        scheduler.signal_job(SimpleNamespace(execution_target=None, slurm_job_id="42"))
        self.assertEqual(calls, [("cancel", "second", "42"), ("signal", "first", "42")])
        with self.assertRaises(ValueError):
            scheduler.terminate_job(None, SimpleNamespace(execution_target="removed", slurm_job_id="42"))
        self.assertEqual(len(calls), 2)


class CoordinatorConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_targets_sharing_a_master_share_both_transfer_limits(self):
        config = configuration()
        config["execution"]["targets"]["second"]["transport"]["ssh"]["control_path"] = "first.sock"
        targets = targets_module.execution_targets(config)
        workers = {key: SimpleNamespace() for key in targets}
        coordinator_class(targets, workers)()
        self.assertIs(workers["first"]._artifact_recovery_slots, workers["second"]._artifact_recovery_slots)
        self.assertIs(workers["first"]._custody_bulk_slots, workers["second"]._custody_bulk_slots)

    async def test_stalled_target_does_not_block_other_target_or_duplicate_passes(self):
        gate = asyncio.Event()
        slow_started = asyncio.Event()
        fast_calls = []
        targets = targets_module.execution_targets(configuration())
        async def slow():
            slow_started.set()
            await gate.wait()
        async def fast():
            fast_calls.append(True)
        async def shutdown():
            pass
        workers = {
            "first": SimpleNamespace(tick=slow, shutdown=shutdown, inflight_job_ids=lambda: {"a"}),
            "second": SimpleNamespace(tick=fast, shutdown=shutdown, inflight_job_ids=lambda: {"b"}),
        }
        scheduler = coordinator_class(targets, workers)()
        await scheduler.tick()
        await asyncio.wait_for(slow_started.wait(), 1)
        await asyncio.sleep(0)
        self.assertEqual(len(fast_calls), 1)
        first_task = scheduler._ticks["first"]
        await scheduler.tick()
        await asyncio.sleep(0)
        self.assertIs(scheduler._ticks["first"], first_task)
        self.assertEqual(len(fast_calls), 2)
        self.assertEqual(scheduler.inflight_job_ids(), {"a", "b"})
        await scheduler.shutdown()
        self.assertTrue(first_task.cancelled())


if __name__ == "__main__":
    unittest.main()
