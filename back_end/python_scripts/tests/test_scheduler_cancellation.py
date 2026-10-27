"""A cancelled scheduler cannot leave a destructive worker behind at shutdown."""
import ast
import asyncio
import __future__
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


class SchedulerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_joins_worker_before_propagating_repeated_cancellation(self):
        namespace = {"asyncio": asyncio, "logger": logging.getLogger(__name__)}
        for relative, method in (("_blocking_io.py", "_run_blocking_io"), ("_scheduler/_core.py", "tick")):
            source = SERVER / relative
            tree = ast.parse(source.read_text(encoding="utf-8"))
            function = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == method)
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec", flags=__future__.annotations.compiler_flag), namespace)
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        later = []
        def worker():
            started.set()
            release.wait(3)
            finished.set()
        async def decisions():
            later.append(True)
        scheduler = SimpleNamespace(enabled=True, _loop=None, _sync_reality=worker, _make_decisions=decisions)
        tick = asyncio.create_task(namespace["tick"](scheduler))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            tick.cancel()
            await asyncio.sleep(0)
            tick.cancel()
            await asyncio.sleep(0)
            self.assertFalse(tick.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(tick, 2)
            self.assertTrue(finished.is_set())
            self.assertEqual(later, [])
        finally:
            release.set()
            await asyncio.gather(tick, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
