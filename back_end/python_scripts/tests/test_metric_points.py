"""Malformed producer data cannot poison the remaining metric streams."""

import ast
import asyncio
import glob
import importlib.util
import io
import json
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import anyio.to_thread
import httpx
from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.responses import Response

SERVER = Path(__file__).resolve().parents[2] / "server"
spec = importlib.util.spec_from_file_location(
    "metric_points_test",
    SERVER / "_metric_points.py",
)
points = importlib.util.module_from_spec(
    spec,
)
spec.loader.exec_module(
    points,
)
worker_spec = importlib.util.spec_from_file_location(
    "metric_worker_test",
    SERVER / "_blocking_io.py",
)
worker = importlib.util.module_from_spec(
    worker_spec,
)
worker_spec.loader.exec_module(
    worker,
)


def point(
    **changes: Any,
) -> dict:
    return {"name": "train.loss", "value": 0.5, "time_unix_ms": 1000, **changes}


def encode(
    value: Any,
) -> bytes:
    return (
        json.dumps(
            value,
        ).encode()
        + b"\n"
    )


async def wait_for_event(
    event: threading.Event,
) -> None:
    async with asyncio.timeout(5):
        while not event.is_set():
            await asyncio.sleep(0.001)


class MetricPointTests(unittest.TestCase):
    def test_valid_metadata_and_legacy_gauge_default_remain_readable(self):
        for item in (
            point(),
            point(
                kind="counter",
                labels={"rank": "0"},
                step=12,
                step_domain="optimizer",
                unit="seconds",
            ),
            point(
                labels=None,
                step=None,
                step_domain=None,
                unit=None,
            ),
        ):
            self.assertEqual(
                points.parse_point(
                    encode(
                        item,
                    ),
                ),
                item,
            )

    def test_malformed_shapes_and_nonfinite_numbers_are_ignored(self):
        invalid = [[], 42, None, True, "text"]
        for change in (
            {"name": []},
            {"name": "\ud800"},
            {"value": None},
            {"value": True},
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": 10**1000},
            {"time_unix_ms": "1000"},
            {"time_unix_ms": None},
            {"time_unix_ms": -1},
            {"step": []},
            {"step": True},
            {"step_domain": {}},
            {"unit": []},
            {"labels": "rank0"},
            {"labels": {"rank": ["0"]}},
            {"labels": {"rank": "\ud800"}},
            {"kind": []},
        ):
            invalid.append(
                point(
                    **change,
                ),
            )
        for item in invalid:
            with self.subTest(
                item=repr(item)[:80],
            ):
                self.assertIsNone(
                    points.parse_point(
                        encode(
                            item,
                        ),
                    ),
                )

    def test_broken_encoding_and_deep_json_do_not_stop_the_reader(self):
        for raw in (b"\xff", b"{", b"[" * 1500, b'{"value":' + b"9" * 5000 + b"}"):
            self.assertIsNone(
                points.parse_point(
                    raw,
                ),
            )

    def test_oversized_line_is_drained_in_bounded_reads(self):
        class BoundedStream(io.BytesIO):
            def readline(
                self,
                size: int = -1,
            ) -> bytes:
                if not 0 < size <= points.MAX_LINE_BYTES + 1:
                    raise AssertionError("unbounded read")
                return super().readline(
                    size,
                )

        data = (
            encode(point())
            + b"x" * (points.MAX_LINE_BYTES * 3)
            + b"\n"
            + encode(
                point(
                    value=2,
                ),
            )
        )
        self.assertEqual(
            list(
                points.iter_points(
                    BoundedStream(data),
                    len(data),
                ),
            ),
            [point(), point(value=2)],
        )

    def test_snapshot_only_contains_records_committed_by_newline(self):
        first = encode(point())
        data = first + encode(point(value=2))
        for size, expected in (
            (len(first) - 1, []),
            (len(first), [point()]),
            (len(data), [point(), point(value=2)]),
        ):
            self.assertEqual(
                list(
                    points.iter_points(
                        io.BytesIO(data),
                        size,
                    ),
                ),
                expected,
            )

    def test_query_labels_keep_no_filter_fallback_for_invalid_input(self):
        for value in (None, "", "{", "[]", "true", '"label"', '{"rank": []}'):
            self.assertIsNone(
                points.parse_labels(
                    value,
                ),
            )
        self.assertEqual(
            points.parse_labels(
                '{"rank": "0"}',
            ),
            {"rank": "0"},
        )
        self.assertEqual(
            points.parse_labels(
                "{}",
            ),
            {},
        )


class MetricReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(
            temporary.cleanup,
        )
        self.root = Path(temporary.name)
        metrics = self.root / "jobs/example/metrics"
        metrics.mkdir(
            parents=True,
        )
        payload = b"".join(
            encode(item)
            for item in (
                [],
                point(value=float("nan")),
                point(labels={"rank": ["0"]}),
                point(value=2, labels={"rank": "0"}, time_unix_ms=2000),
                point(value=1, labels={"rank": "0"}),
                point(name="system.memory.used_bytes", value=2048),
            )
        )
        (metrics / "system.jsonl").write_bytes(
            payload,
        )
        source = SERVER / "routers/metrics.py"
        tree = ast.parse(
            source.read_text(),
        )
        names = {
            "_metrics_dir_for_job",
            "_iter_points",
            "_stream_key",
            "_build_streams",
            "list_metric_streams",
            "_shape_point",
            "_downsample",
            "query_metrics",
            "_metric_priority",
            "get_initial_metrics",
            "_labels_legend_key",
            "render_metric_chart",
            "_render_metric_chart_response",
        }
        # Keep the real FastAPI registration and limiter, excluding startup/config IO.
        tree.body = [
            node
            for node in tree.body
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names
            )
            or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "_chart_semaphore"
                    for target in node.targets
                )
            )
        ]
        namespace = {
            "Dict": Dict,
            "Any": Any,
            "List": List,
            "Tuple": Tuple,
            "Optional": Optional,
            "Iterable": Iterable,
            "Iterator": Iterator,
            "MetricPoint": points.MetricPoint,
            "models": SimpleNamespace(User=object),
            "Depends": Depends,
            "get_current_user": lambda: None,
            "Query": Query,
            "Response": Response,
            "router": APIRouter(),
            "os": os,
            "glob": glob,
            "json": json,
            "asyncio": asyncio,
            "_run_blocking_io": worker._run_blocking_io,
            "magnus_workspace_path": str(self.root),
            "open_protocol_file": lambda root, relative: open(
                Path(root) / relative, "rb"
            ),
            "iter_points": points.iter_points,
            "parse_labels": points.parse_labels,
        }
        exec(
            compile(
                tree,
                str(source),
                "exec",
            ),
            namespace,
        )
        self.reader = namespace
        self.app = FastAPI()
        self.app.include_router(
            namespace["router"],
        )

    def test_bad_line_does_not_hide_other_streams(self):
        streams = self.reader["list_metric_streams"](
            "example",
        )
        self.assertEqual(
            [(item["name"], item["point_count"], item["kind"]) for item in streams],
            [("train.loss", 2, "gauge"), ("system.memory.used_bytes", 1, "gauge")],
        )

    def test_query_preserves_selection_sorting_and_response_shape(self):
        result = self.reader["query_metrics"](
            "example",
            "train.loss",
            labels='{"rank":"0"}',
            max_points=2000,
        )
        self.assertEqual(
            result,
            {
                "name": "train.loss",
                "points": [
                    {"value": 1, "time_unix_ms": 1000, "labels": {"rank": "0"}},
                    {"value": 2, "time_unix_ms": 2000, "labels": {"rank": "0"}},
                ],
            },
        )
        json.dumps(
            result,
            allow_nan=False,
        )
        self.assertEqual(
            self.reader["query_metrics"](
                "example",
                "train.loss",
                labels="[]",
                since_ms=1500,
                max_points=2000,
            )["points"],
            result["points"][1:],
        )

    def test_initial_response_keeps_default_points_after_scanning_streams(self):
        result = self.reader["get_initial_metrics"](
            "example",
            max_points=1,
        )
        self.assertEqual(
            result,
            {
                "streams": self.reader["list_metric_streams"]("example"),
                "default_metric": "train.loss",
                "default_data": [
                    {
                        "labels": {"rank": "0"},
                        "points": [
                            {"value": 2, "time_unix_ms": 2000, "labels": {"rank": "0"}}
                        ],
                    }
                ],
            },
        )

    def test_render_reads_groups_and_plots_outside_the_event_loop(self):
        thread_ids = []
        original_iter = self.reader["_iter_points"]
        original_downsample = self.reader["_downsample"]

        def tracked_iter(
            *args: Any,
        ) -> Iterator:
            thread_ids.append(threading.get_ident())
            return original_iter(*args)

        def tracked_downsample(
            *args: Any,
        ) -> list:
            thread_ids.append(threading.get_ident())
            return original_downsample(*args)

        def render(
            *args: Any,
        ) -> bytes:
            thread_ids.append(threading.get_ident())
            return b"png"

        self.reader.update(
            _iter_points=tracked_iter,
            _downsample=tracked_downsample,
            _render_chart_png=render,
        )

        async def exercise() -> None:
            loop_thread = threading.get_ident()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://test",
                trust_env=False,
            ) as client:
                response = await client.get(
                    "/jobs/example/metrics/render",
                    params={"name": "train.loss"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"png")
            self.assertEqual(len(thread_ids), 3)
            self.assertNotIn(loop_thread, thread_ids)

        asyncio.run(
            exercise(),
        )

    def test_concurrent_render_branches_serialize_matplotlib_access(self):
        active = 0
        maximum = 0
        calls = 0
        state_lock = threading.Lock()

        def render(
            *args: Any,
        ) -> bytes:
            nonlocal active, maximum, calls
            with state_lock:
                active += 1
                calls += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.025)
                return b"png"
            finally:
                with state_lock:
                    active -= 1

        self.reader.update(
            _render_chart_png=render,
            _no_data_png=render,
        )

        async def exercise() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://test",
                trust_env=False,
            ) as client:
                responses = await asyncio.gather(
                    *(
                        client.get(
                            f"/jobs/{job_id}/metrics/render",
                            params={"name": name},
                        )
                        for job_id, name in (
                            ("example", "train.loss"),
                            ("example", "train.loss"),
                            ("example", "missing"),
                            ("missing", "train.loss"),
                        )
                    ),
                )
            self.assertTrue(
                all(
                    response.status_code == 200 and response.content == b"png"
                    for response in responses
                ),
            )

        asyncio.run(
            exercise(),
        )
        self.assertEqual(calls, 4)
        self.assertEqual(maximum, 1)

    def test_waiting_charts_do_not_exhaust_the_shared_http_pool(self):
        entered = threading.Event()
        release = threading.Event()

        def render(
            *args: Any,
        ) -> bytes:
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test did not release renderer")
            return b"png"

        self.reader["_render_chart_png"] = render

        @self.app.get("/health")
        def health() -> dict:
            return {"ok": True}

        async def exercise() -> None:
            limiter = anyio.to_thread.current_default_thread_limiter()
            original_tokens = limiter.total_tokens
            limiter.total_tokens = 1
            requests = []
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=self.app),
                    base_url="http://test",
                    trust_env=False,
                ) as client:
                    try:
                        requests.append(
                            asyncio.create_task(
                                client.get(
                                    "/jobs/example/metrics/render",
                                    params={"name": "train.loss"},
                                )
                            )
                        )
                        await wait_for_event(entered)
                        requests.append(
                            asyncio.create_task(
                                client.get(
                                    "/jobs/example/metrics/render",
                                    params={"name": "train.loss"},
                                )
                            )
                        )
                        await asyncio.sleep(0.025)
                        response = await asyncio.wait_for(
                            client.get("/health"),
                            timeout=2,
                        )
                        self.assertEqual(response.json(), {"ok": True})
                        self.assertTrue(all(not task.done() for task in requests))
                    finally:
                        release.set()
                        await asyncio.gather(
                            *requests,
                            return_exceptions=True,
                        )
            finally:
                limiter.total_tokens = original_tokens

        asyncio.run(
            exercise(),
        )

    def test_cancelled_chart_keeps_render_slot_until_its_worker_finishes(self):
        entered = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()

        def render(
            *args: Any,
        ) -> bytes:
            if entered.is_set():
                second_entered.set()
            else:
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("test did not release renderer")
            return b"png"

        self.reader["_render_chart_png"] = render

        async def request() -> Response:
            return await self.reader["render_metric_chart"](
                "example",
                "train.loss",
                max_points=2000,
                format="png",
            )

        async def exercise() -> None:
            requests = [asyncio.create_task(request())]
            try:
                await wait_for_event(entered)
                requests[0].cancel()
                requests.append(asyncio.create_task(request()))
                await asyncio.sleep(0.025)
                self.assertFalse(requests[0].done())
                self.assertFalse(second_entered.is_set())
            finally:
                release.set()
                results = await asyncio.gather(
                    *requests,
                    return_exceptions=True,
                )
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertEqual(results[1].body, b"png")
            self.assertTrue(second_entered.is_set())

        asyncio.run(
            exercise(),
        )


if __name__ == "__main__":
    unittest.main()
