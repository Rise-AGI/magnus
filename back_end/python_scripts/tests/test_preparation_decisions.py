"""Scheduler decisions keep database waits off the event loop and join cleanup."""

import ast
import asyncio
import threading
import unittest
from types import MethodType
from unittest.mock import AsyncMock, Mock

from sqlalchemy import event

import test_job_finalization as fixtures
from test_image_cache import blocking_io


class PreparationDecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        self.fixture = fixtures.SlurmFinalizationTests()
        self.fixture.setUp()
        self.addCleanup(
            self.fixture.doCleanups,
        )
        self.namespace = {
            **self.fixture.namespace,
            "asyncio": asyncio,
            "_run_blocking_io": blocking_io,
        }
        self.actor = self.fixture.scheduler
        source = fixtures.SERVER / "_scheduler/_decisions.py"
        available = {
            node.name
            for node in ast.walk(
                ast.parse(
                    source.read_text(),
                ),
            )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in (
            "_make_decisions",
            "_load_preparation_candidates",
            "_fail_preparation",
        ):
            if name in available:
                function = fixtures.load_method(
                    "_scheduler/_decisions.py",
                    name,
                    self.namespace,
                )
                setattr(
                    self.actor,
                    name,
                    MethodType(
                        function,
                        self.actor,
                    ),
                )
        self.actor.preparing_jobs = {}
        self.actor._prepare_job_resources = AsyncMock()
        self.actor._queue_pending_submission = Mock()

    async def test_candidate_queries_do_not_run_on_the_event_loop(
        self,
    ) -> None:
        query_threads = []

        def observed(
            *arguments,
        ):
            query_threads.append(
                threading.get_ident(),
            )

        event.listen(
            self.fixture.engine,
            "before_cursor_execute",
            observed,
        )
        await self.actor._make_decisions()
        self.assertGreaterEqual(
            len(
                query_threads,
            ),
            2,
        )
        self.assertNotIn(
            threading.get_ident(),
            query_threads,
        )
        self.actor._queue_pending_submission.assert_called_once()

    async def test_cancel_during_failure_transaction_joins_commit_and_cleanup(
        self,
    ) -> None:
        table = self.fixture.add_job(
            status=fixtures.JobStatus.PREPARING,
        )
        with self.fixture.sessions() as db:
            db.get(
                fixtures.Job,
                "job",
            ).slurm_job_id = None
            db.commit()

        async def crashed() -> None:
            raise RuntimeError("preparation interrupted")

        failed = asyncio.create_task(
            crashed(),
        )
        await asyncio.gather(
            failed,
            return_exceptions=True,
        )
        self.actor.preparing_jobs["job"] = failed
        entered, finish = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        write_threads = []

        def hold_write(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ):
            if not statement.startswith("UPDATE") or entered.is_set():
                return
            write_threads.append(
                threading.get_ident(),
            )
            entered.set()
            if threading.get_ident() != loop_thread and not finish.wait(
                3,
            ):
                raise AssertionError("Failure transaction not released")

        event.listen(
            self.fixture.engine,
            "after_cursor_execute",
            hold_write,
        )
        task = asyncio.create_task(
            self.actor._make_decisions(),
        )
        try:
            self.assertTrue(
                await asyncio.to_thread(
                    entered.wait,
                    3,
                ),
            )
            self.assertNotIn(
                loop_thread,
                write_threads,
            )
            task.cancel()
            await asyncio.sleep(
                0,
            )
            self.assertFalse(
                task.done(),
            )
            finish.set()
            with self.assertRaises(
                asyncio.CancelledError,
            ):
                await task
        finally:
            finish.set()
            await asyncio.gather(
                task,
                return_exceptions=True,
            )
        self.assertEqual(
            self.fixture.state()[:3],
            (fixtures.JobStatus.FAILED, None, "CLEANUP"),
        )
        self.assertTrue((table / "repository").exists())
        self.fixture.cleanup(self.actor)
        self.assertEqual(
            self.fixture.state()[:3],
            (fixtures.JobStatus.FAILED, None, None),
        )
        self.assertFalse(
            (table / "repository").exists(),
        )
        self.assertTrue(
            (table / "slurm/output.txt").exists(),
        )
        self.actor._queue_pending_submission.assert_not_called()
