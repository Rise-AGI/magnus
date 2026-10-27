"""Shared remote cache protection using SQLite and the real file receiver."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from test_execution_targets import SERVER, targets_module
from test_transport_timeouts import load_transport


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_target: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    build_result: Mapped[str | None] = mapped_column(String, nullable=True)
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True)


def checksum(
    content,
):
    return "sha256:" + hashlib.sha256(content).hexdigest()


class RelayCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.engine = create_engine("sqlite:///" + str(self.root / "jobs.db"))
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        config = {
            "execution": {
                "backend": "slurm",
                "image_build": {"timeout_seconds": 10, "cache_size": "128B", "min_free_space": "0B"},
                "targets": {
                    "fat": {"slurm": {"partition": "fat"}},
                    "independent": {"transport": {"mode": "ssh", "ssh": {
                        "host": "other", "user": "account", "control_path": "other.sock", "remote_root": str(self.root),
                    }}},
                },
            },
            "transport": {"mode": "ssh", "ssh": {
                "host": "cluster", "user": "account", "control_path": "primary.sock", "remote_root": str(self.root),
            }},
            "cluster": {"name": "Primary"},
        }
        self.targets = targets_module.execution_targets(config)
        self.targets["fat"].config["transport"]["ssh"].update(
            control_path="another.sock",
            remote_root=str(self.root) + "/./",
        )
        path = SERVER / "_scheduler/_relay_build_cache.py"
        tree = ast.parse(path.read_text())
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        self.module = {
            "__file__": str(path),
            "check_transfer_cancelled": load_transport()["check_transfer_cancelled"],
            "configured_execution_targets": self.targets,
            "SessionLocal": lambda: Session(self.engine),
            "Job": Job,
            "JobStatus": SimpleNamespace(SUCCESS="Success", FAILED="Failed", TERMINATED="Terminated"),
        }
        exec(compile((SERVER / "_size_utils.py").read_text(), "sizes", "exec"), self.module)
        exec(compile(tree, str(path), "exec"), self.module)
        self.incoming = self.root / "incoming.sif"
        self.incoming.write_bytes(b"new" * 20)

    def stage(
        self,
        target_id,
        transport,
    ):
        self.module["stage_built_sif"](
            transport,
            str(self.incoming),
            checksum(self.incoming.read_bytes()),
            job_id="submission",
            target=self.targets[target_id],
        )

    def test_shared_targets_pin_legacy_active_and_releasing_images_during_eviction(self):
        active = b"a" * 20
        legacy = b"b" * 20
        releasing = b"c" * 20
        old = b"d" * 20
        cache = self.root / "container_cache/native_builds"
        cache.mkdir(parents=True)
        for content in (active, legacy, releasing, old):
            (cache / (checksum(content)[7:] + ".sif")).write_bytes(content)
        with Session(self.engine) as db:
            for identifier, target, status, external, content in (
                ("active", "default", "Running", "1", active),
                ("legacy", None, "Pending", None, legacy),
                ("releasing", "default", "Terminated", "2", releasing),
                ("old", "fat", "Success", None, old),
            ):
                db.add(Job(id=identifier, execution_target=target, status=status,
                           slurm_job_id=external, build_result=json.dumps({"digest": checksum(content)})))
            db.add(Job(id="elsewhere", execution_target="independent", status="Running", build_result="invalid"))
            db.commit()

        def receive(
            command,
            path,
            *,
            prefix,
            timeout,
            cancel_event=None,
        ):
            request = json.loads(prefix)
            self.assertEqual(set(request["protected"]), {checksum(active), checksum(legacy), checksum(releasing)})
            result = subprocess.run(
                [sys.executable, *command[1:]],
                input=prefix + Path(path).read_bytes(),
                capture_output=True,
                timeout=timeout,
            )
            return SimpleNamespace(returncode=result.returncode, stderr=result.stderr.decode())

        self.stage("fat", SimpleNamespace(run_with_file_input=receive))
        for content in (active, legacy, releasing, self.incoming.read_bytes()):
            self.assertEqual((cache / (checksum(content)[7:] + ".sif")).read_bytes(), content)
        self.assertFalse((cache / (checksum(old)[7:] + ".sif")).exists())

    def test_cancel_while_waiting_preserves_other_jobs_cache_lock(self):
        target = self.targets["default"]
        cache_key = target.image_cache_key
        lock = threading.Lock()
        lock.acquire()
        self.module["_STAGING_LOCKS"][cache_key] = lock
        cancelled = threading.Event()
        timer = threading.Timer(0.05, cancelled.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(InterruptedError):
                self.module["stage_built_sif"](
                    SimpleNamespace(), str(self.incoming), checksum(self.incoming.read_bytes()),
                    job_id="submission", target=target, cancel_event=cancelled,
                )
            self.assertTrue(lock.locked())
            self.assertLess(time.monotonic() - started, 1)
        finally:
            timer.join()
            lock.release()

    def test_shared_snapshot_and_transfer_serialize_but_independent_cache_can_progress(self):
        entered = threading.Event()
        release = threading.Event()
        second_snapshot = threading.Event()
        second_attempt = threading.Event()
        independent = threading.Event()
        original_session = self.module["SessionLocal"]

        def session():
            if threading.current_thread().name == "shared-second":
                second_snapshot.set()
            return original_session()

        self.module["SessionLocal"] = session

        def hold(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release the first transfer")
            return SimpleNamespace(returncode=0)

        def second():
            threading.current_thread().name = "shared-second"
            second_attempt.set()
            self.stage("fat", SimpleNamespace(run_with_file_input=lambda *args, **kwargs: SimpleNamespace(returncode=0)))

        def separate(*args, **kwargs):
            independent.set()
            return SimpleNamespace(returncode=0)

        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.stage, "default", SimpleNamespace(run_with_file_input=hold))
            try:
                self.assertTrue(entered.wait(3))
                shared = pool.submit(second)
                self.assertTrue(second_attempt.wait(3))
                other = pool.submit(self.stage, "independent", SimpleNamespace(run_with_file_input=separate))
                self.assertTrue(independent.wait(3))
                other.result(timeout=3)
                self.assertFalse(second_snapshot.wait(0.1))
            finally:
                release.set()
            first.result(timeout=3)
            shared.result(timeout=3)
        self.assertTrue(second_snapshot.is_set())


if __name__ == "__main__":
    unittest.main()
