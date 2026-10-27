"""Isolated diagnostics regressions; run directly with Python 3.14, without a server."""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import types
import unittest
from unittest.mock import patch


SOURCE = pathlib.Path(__file__).resolve().parents[2] / "server" / "_resource_manager"
ROOT_PACKAGE = "isolated_build_diagnostics"
root_package = types.ModuleType(ROOT_PACKAGE)
root_package.__path__ = [str(SOURCE.parent)]
sys.modules[ROOT_PACKAGE] = root_package
PACKAGE = f"{ROOT_PACKAGE}._resource_manager"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SOURCE)]
sys.modules[PACKAGE] = package


def load(
    name: str,
):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


buildkit = load("_buildkit")
progress_module = load("_build_progress")


def log_event(
    data: bytes,
    vertex: str = "one",
) -> str:
    return json.dumps({"logs": [{
        "vertex": vertex,
        "stream": 1,
        "data": base64.b64encode(data).decode("ascii"),
    }]})


class ProgressTests(unittest.TestCase):
    def test_cached_vertex_can_resume_execution(self):
        for cached in ({}, {"cached": False}):
            with self.subTest(cached=cached):
                progress = progress_module.BuildProgress()
                progress.consume(json.dumps({"vertexes": [
                    {
                        "digest": "compile",
                        "name": "RUN compile",
                        "cached": True,
                        "started": "2026-01-01T00:00:00Z",
                        "completed": "2026-01-01T00:00:00Z",
                    },
                    {
                        "digest": "compile",
                        "name": "RUN compile",
                        "started": "2026-01-01T00:00:01Z",
                        **cached,
                    },
                ]}))
                snapshot = progress.snapshot()
                self.assertEqual(snapshot["cached_steps"], 0)
                self.assertEqual(snapshot["completed_steps"], 0)
                self.assertEqual(snapshot["current_step"], "RUN compile")
                progress.consume(json.dumps({"vertexes": [{
                    "digest": "compile",
                    "name": "RUN compile",
                    "started": "2026-01-01T00:00:01Z",
                    "completed": "2026-01-01T00:00:10Z",
                }]}))
                self.assertEqual(progress.snapshot()["cached_steps"], 0)
                self.assertEqual(progress.snapshot()["completed_steps"], 1)

    def test_log_events_do_not_change_vertex_cache_state(self):
        progress = progress_module.BuildProgress()
        progress.consume(json.dumps({"vertexes": [{
            "digest": "one",
            "name": "RUN compile",
            "cached": True,
            "completed": "2026-01-01T00:00:00Z",
        }]}))
        progress.consume(log_event(b"retained output"))
        progress.consume(json.dumps({"statuses": [{"vertex": "one", "current": 1}]}))
        snapshot = progress.snapshot()
        self.assertEqual(snapshot["cached_steps"], 1)
        self.assertEqual(snapshot["completed_steps"], 1)
        self.assertIn("retained output", snapshot["log_tail"])

    def test_independent_subscribers_across_partial_blocks(self):
        progress = progress_module.BuildProgress()
        with tempfile.TemporaryDirectory() as temporary:
            first = pathlib.Path(temporary) / "first"
            second = pathlib.Path(temporary) / "second"
            first.mkdir()
            second.mkdir()
            progress.append("before")
            progress.persist(first)
            progress.append("after")
            progress.persist(second)
            progress.persist(first)
            progress.persist(first)
            self.assertEqual((first / "output.txt").read_bytes(), b"before\nafter\n")
            self.assertEqual((second / "output.txt").read_bytes(), b"before\nafter\n")
            progress.persist(first, cancelled=True)
            progress.set_phase("ready")
            progress.persist(first)
            progress.persist(second)
            self.assertEqual(json.loads((first / "progress.json").read_text())["phase"], "cancelled")
            self.assertEqual(json.loads((second / "progress.json").read_text())["phase"], "ready")

    def test_concurrent_append_and_subscriber_flushes(self):
        progress = progress_module.BuildProgress()
        started = threading.Barrier(3)
        with tempfile.TemporaryDirectory() as temporary:
            directories = [pathlib.Path(temporary) / name for name in ("first", "second")]
            for directory in directories:
                directory.mkdir()

            def produce():
                started.wait()
                for index in range(12000):
                    progress.append(str(index))

            def flush(
                directory: pathlib.Path,
            ):
                started.wait()
                for _ in range(30):
                    progress.persist(directory)

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                tasks = [executor.submit(produce), *(executor.submit(flush, path) for path in directories)]
                for task in tasks:
                    task.result()
            expected = "".join(f"{index}\n" for index in range(12000)).encode()
            for directory in directories:
                progress.persist(directory)
                self.assertEqual((directory / "output.txt").read_bytes(), expected)

    def test_truncation_and_snapshot_allocation(self):
        progress = progress_module.BuildProgress()
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            progress.append("old")
            progress.persist(directory)
            for _ in range(progress_module.LOG_LIMIT // 1024 + 100):
                progress.append("x" * 1023)
            progress.append("last line")
            progress.persist(directory)
            output = (directory / "output.txt").read_bytes()
            self.assertLessEqual(len(output), progress_module.LOG_LIMIT)
            self.assertIn(progress_module.TRUNCATION_MARKER, output)
            self.assertTrue(output.endswith(b"last line\n"))
            self.assertLessEqual(progress.log_size, progress_module.LOG_LIMIT)
            self.assertLessEqual(len(progress.logs), progress_module.LOG_CHUNK_COUNT)
            tracemalloc.start()
            try:
                snapshot = progress.snapshot()
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(snapshot["log_tail"], output[-progress_module.TAIL_LIMIT:].decode())
            self.assertLess(peak, 256 * 1024)
            delayed = directory / "delayed"
            delayed.mkdir()
            progress.persist(delayed)
            self.assertTrue((delayed / "output.txt").read_bytes().startswith(progress_module.TRUNCATION_MARKER))

    def test_rawjson_utf8_fragments_and_unknown_records(self):
        progress = progress_module.BuildProgress()
        encoded = "\u4e2d".encode()
        progress.consume(log_event(encoded[:2]))
        progress.consume(log_event(b"other", vertex="two"))
        progress.consume(log_event(encoded[2:]))
        progress.consume('{"extension": "future diagnostic"}')
        progress.consume('{"logs": 10}')
        invalid = json.dumps({"logs": [{"data": "invalid !"}] * 100})
        progress.consume(invalid)
        progress.consume(log_event(b"\xe2"))
        progress.set_phase("ready")
        tail = progress.snapshot()["log_tail"]
        self.assertIn("other\n\u4e2d\n", tail)
        self.assertIn('"future diagnostic"', tail)
        self.assertIn('{"logs": 10}', tail)
        self.assertEqual(tail.count(invalid), 1)
        self.assertTrue(tail.endswith("\ufffd\n"))

    def test_error_filter_keeps_diagnostics_and_unknown_json(self):
        error = "\n".join([
            json.dumps({"vertexes": [{"digest": "one", "error": "registry returned 403"}]}),
            json.dumps({"statuses": [{"vertex": "one", "current": 10}]}),
            "Dockerfile:1",
            '{"unknown": "keep this"}',
        ])
        detail = buildkit._build_error_detail(error)
        self.assertIn("registry returned 403", detail)
        self.assertIn("Dockerfile:1", detail)
        self.assertIn('"keep this"', detail)
        self.assertNotIn('"statuses"', detail)


class UsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_partitions_preserve_every_record_and_are_reused(self):
        records = dict(zip(("a", "aa", "an", "n", "na", "nn", "中"), range(1, 8)))
        calls = []

        async def query(arguments, **kwargs):
            calls.append(arguments)
            selected = records
            if "--filter" in arguments:
                expression = arguments[arguments.index("--filter") + 1].removeprefix("id~=")
                selected = {key: value for key, value in records.items() if re.search(expression, key)}
            if len(selected) > 3:
                raise buildkit.ImageBuildError(
                    "buildctl: ResourceExhausted: trying to send message larger than max (16925343 vs. 16777216)"
                )
            return "\n".join(str(value) for value in selected.values())

        client = buildkit.BuildKitClient({})
        with patch.object(buildkit, "run_command", query):
            self.assertEqual(await client.disk_usage(), sum(records.values()))
            calls.clear()
            records["a中"] = 11
            self.assertEqual(await client.disk_usage(), sum(records.values()))
        self.assertTrue(all("--filter" in arguments for arguments in calls))
        for identifier in records:
            self.assertEqual(
                sum(bool(re.search(buildkit._usage_filter(path).removeprefix("id~="), identifier))
                    for path in client._usage_partitions),
                1,
            )

    async def test_usage_fast_path_preserves_size_parsing(self):
        calls = []

        async def query(arguments, **kwargs):
            calls.append(arguments)
            return "12\n-1\n7\n"

        with patch.object(buildkit, "run_command", query):
            self.assertEqual(await buildkit.BuildKitClient({}).disk_usage(), 19)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--filter", calls[0])

    async def test_usage_does_not_retry_other_worker_errors(self):
        calls = []

        async def query(arguments, **kwargs):
            calls.append(arguments)
            raise buildkit.ImageBuildError("ResourceExhausted: disk quota exceeded")

        with patch.object(buildkit, "run_command", query):
            with self.assertRaisesRegex(buildkit.ImageBuildError, "disk quota"):
                await buildkit.BuildKitClient({}).disk_usage()
        self.assertEqual(len(calls), 1)

    async def test_usage_failure_never_publishes_partial_partitions(self):
        async def query(arguments, **kwargs):
            if "--filter" not in arguments:
                raise buildkit.ImageBuildError("buildctl returned too much data to inspect safely")
            expression = arguments[arguments.index("--filter") + 1]
            return "12" if "[^" not in expression else "invalid size"

        client = buildkit.BuildKitClient({})
        with patch.object(buildkit, "run_command", query):
            with self.assertRaisesRegex(buildkit.ImageBuildError, "Cannot determine"):
                await client.disk_usage()
        self.assertEqual(client._usage_partitions, ((),))

    async def test_single_oversized_record_has_bounded_retries(self):
        calls = []

        async def query(arguments, **kwargs):
            calls.append(arguments)
            raise buildkit.ImageBuildError("ResourceExhausted: received message larger than max")

        client = buildkit.BuildKitClient({})
        with patch.object(buildkit, "run_command", query):
            with self.assertRaisesRegex(buildkit.ImageBuildError, "bounded queries"):
                await client.disk_usage()
        self.assertLessEqual(len(calls), 64)
        self.assertEqual(client._usage_partitions, ((),))

    async def test_usage_cancellation_keeps_previous_complete_partition(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def query(arguments, **kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        client = buildkit.BuildKitClient({})
        with patch.object(buildkit, "run_command", query):
            task = asyncio.create_task(client.disk_usage())
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(client._usage_partitions, ((),))


class PipeTests(unittest.IsolatedAsyncioTestCase):
    async def test_abrupt_lease_write_keeps_previous_complete_record(self):
        child = """
import asyncio
import os
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[2])
from test_build_diagnostics import buildkit
original_write = Path.write_text

def interrupted_write(path, *arguments, **keywords):
    if 'process.json' in path.name:
        with path.open('w') as stream:
            stream.write('{')
            stream.flush()
            os._exit(77)
    return original_write(path, *arguments, **keywords)

Path.write_text = interrupted_write
asyncio.run(buildkit.run_command(
    [sys.executable, '-I', '-c', 'pass'],
    process_directory=Path(sys.argv[1]),
))
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            marker = directory / "process.json"
            marker.write_text('{"state": "idle"}', encoding="utf-8")
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-I", "-c", child, temporary, str(pathlib.Path(__file__).parent)],
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 77, result.stderr.decode())
            self.assertEqual(json.loads(marker.read_text()), {"state": "idle"})
            self.assertTrue(list(directory.glob(".process.json.*.tmp")))

    async def test_split_rawjson_and_partial_utf8(self):
        progress = progress_module.BuildProgress()
        event = (log_event("\u4e2d".encode()) + "\n").encode()
        child = (
            "import os,time; "
            f"data={event!r}; "
            "[(os.write(1,data[i:i+1]),time.sleep(.001)) for i in range(len(data))]"
        )
        await buildkit.run_command([sys.executable, "-I", "-c", child], on_line=progress.consume)
        self.assertEqual(progress.snapshot()["log_tail"], "\u4e2d\n")

    async def test_long_line_preserves_every_byte_and_utf8(self):
        pieces = []
        expected = "x" * (1024 * 1024 - 1) + "\u20ac" + "y" * 100000
        child = (
            "import os,time; "
            "os.write(1,b'x'*(1024*1024-1)+b'\\xe2\\x82'); time.sleep(.1); "
            "os.write(1,b'\\xac'+b'y'*100000+b'\\n')"
        )
        await buildkit.run_command([sys.executable, "-I", "-c", child], on_line=pieces.append)
        self.assertEqual("".join(pieces), expected)

    async def test_output_limit_and_failure_diagnostic(self):
        with self.assertRaisesRegex(buildkit.ImageBuildError, "too much data"):
            await buildkit.run_command([
                sys.executable, "-I", "-c", "import os; os.write(1,b'x'*(4*1024*1024+1))",
            ])
        with self.assertRaisesRegex(buildkit.ImageBuildError, "known failure"):
            await buildkit.run_command([
                sys.executable, "-I", "-c", "import sys; print('known failure',file=sys.stderr); sys.exit(3)",
            ], on_line=lambda line: None)

    async def test_cancellation_finishes_process_cleanup(self):
        reached_output = asyncio.Event()
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            task = asyncio.create_task(buildkit.run_command([
                sys.executable, "-I", "-u", "-c", "import time; print('ready'); time.sleep(30)",
            ], on_line=lambda line: reached_output.set(), process_directory=directory))
            await asyncio.wait_for(reached_output.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=10)
            self.assertEqual(json.loads((directory / "process.json").read_text())["state"], "idle")

    async def test_two_mebibytes_of_empty_lines_remain_bounded_and_responsive(self):
        progress = progress_module.BuildProgress()
        finished = asyncio.Event()
        maximum_lag = 0.0

        async def heartbeat():
            nonlocal maximum_lag
            while not finished.is_set():
                started = time.perf_counter()
                await asyncio.sleep(.01)
                maximum_lag = max(maximum_lag, time.perf_counter() - started - .01)

        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)

            async def flush():
                while not finished.is_set():
                    await asyncio.to_thread(progress.persist, directory)
                    await asyncio.sleep(.01)

            tasks = [asyncio.create_task(heartbeat()), asyncio.create_task(flush())]
            started = time.perf_counter()
            try:
                await buildkit.run_command([
                    sys.executable, "-I", "-c",
                    "import os; block=b'\\n'*65536; [(os.write(1,block),os.write(2,block)) for _ in range(16)]",
                ], on_line=progress.append)
            finally:
                finished.set()
                await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - started
            progress.persist(directory)
            self.assertEqual((directory / "output.txt").read_bytes(), b"\n" * (2 * 1024 * 1024))
            self.assertLessEqual(len(progress.logs), progress_module.LOG_CHUNK_COUNT)
            self.assertLess(maximum_lag, 1.0)
            print(json.dumps({
                "case": "synthetic_empty_lines_2MiB",
                "seconds": elapsed,
                "max_loop_lag_seconds": maximum_lag,
                "retained_blocks": len(progress.logs),
                "retained_bytes": progress.log_size,
                "scope": "local subprocess output, not a production BuildKit workload",
            }))


if __name__ == "__main__":
    unittest.main(verbosity=2)
