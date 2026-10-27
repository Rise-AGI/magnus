"""A stalled transfer cannot hold admission or consume unbounded worker slots."""
import ast
import asyncio
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_recovery():
    namespace = {"__name__": __name__, "logger": logging.getLogger(__name__)}
    for relative in ("_blocking_io.py", "_scheduler/_artifact_recovery.py"):
        path = SERVER / relative
        tree = ast.parse(path.read_text())
        if relative.endswith("_blocking_io.py"):
            tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_blocking_io"]
            namespace["asyncio"] = asyncio
        else:
            tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class ArtifactRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def actor(self, namespace, work, slots=4):
        actor = namespace["_ArtifactRecoveryMixin"]()
        actor._artifact_recovery_tasks = {}
        actor._custody_bulk_tasks = {}
        actor._custody_bulk_slots = asyncio.Semaphore(1)
        actor._custody_relay = SimpleNamespace(bulk_job_ids=lambda: set(), cancel_transfers=lambda: None)
        actor._artifact_retry_after = {}
        actor._artifact_recovery_slots = asyncio.Semaphore(slots)
        actor._recover_job_artifacts = work
        return actor

    async def test_bulk_lane_does_not_block_polling_and_shutdown_cancels_it(self):
        namespace = load_recovery()
        started, stop, observed = threading.Event(), threading.Event(), threading.Event()
        actor = self.actor(namespace, lambda job, external: (observed.set() or 30), slots=1)
        calls = []
        def bulk(job):
            calls.append(job)
            started.set()
            stop.wait(5)
        actor._custody_relay = SimpleNamespace(
            bulk_job_ids=lambda: {"job"}, transfer_bulk=bulk, cancel_transfers=stop.set,
        )
        try:
            actor._schedule_artifact_recovery({"job": "42"})
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            self.assertTrue(await asyncio.to_thread(observed.wait, 1))
            actor._schedule_artifact_recovery({"job": "42"})
            self.assertEqual(calls, ["job"])
            await asyncio.wait_for(actor.shutdown_artifact_recovery(), 1)
            self.assertTrue(stop.is_set())
            self.assertEqual(actor._custody_bulk_tasks, {})
        finally:
            stop.set()
            await actor.shutdown_artifact_recovery()

    async def test_stalled_job_does_not_hold_tick_or_duplicate_its_worker(self):
        namespace = load_recovery()
        started, release, other = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def work(job, external):
            calls.append(job)
            if job == "old":
                started.set()
                release.wait(5)
            else:
                other.set()
            return 60
        actor = self.actor(namespace, work)
        tree = ast.parse((SERVER / "_scheduler/_core.py").read_text())
        tick = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "tick")
        exec(compile(ast.Module(body=[tick], type_ignores=[]), "tick", "exec"), namespace)
        decisions = []
        async def decide():
            decisions.append("admitted")
        actor.enabled = True
        actor._loop = None
        actor._sync_reality = lambda: {"old": "1", "new": "2"}
        actor._make_decisions = decide
        actor._release_finished_builds = lambda: None
        actor._record_snapshot = lambda: None
        actor._reclaim_workspace_over_cap = lambda: None
        try:
            await asyncio.wait_for(namespace["tick"](actor), 1)
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            self.assertTrue(await asyncio.to_thread(other.wait, 1))
            await asyncio.wait_for(namespace["tick"](actor), 1)
            self.assertEqual(decisions, ["admitted", "admitted"])
            self.assertEqual(calls.count("old"), 1)
        finally:
            release.set()
            await actor.shutdown_artifact_recovery()

    async def test_bulk_jobs_take_single_file_turns_without_overlap(self):
        namespace = load_recovery()
        actor = self.actor(namespace, lambda job, external: 30)
        entered = {job: threading.Event() for job in ('a', 'b', 'c')}
        release = {job: threading.Event() for job in entered}
        calls = []

        def bulk(job):
            calls.append(job)
            entered[job].set()
            if not release[job].wait(5):
                raise TimeoutError('test did not release the transfer')

        actor._custody_relay = SimpleNamespace(
            bulk_job_ids=lambda: set(entered), transfer_bulk=bulk,
            cancel_transfers=lambda: [event.set() for event in release.values()],
        )
        try:
            actor._schedule_artifact_recovery({'a': '1', 'b': '2'})
            self.assertTrue(await asyncio.to_thread(entered['a'].wait, 1))
            self.assertFalse(entered['b'].is_set())
            actor._schedule_artifact_recovery({'a': '1', 'b': '2', 'c': '3'})
            release['a'].set()
            await asyncio.wait_for(actor._custody_bulk_tasks['a'], 1)
            self.assertTrue(await asyncio.to_thread(entered['b'].wait, 1))
            actor._schedule_artifact_recovery({'a': '1', 'b': '2', 'c': '3'})
            self.assertFalse(entered['c'].is_set())
            release['b'].set()
            self.assertTrue(await asyncio.to_thread(entered['c'].wait, 1))
            self.assertEqual(calls, ['a', 'b', 'c'])
            release['c'].set()
            await asyncio.wait_for(asyncio.gather(*actor._custody_bulk_tasks.values()), 1)
            self.assertEqual(calls, ['a', 'b', 'c', 'a'])
        finally:
            for event in release.values():
                event.set()
            await actor.shutdown_artifact_recovery()

    async def test_slow_submission_is_single_flight_and_shutdown_joins_it(self):
        namespace = load_recovery()
        path = SERVER / "_scheduler/_decisions.py"
        tree = ast.parse(path.read_text())
        methods = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("_queue_pending_submission", "shutdown_submission")]
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
        started, release = threading.Event(), threading.Event()
        calls = []
        def submit():
            calls.append("submit")
            started.set()
            release.wait(5)
        actor = SimpleNamespace(
            _submission_task=None,
            _schedule_pending_jobs=submit,
            _preparing_jobs_lock=threading.Lock(),
            _submission_cancel_events={},
            _submission_stopping=False,
        )
        cancelled = threading.Event()
        actor._submission_cancel_events["job"] = cancelled
        try:
            namespace["_queue_pending_submission"](actor)
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            namespace["_queue_pending_submission"](actor)
            self.assertEqual(calls, ["submit"])
            shutdown = asyncio.create_task(namespace["shutdown_submission"](actor))
            await asyncio.sleep(0.02)
            self.assertFalse(shutdown.done())
            self.assertTrue(cancelled.is_set())
            self.assertTrue(actor._submission_stopping)
            release.set()
            await asyncio.wait_for(shutdown, 1)
            self.assertIsNone(actor._submission_task)
        finally:
            release.set()
            if actor._submission_task is not None:
                await namespace["shutdown_submission"](actor)

    async def test_shutdown_drains_active_worker_and_cancels_waiting_jobs(self):
        namespace = load_recovery()
        started, release = threading.Event(), threading.Event()
        calls = []
        def work(job, external):
            calls.append(job)
            started.set()
            release.wait(5)
            return 60
        actor = self.actor(namespace, work, slots=1)
        actor._schedule_artifact_recovery({"old": "1", "queued": "2"})
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            shutdown = asyncio.create_task(actor.shutdown_artifact_recovery())
            await asyncio.sleep(0.02)
            self.assertFalse(shutdown.done())
            self.assertEqual(calls, ["old"])
            release.set()
            await asyncio.wait_for(shutdown, 1)
            self.assertEqual(actor._artifact_recovery_tasks, {})
        finally:
            release.set()
            await actor.shutdown_artifact_recovery()

    async def test_failed_transfer_retries_after_backoff_without_leaking_slots(self):
        namespace = load_recovery()
        now = [0]
        namespace["monotonic"] = lambda: now[0]
        calls = []
        def work(job, external):
            calls.append(job)
            raise TimeoutError("transfer deadline")
        actor = self.actor(namespace, work, slots=1)
        try:
            actor._schedule_artifact_recovery({"job": "1"})
            await asyncio.gather(*actor._artifact_recovery_tasks.values(), return_exceptions=True)
            with self.assertLogs(__name__, level="WARNING"):
                actor._schedule_artifact_recovery({"job": "1"})
            self.assertEqual(calls, ["job"])
            now[0] = 60
            actor._schedule_artifact_recovery({"job": "1"})
            await asyncio.gather(*actor._artifact_recovery_tasks.values(), return_exceptions=True)
            self.assertEqual(calls, ["job", "job"])
        finally:
            await actor.shutdown_artifact_recovery()


if __name__ == "__main__":
    unittest.main()
