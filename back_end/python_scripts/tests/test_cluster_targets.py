"""Cluster list scope and pagination with real SQL predicates and API schemas."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from sqlalchemy import String, create_engine, or_
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from test_execution_targets import SERVER, targets_module
from test_job_resources import load_schemas


helpers = {}
exec(compile((SERVER / "models/_helpers.py").read_text(), "helpers", "exec"), helpers)
UtcDateTime = helpers["UtcDateTime"]


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_target: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    job_type: Mapped[str] = mapped_column(String, default="A2")
    user_id: Mapped[str] = mapped_column(String, default="owner")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    start_time: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    task_name = "Example"
    namespace = "Example"
    repo_name = "example"
    gpu_count = 0
    gpu_type = "cpu"
    cpu_count = 2
    memory_demand = "4G"
    resource_mode = "slurm_per_cpu"


class ClusterTargetTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)
        self.config = {
            "transport": {"mode": "ssh", "ssh": {
                "host": "one", "user": "account", "control_path": "one.sock", "remote_root": "/shared",
            }},
            "server": {"scheduler": {"cluster_stats_cache_ttl": 0}},
            "cluster": {"name": "First", "gpus": []},
            "execution": {
                "backend": "slurm",
                "slurm": {"partition": "ordinary", "mem_mode": "per_cpu"},
                "targets": {
                    "fat": {"slurm": {"partition": "fat"}},
                    "other": {"transport": {"mode": "ssh", "ssh": {
                        "host": "two", "user": "account", "control_path": "two.sock", "remote_root": "/shared",
                    }}},
                },
            },
        }
        self.targets = targets_module.execution_targets(self.config)
        self.namespace = load_schemas()
        self.namespace.update({
            "time": time, "threading": threading, "timezone": timezone,
            "HTTPException": HTTPException, "or_": or_,
            "Depends": lambda value: None,
            "database": SimpleNamespace(get_db=None), "get_current_user": None,
            "models": SimpleNamespace(Job=Job, job_list_load_options=lambda: []),
            "magnus_config": self.config,
            "configured_execution_targets": self.targets,
            "select_execution_target": targets_module.select_execution_target,
            "requested_execution_target": targets_module.requested_execution_target,
            "is_local_mode": False,
            "resource_partitions": lambda config: [config["execution"]["slurm"]["partition"]],
            "SlurmManager": lambda **kwargs: kwargs["config"]["execution"]["slurm"]["partition"],
            "_node_name": "First",
        })
        path = SERVER / "routers/cluster.py"
        tree = ast.parse(path.read_text())
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        for node in tree.body:
            node.decorator_list = []
        exec(compile(tree, str(path), "exec"), self.namespace)
        self.observations = {}
        self.queries = []

        def view(manager, partitions):
            self.queries.append(manager)
            snapshot = SimpleNamespace(
                nodes=(SimpleNamespace(allocated_gpus=0, allocated_cpus=3, allocated_mem_mb=4096),),
                total_gpus=0, cpu_total=64, mem_total_mb=256000,
                unavailable_gpus=0, unavailable_cpus=0, unavailable_mem_mb=0, node_count=1,
            )
            return self.observations.get(manager, []), snapshot, self.now, time.monotonic()

        self.namespace["_query_cluster_view"] = view

    def add(
        self,
        identifier,
        target="default",
        status="Running",
        external=None,
        **fields,
    ):
        job = Job(
            id=identifier,
            execution_target=target,
            status=status,
            slurm_job_id=external,
            created_at=fields.pop("created_at", self.now),
            start_time=fields.pop("start_time", self.now),
            **fields,
        )
        self.db.add(job)
        self.db.commit()

    def task(
        self,
        identifier,
    ):
        return {
            "id": identifier, "user": "person", "name": "Slurm task", "start_time": self.now.isoformat(),
            "gpu_type": "cpu", "gpu_count": 0, "cpu_count": 8, "memory_mb": 8192, "node_count": 1,
        }

    def stats(
        self,
        target="default",
        **pagination,
    ):
        return self.namespace["get_cluster_stats"](execution_target=target, db=self.db, **pagination)

    def test_global_magnus_running_and_selected_external_rows_do_not_collide(self):
        self.add("ordinary", external="41")
        self.add("legacy", target=None, external="42")
        self.add("fat", target="fat", external="43", start_time=self.now + timedelta(seconds=1))
        self.add("independent", target="other", external="41")
        self.add("pending-collision", target="other", status="Queued", external="43")
        self.add("releasing", target="fat", status="Terminated", external="44")
        self.add("db-lag", status="Queued", external="45")
        self.add("finished", target="other", status="Success")
        self.observations["ordinary"] = [self.task(value) for value in ("41", "43", "44", "45", "900")]
        self.observations["fat"] = [self.task(value) for value in ("43", "901")]

        response = self.stats()
        rows = {row.id: row for row in response["running_jobs"]}
        self.assertEqual(set(rows), {"ordinary", "legacy", "fat", "independent", "releasing", "db-lag", "900 (slurm)"})
        self.assertEqual(response["total_running"], 7)
        self.assertEqual([row.id for row in response["pending_jobs"]], ["pending-collision"])
        self.assertEqual(rows["fat"].resource_summary.cpu_count, 8)
        self.assertEqual(rows["independent"].resource_summary.cpu_count, 2)
        self.assertEqual(rows["releasing"].status, "Terminated")
        self.assertTrue(rows["releasing"].is_releasing)
        self.assertEqual(response["resources"]["cpu_free"], 61)
        self.assertEqual(self.queries, ["ordinary"])

        selected = self.stats("fat", running_limit=1)
        self.assertEqual([row.id for row in selected["running_jobs"]], ["fat"])
        self.assertEqual(selected["total_running"], 5)
        tail = self.stats("fat", running_skip=4, running_limit=1)
        self.assertEqual([row.id for row in tail["running_jobs"]], ["901 (slurm)"])

    def test_pending_target_preference_precedes_lifecycle_and_priority_before_pagination(self):
        self.add("other-high", target="other", status="Pending", job_type="A1")
        self.add("base-queued", status="Pending", job_type="B2")
        self.add("base-build", status="Building")
        self.add("fat-low", target="fat", status="Pending", job_type="B2")
        self.add("fat-high", target="fat", status="Pending", job_type="A1")
        self.add("fat-build", target="fat", status="Preparing")
        response = self.stats("fat", pending_limit=3)
        self.assertEqual(response["total_pending"], 6)
        self.assertEqual([row.id for row in response["pending_jobs"]], ["fat-high", "fat-low", "fat-build"])
        response = self.stats("fat", pending_skip=3, pending_limit=3)
        self.assertEqual([row.id for row in response["pending_jobs"]], ["other-high", "base-queued", "base-build"])

    def test_my_active_preserves_descendant_scope_and_legacy_target_before_pagination(self):
        self.add("other-running", target="other")
        self.add("legacy-pending", target=None, status="Pending", user_id="child")
        self.add("base-build", status="Building")
        self.add("unrelated", user_id="stranger")
        self.add("finished", status="Success")
        owner = SimpleNamespace(id="owner", children=[SimpleNamespace(id="child", children=[])])
        response = self.namespace["get_my_active_jobs"](
            execution_target="default", db=self.db, current_user=owner, limit=2,
        )
        self.assertEqual(response["total"], 3)
        self.assertEqual([row.id for row in response["items"]], ["legacy-pending", "base-build"])
        response = self.namespace["get_my_active_jobs"](
            execution_target="default", db=self.db, current_user=owner, skip=2, limit=2,
        )
        self.assertEqual([row.id for row in response["items"]], ["other-running"])


if __name__ == "__main__":
    unittest.main()
