"""Disconnects cannot orphan a committed image pull or its DB worker."""
import ast
import asyncio
import __future__
from pathlib import Path
import runpy
import threading
from types import SimpleNamespace
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


class RouteWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_image_request_drains_record_work_and_starts_pull(self):
        namespace = runpy.run_path(str(SERVER / "_blocking_io.py"))
        started, release, committed = threading.Event(), threading.Event(), threading.Event()
        pulled = asyncio.Event()
        calls = []
        def prepare(*args):
            started.set()
            release.wait(3)
            committed.set()
            return SimpleNamespace(id=7), False
        async def pull(identifier, uri, *, is_refresh):
            calls.append((identifier, uri, is_refresh))
            pulled.set()
        namespace.update(_prepare_pull=prepare, _do_pull=pull)
        path = SERVER / "routers/images.py"
        node = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_begin_pull")
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec", flags=__future__.annotations.compiler_flag), namespace)
        task = asyncio.create_task(namespace["_begin_pull"](None, "image", None, False))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                self.assertFalse(committed.is_set())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            self.assertTrue(committed.is_set())
            await asyncio.wait_for(pulled.wait(), 1)
            self.assertEqual(calls, [(7, "image", False)])
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
