"""ASGI failures must join writers and attempt every resource shutdown."""
import ast
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock


SERVER = Path(__file__).resolve().parents[2] / "server"
helpers = {}
for name in ("_lifecycle.py", "_blocking_io.py"):
    exec(compile((SERVER / name).read_text(), str(SERVER / name), "exec"), helpers)


def load_function(
    source,
    name,
    namespace,
):
    tree = ast.parse((SERVER / source).read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), source, "exec"), namespace)
    return namespace[name]


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.started = set()
        self.drained = set()
        self.failures = set()
        self.drain_gate = None
        self.drain_started = asyncio.Event()
        self.limiter = SimpleNamespace(total_tokens=0)
        self.db = MagicMock()
        self.db.__enter__.return_value = self.db
        self.db.query.return_value.filter.return_value.all.return_value = []
        self.db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        async def background(name):
            self.started.add(name)
            try:
                await asyncio.Future()
            finally:
                self.drain_started.set()
                if self.drain_gate is not None:
                    await self.drain_gate.wait()
                await asyncio.sleep(0)
                self.drained.add(name)
                self.events.append(f"drained:{name}")
                if name in self.failures:
                    raise RuntimeError(name)

        async def shutdown(name):
            self.assertEqual(self.started, self.drained)
            self.events.append(name)
            if name in self.failures:
                raise RuntimeError(name)

        async def refresh():
            await asyncio.sleep(0)
            if "refresh" in self.failures:
                raise ValueError("refresh")

        def custody_shutdown():
            self.assertEqual(self.started, self.drained)
            self.events.append("custody")
            if "custody" in self.failures:
                raise RuntimeError("custody")

        def upload_shutdown():
            self.assertEqual(self.started, self.drained)
            self.events.append("uploads")
            if "uploads" in self.failures:
                raise RuntimeError("uploads")

        def recover():
            if "recover" in self.failures:
                raise ValueError("recover")

        self.namespace = {
            **helpers,
            "asynccontextmanager": asynccontextmanager,
            "asyncio": SimpleNamespace(
                get_running_loop=lambda: SimpleNamespace(set_default_executor=lambda executor: None),
                create_task=asyncio.create_task,
            ),
            "concurrent": SimpleNamespace(futures=SimpleNamespace(ThreadPoolExecutor=lambda **kwargs: object())),
            "anyio": SimpleNamespace(to_thread=SimpleNamespace(current_default_thread_limiter=lambda: self.limiter)),
            "FastAPI": object,
            "logger": logging.getLogger("lifespan-test"),
            "SessionLocal": lambda: self.db,
            "models": MagicMock(),
            "json": __import__("json"),
            "recover_stuck_images": recover,
            "shutdown_image_pulls": lambda: asyncio.sleep(0),
            "scheduler": SimpleNamespace(
                shutdown=lambda: shutdown("scheduler"),
                restore_pins=lambda rows: None,
            ),
            "resource_manager": SimpleNamespace(
                builds=SimpleNamespace(
                    shutdown=lambda: shutdown("builds"),
                    restore_pins=lambda pins: None,
                ),
                images=SimpleNamespace(restore_legacy_names=lambda rows: None, pin=lambda *args: None),
            ),
            "file_custody_manager": SimpleNamespace(
                cleanup_loop=lambda: background("custody-loop"),
                shutdown=custody_shutdown,
            ),
            "upload_store": SimpleNamespace(shutdown=upload_shutdown),
            "cleanup_uploads": lambda: background("uploads-loop"),
            "run_scheduler_loop": lambda: background("scheduler-loop"),
            "service_manager": SimpleNamespace(start_background_loop=lambda: background("service-loop")),
            "_refresh_all_user_info": refresh,
            "_run_user_info_refresh_loop": lambda: background("refresh-loop"),
            "is_local_mode": False,
            "is_local_auth": False,
            "magnus_config": {"server": {"auth": {"feishu_client": {"refresh_interval": 1}}}},
        }
        self.lifespan = load_function("main.py", "lifespan", self.namespace)

    def assert_shutdown_complete(self):
        self.assertEqual(self.started, self.drained)
        self.assertEqual(self.events[-4:], ["scheduler", "uploads", "custody", "builds"])

    async def test_normal_exit_joins_every_background_task(self):
        async with self.lifespan(None):
            await asyncio.sleep(0)
        self.assertEqual(len(self.started), 5)
        self.assert_shutdown_complete()

    async def test_startup_failure_before_and_after_background_launch(self):
        for name in ("recover", "refresh"):
            with self.subTest(name=name):
                self.failures = {name}
                with self.assertRaisesRegex(ValueError, name):
                    async with self.lifespan(None):
                        self.fail("startup must fail")
                self.assert_shutdown_complete()

    async def test_exception_thrown_at_yield_preserves_cleanup_and_original_error(self):
        with self.assertRaisesRegex(ValueError, "request lifetime"):
            async with self.lifespan(None):
                await asyncio.sleep(0)
                raise ValueError("request lifetime")
        self.assert_shutdown_complete()

    async def test_shutdown_failure_does_not_skip_remaining_components(self):
        for name in ("scheduler", "uploads", "custody", "builds"):
            with self.subTest(name=name):
                self.failures = {name}
                with self.assertRaisesRegex(RuntimeError, name):
                    async with self.lifespan(None):
                        await asyncio.sleep(0)
                self.assert_shutdown_complete()

    async def test_failed_background_cleanup_is_reported_after_all_tasks_join(self):
        self.failures = {"service-loop"}
        with self.assertRaises(BaseExceptionGroup) as caught:
            async with self.lifespan(None):
                await asyncio.sleep(0)
        self.assertIn("service-loop", str(caught.exception.exceptions[0]))
        self.assert_shutdown_complete()

    async def test_repeated_cancellation_waits_for_teardown(self):
        self.drain_gate = asyncio.Event()
        serving = asyncio.Event()
        async def serve():
            async with self.lifespan(None):
                await asyncio.sleep(0)
                serving.set()
                await asyncio.Future()
        task = asyncio.create_task(serve())
        try:
            await asyncio.wait_for(serving.wait(), 2)
            task.cancel()
            await asyncio.wait_for(self.drain_started.wait(), 2)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertNotIn("scheduler", self.events)
        finally:
            self.drain_gate.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assert_shutdown_complete()

    async def test_partial_docker_collector_start_still_stops(self):
        def start():
            self.events.append("collector-start")
            raise RuntimeError("collector start")
        async def stop():
            self.events.append("collector-stop")
        self.namespace["is_local_mode"] = True
        self.namespace["DockerMetricsCollector"] = lambda: SimpleNamespace(start=start, stop=stop)
        with self.assertRaisesRegex(RuntimeError, "collector start"):
            async with self.lifespan(None):
                self.fail("startup must fail")
        self.assertEqual(self.events[:2], ["collector-start", "collector-stop"])
        self.assert_shutdown_complete()

    async def test_shutdown_failure_retains_original_startup_exception_context(self):
        self.failures = {"refresh", "scheduler"}
        with self.assertRaisesRegex(RuntimeError, "scheduler") as caught:
            async with self.lifespan(None):
                self.fail("startup must fail")
        context = caught.exception.__context__
        self.assertIsInstance(context, ValueError)
        self.assertEqual(str(context), "refresh")
        self.assert_shutdown_complete()


class SchedulerShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_target_does_not_detach_other_target_shutdown(self):
        namespace = {**helpers, "asyncio": asyncio}
        shutdown = load_function("_scheduler/_coordinator.py", "shutdown", namespace)
        gate = asyncio.Event()
        entered = asyncio.Event()
        events = []
        async def failed():
            raise RuntimeError("first target")
        async def delayed():
            entered.set()
            await gate.wait()
            events.append("joined")
        coordinator = SimpleNamespace(
            _ticks={}, _gc_task=None, _custody_task=None,
            workers={"first": SimpleNamespace(shutdown=failed), "second": SimpleNamespace(shutdown=delayed)},
        )
        closing = asyncio.create_task(shutdown(coordinator))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
        finally:
            gate.set()
            with self.assertRaises(BaseExceptionGroup):
                await asyncio.wait_for(closing, 2)
        self.assertEqual(events, ["joined"])

    async def test_worker_failure_still_joins_preparation(self):
        from contextlib import AsyncExitStack
        namespace = {**helpers, "asyncio": asyncio, "AsyncExitStack": AsyncExitStack}
        shutdown = load_function("_scheduler/_core.py", "shutdown", namespace)
        preparing = load_function("_scheduler/_core.py", "_shutdown_preparing_jobs", namespace)
        events = []
        entered = asyncio.Event()
        async def prepare():
            try:
                entered.set()
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                events.append("preparation joined")
        async def submissions():
            raise RuntimeError("submission shutdown")
        async def artifacts():
            events.append("artifacts")
        task = asyncio.create_task(prepare())
        await asyncio.wait_for(entered.wait(), 2)
        worker = SimpleNamespace(
            _preparing_jobs_lock=threading.Lock(), preparing_jobs={"job": task},
            _image_pull_tasks={},
            shutdown_submission=submissions, shutdown_artifact_recovery=artifacts,
        )
        worker._shutdown_preparing_jobs = lambda: preparing(worker)
        with self.assertRaisesRegex(RuntimeError, "submission shutdown"):
            await shutdown(worker)
        self.assertTrue(task.done())
        self.assertEqual(events, ["artifacts", "preparation joined"])

    async def test_shutdown_joins_detached_shared_pull_after_its_last_job_leaves(self):
        namespace = {**helpers, "asyncio": asyncio}
        shutdown = load_function("_scheduler/_core.py", "_shutdown_preparing_jobs", namespace)
        started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def pull():
            try:
                started.set()
                await asyncio.Future()
            finally:
                cleaning.set()
                await finish.wait()

        pull_task = asyncio.create_task(pull())
        await asyncio.wait_for(started.wait(), 2)
        worker = SimpleNamespace(
            _preparing_jobs_lock=threading.Lock(),
            preparing_jobs={},
            _image_pull_tasks={"image": pull_task},
        )
        closing = asyncio.create_task(shutdown(worker))
        try:
            await asyncio.wait_for(cleaning.wait(), 2)
            self.assertFalse(closing.done())
        finally:
            finish.set()
            await asyncio.wait_for(closing, 2)
        self.assertTrue(pull_task.done())


class BackgroundWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_custody_and_metrics_wait_for_cancelled_thread(self):
        for source, name, worker_name in (
            ("_file_custody_manager/_manager.py", "cleanup_loop", "cleanup_expired"),
            ("_metrics_collector/_collector.py", "_run_loop", "_tick"),
        ):
            with self.subTest(source=source):
                entered = threading.Event()
                release = threading.Event()
                finished = threading.Event()
                def worker():
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test did not release worker")
                    finished.set()
                async def sleep(delay):
                    await asyncio.sleep(0)
                namespace = {
                    **helpers,
                    "asyncio": SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
                    "logger": logging.getLogger("writer-test"),
                    "_SAMPLE_INTERVAL_SECONDS": 0,
                }
                loop = load_function(source, name, namespace)
                task = asyncio.create_task(loop(SimpleNamespace(**{worker_name: worker})))
                try:
                    async with asyncio.timeout(2):
                        while not entered.is_set():
                            await asyncio.sleep(0.001)
                    task.cancel()
                    await asyncio.sleep(0.01)
                    self.assertFalse(task.done())
                    self.assertFalse(finished.is_set())
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                self.assertTrue(finished.is_set())


if __name__ == "__main__":
    unittest.main()
