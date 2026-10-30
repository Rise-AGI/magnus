"""Cleanup cannot hold artifact recovery, duplicate work or lose restart intent."""

import ast
import asyncio
import threading
import unittest

import test_job_finalization as finalization
from test_artifact_recovery import SERVER, load_recovery


def load_cleanup():
    namespace = load_recovery()
    path = SERVER / "_scheduler" / "_cleanup.py"
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class CleanupSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def actor(self, namespace, work):
        actor = namespace["_CleanupMixin"]()
        actor._cleanup_tasks = {}
        actor._cleanup_retry_after = {}
        actor._cleanup_slots = asyncio.Semaphore(2)
        actor._cleanup_channel_slots = asyncio.Semaphore(1)
        actor._finish_job_cleanup = work
        self.addAsyncCleanup(actor.shutdown_cleanup)
        return actor

    async def test_cleanup_is_bounded_and_does_not_block_artifacts(self):
        namespace = load_cleanup()
        entered, release, transferred = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def work(job, external):
            calls.append(job)
            entered.set()
            release.wait(5)

        actor = self.actor(namespace, work)
        artifacts = namespace["_ArtifactRecoveryMixin"]()
        artifacts._artifact_recovery_slots = asyncio.Semaphore(1)
        artifacts._recover_job_artifacts = lambda job, external: transferred.set() or 30
        candidates = {str(index): str(index) for index in range(1000)}
        try:
            actor._schedule_cleanup(candidates)
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            actor._schedule_cleanup(candidates)
            self.assertEqual(len(actor._cleanup_tasks), 1)
            await asyncio.wait_for(artifacts._recover_artifacts_in_slot("live", "42"), 1)
            self.assertTrue(transferred.is_set())
            release.set()
            await next(iter(actor._cleanup_tasks.values()))
            actor._schedule_cleanup(candidates)
            await next(iter(actor._cleanup_tasks.values()))
            self.assertEqual(calls, ["0", "1"])
        finally:
            release.set()

    async def test_channel_limit_and_shutdown_keep_ownership_until_joined(self):
        namespace = load_cleanup()
        entered = [threading.Event() for _ in range(3)]
        release = threading.Event()
        actors = []
        for event in entered:
            def work(job, external, signal=event):
                signal.set()
                release.wait(5)
            actors.append(self.actor(namespace, work))
        slots = asyncio.Semaphore(2)
        for actor in actors:
            actor._cleanup_slots = slots
        actors[1]._cleanup_channel_slots = actors[0]._cleanup_channel_slots
        try:
            for index, actor in enumerate(actors):
                actor._schedule_cleanup({str(index): "42"})
            self.assertTrue(await asyncio.to_thread(entered[0].wait, 1))
            self.assertTrue(await asyncio.to_thread(entered[2].wait, 1))
            self.assertFalse(entered[1].is_set())
            stopping = [asyncio.create_task(actor.shutdown_cleanup()) for actor in actors]
            await asyncio.sleep(0.02)
            self.assertFalse(stopping[0].done())
            self.assertEqual(slots._value, 0)
            release.set()
            await asyncio.wait_for(asyncio.gather(*stopping), 1)
            self.assertFalse(entered[1].is_set())
            self.assertEqual(slots._value, 2)
        finally:
            release.set()

    async def test_restart_recovers_durable_cleanup_without_reimporting_artifacts(self):
        fixture = finalization.SlurmFinalizationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        table = fixture.add_job()
        fixture.scheduler._sync_reality_slurm()
        fixture.scheduler._recover_job_artifacts("job", "slurm-job")
        self.assertEqual(fixture.state()[2], "CLEANUP")
        self.assertTrue((table / "repository").is_dir())
        namespace = load_cleanup()
        namespace.update(SessionLocal=fixture.sessions, Job=finalization.Job)
        restarted = fixture.new_scheduler()
        actor = self.actor(namespace, restarted._finish_job_cleanup)
        actor._job_query = restarted._job_query
        fixture.events.clear()
        actor._schedule_cleanup(actor._cleanup_candidates())
        await next(iter(actor._cleanup_tasks.values()))
        self.assertEqual(fixture.state()[1:3], (None, None))
        self.assertFalse((table / "repository").exists())
        self.assertTrue((table / "slurm" / "output.txt").exists())
        self.assertFalse(any(event[0] in ("transfer", "markers", "query") for event in fixture.events))


if __name__ == "__main__":
    unittest.main()
