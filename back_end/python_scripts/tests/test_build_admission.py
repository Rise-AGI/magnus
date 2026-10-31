"""Admission and teardown interleavings without a running builder or backend."""

import asyncio
import hashlib
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from test_image_build_concurrency import SERVER, load_coordinator


class FairAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        self.admission_type = load_coordinator().BuildAdmission

    async def test_user_backlog_rotates_without_reordering_its_own_requests(
        self,
    ) -> None:
        admission = self.admission_type(
            capacity=1,
            per_user=1,
        )
        await admission.acquire("a")
        requests = [
            (name, asyncio.create_task(admission.acquire(name)))
            for name in ("a", "a", "a", "b", "b")
        ]
        await asyncio.sleep(0)
        admission.release("a")
        served = set()
        for index in (0, 3, 1, 4, 2):
            name, task = requests[index]
            await asyncio.wait_for(
                task,
                1,
            )
            served.add(index)
            for other_index, (_, other) in enumerate(requests):
                if other_index not in served:
                    self.assertFalse(other.done())
            admission.release(name)

    async def test_saturated_user_does_not_hold_another_users_free_slot(
        self,
    ) -> None:
        admission = self.admission_type(
            capacity=2,
            per_user=1,
        )
        await admission.acquire("a")
        blocked = asyncio.create_task(admission.acquire("a"))
        other = asyncio.create_task(admission.acquire("b"))
        await asyncio.wait_for(
            other,
            1,
        )
        self.assertFalse(blocked.done())
        admission.release("b")
        self.assertFalse(blocked.done())
        admission.release("a")
        await asyncio.wait_for(
            blocked,
            1,
        )
        admission.release("a")

    async def test_cancellation_after_grant_returns_capacity(
        self,
    ) -> None:
        admission = self.admission_type(
            capacity=1,
            per_user=1,
        )
        await admission.acquire("a")
        waiting = asyncio.create_task(admission.acquire("b"))
        await asyncio.sleep(0)
        admission.release("a")
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        await asyncio.wait_for(
            admission.acquire("c"),
            1,
        )
        admission.release("c")

    async def test_dispatch_can_prune_cancelled_head_before_task_unwinds(
        self,
    ) -> None:
        admission = self.admission_type(
            capacity=1,
            per_user=1,
        )
        await admission.acquire("a")
        waiting = [asyncio.create_task(admission.acquire("b")) for _ in range(3)]
        await asyncio.sleep(0)
        waiting[0].cancel()
        admission.release("a")
        with self.assertRaises(asyncio.CancelledError):
            await waiting[0]
        for task in waiting[1:]:
            await asyncio.wait_for(
                task,
                1,
            )
            admission.release("b")


class PreparationAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="magnus-admission-",
            dir=Path.home().anchor if os.name == "nt" else None,
        )
        self.base = Path(self.temporary.name)
        spec = importlib.util.spec_from_file_location(
            "admission_progress_fixture",
            SERVER / "_resource_manager/_build_progress.py",
        )
        progress = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(progress)
        self.module = load_coordinator(
            progress_type=progress.BuildProgress,
        )
        self.module.magnus_config = {
            "server": {
                "root": str(self.base / "server"),
                "ephemeral_root": str(self.base / "ephemeral"),
            },
            "execution": {
                "image_build": {
                    "enabled": True,
                    "platform": "linux/amd64",
                    "max_concurrent": 2,
                    "max_concurrent_conversions": 1,
                    "timeout_seconds": 10,
                    "cache_size": "8M",
                    "scratch_size": "2M",
                    "min_free_space": "0",
                }
            },
        }
        self.manager = self.module.ImageBuildManager()
        self.manager.client.build = self.build
        self.manager._import_archive = self.convert
        self.building: dict[str, asyncio.Event] = {}
        self.converting: dict[str, asyncio.Event] = {}
        self.build_gates: dict[str, asyncio.Event] = {}
        self.conversion_gates: dict[str, asyncio.Event] = {}
        self.calls: list[str] = []
        self.waiters: list[asyncio.Task] = []
        self.thread_gates: list[threading.Event] = []

    async def asyncTearDown(
        self,
    ) -> None:
        for event in (
            *self.build_gates.values(),
            *self.conversion_gates.values(),
            *self.thread_gates,
        ):
            event.set()
        for task in self.waiters:
            task.cancel()
        await self.manager.shutdown()
        await asyncio.gather(
            *self.waiters,
            return_exceptions=True,
        )
        self.temporary.cleanup()

    def submit(
        self,
        recipe: str,
        *,
        user_id: str = "user",
    ) -> asyncio.Task:
        job_id = f"{len(self.waiters) + 1:016x}"
        self.building.setdefault(
            recipe,
            asyncio.Event(),
        )
        self.converting.setdefault(
            recipe,
            asyncio.Event(),
        )
        self.build_gates.setdefault(
            recipe,
            asyncio.Event(),
        )
        self.conversion_gates.setdefault(
            recipe,
            asyncio.Event(),
        )
        task = asyncio.create_task(
            self.manager.ensure(
                job_id,
                recipe,
                user_id=user_id,
            )
        )
        self.waiters.append(task)
        return task

    async def build(
        self,
        directory: Path,
        tag: str,
        on_line,
    ) -> Path:
        recipe = (directory / "recipe/Dockerfile").read_text()
        self.calls.append(recipe)
        self.building[recipe].set()
        await self.build_gates[recipe].wait()
        archive = directory / "image.tar"
        archive.write_bytes(b"archive")
        return archive

    async def convert(
        self,
        operation,
        directory: Path,
        archive: Path,
        tag: str,
    ) -> dict:
        self.converting[operation.dockerfile].set()
        await self.conversion_gates[operation.dockerfile].wait()
        return {"digest": "sha256:" + hashlib.sha256(operation.dockerfile.encode()).hexdigest()}

    async def reached(
        self,
        event: asyncio.Event,
    ) -> None:
        await asyncio.wait_for(
            event.wait(),
            2,
        )

    async def test_slow_build_allows_an_unrelated_build_and_import(
        self,
    ) -> None:
        slow = self.submit("slow")
        await self.reached(self.building["slow"])
        fast = self.submit(
            "fast",
            user_id="other",
        )
        self.build_gates["fast"].set()
        self.conversion_gates["fast"].set()
        await asyncio.wait_for(
            fast,
            2,
        )
        self.assertFalse(slow.done())

    async def test_conversion_is_serial_but_does_not_block_other_solves(
        self,
    ) -> None:
        first = self.submit("first")
        self.build_gates["first"].set()
        await self.reached(self.converting["first"])
        second = self.submit("second")
        await self.reached(self.building["second"])
        self.build_gates["second"].set()
        await asyncio.sleep(0)
        self.assertFalse(self.converting["second"].is_set())
        self.conversion_gates["first"].set()
        await asyncio.wait_for(
            first,
            2,
        )
        await self.reached(self.converting["second"])
        self.conversion_gates["second"].set()
        await asyncio.wait_for(
            second,
            2,
        )

    async def test_cleanup_keeps_capacity_even_after_repeated_cancellation(
        self,
    ) -> None:
        self.manager.admission = self.module.BuildAdmission(
            capacity=1,
            per_user=1,
        )
        cleaning = threading.Event()
        release = threading.Event()
        self.thread_gates.append(release)
        original = self.manager._cleanup_directory

        def cleanup(
            directory: Path,
            *,
            allow_missing: bool = False,
        ) -> bool:
            if (directory / "recipe/Dockerfile").read_text() == "first":
                cleaning.set()
                release.wait(3)
            return original(
                directory,
                allow_missing=allow_missing,
            )

        self.manager._cleanup_directory = cleanup
        first = self.submit("first")
        self.build_gates["first"].set()
        self.conversion_gates["first"].set()
        self.assertTrue(
            await asyncio.to_thread(
                cleaning.wait,
                2,
            )
        )
        second = self.submit("second")
        operation = self.manager.jobs["0000000000000001"]
        operation.task.cancel()
        await asyncio.sleep(0)
        operation.task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(self.building["second"].is_set())
        release.set()
        await asyncio.wait_for(
            first,
            2,
        )
        await self.reached(self.building["second"])
        self.build_gates["second"].set()
        self.conversion_gates["second"].set()
        await asyncio.wait_for(
            second,
            2,
        )

    async def test_queue_timeout_creates_no_build_scratch_and_preserves_holder(
        self,
    ) -> None:
        self.manager.admission = self.module.BuildAdmission(
            capacity=1,
            per_user=1,
        )
        first = self.submit("first")
        await self.reached(self.building["first"])
        before = set(self.manager.temporary_root.iterdir())
        self.manager.config["queue_timeout_seconds"] = 0.03
        second = self.submit("second")
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "queue wait exceeded",
        ):
            await asyncio.wait_for(
                second,
                2,
            )
        self.assertEqual(
            set(self.manager.temporary_root.iterdir()),
            before,
        )
        self.assertFalse(first.done())
        self.assertNotIn(
            "second",
            self.calls,
        )
        progress = json.loads(
            (self.manager.diagnostics_root / "0000000000000002/progress.json").read_text()
        )
        self.assertEqual(
            progress["phase"],
            "failed",
        )

    async def test_shared_recipe_uses_one_slot_and_survives_one_waiter_leaving(
        self,
    ) -> None:
        first = self.submit(
            "shared",
            user_id="a",
        )
        await self.reached(self.building["shared"])
        second = self.submit(
            "shared",
            user_id="b",
        )
        async with asyncio.timeout(2):
            while len(self.manager.jobs) < 2:
                await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.build_gates["shared"].set()
        self.conversion_gates["shared"].set()
        await asyncio.wait_for(
            second,
            2,
        )
        self.assertEqual(
            self.calls,
            ["shared"],
        )

    async def test_ready_image_bypasses_full_admission(
        self,
    ) -> None:
        self.manager.admission = self.module.BuildAdmission(
            capacity=1,
            per_user=1,
        )
        slow = self.submit("slow")
        await self.reached(self.building["slow"])
        original = self.manager._cached_result

        async def cached(
            operation,
        ) -> dict | None:
            if operation.dockerfile == "warm":
                return {"digest": "sha256:" + "a" * 64}
            return await original(operation)

        self.manager._cached_result = cached
        warm = self.submit("warm")
        await asyncio.wait_for(
            warm,
            2,
        )
        self.assertFalse(slow.done())
        self.assertNotIn(
            "warm",
            self.calls,
        )

    async def test_cancelled_conversion_waiter_does_not_release_anothers_slot(
        self,
    ) -> None:
        first = self.submit("first")
        self.build_gates["first"].set()
        await self.reached(self.converting["first"])
        second = self.submit("second")
        self.build_gates["second"].set()
        await self.reached(self.building["second"])
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        third = self.submit("third")
        self.build_gates["third"].set()
        await self.reached(self.building["third"])
        await asyncio.sleep(0)
        self.assertFalse(self.converting["third"].is_set())
        self.conversion_gates["first"].set()
        await asyncio.wait_for(
            first,
            2,
        )
        await self.reached(self.converting["third"])
        self.conversion_gates["third"].set()
        await asyncio.wait_for(
            third,
            2,
        )

    async def test_unconfirmed_process_preserves_scratch_and_blocks_new_cold_builds(
        self,
    ) -> None:
        first = self.submit("first")
        await self.reached(self.building["first"])
        directory = next(self.manager.temporary_root.glob("build-*"))
        (directory / "process.json").write_text(json.dumps({"state": "spawning"}))
        self.build_gates["first"].set()
        self.conversion_gates["first"].set()
        await asyncio.wait_for(
            first,
            2,
        )
        self.assertTrue(directory.is_dir())
        second = self.submit("second")
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "cleanup could not confirm",
        ):
            await asyncio.wait_for(
                second,
                2,
            )
        self.assertEqual(
            self.calls,
            ["first"],
        )

    async def test_failed_conversion_releases_both_limits_for_next_build(
        self,
    ) -> None:
        self.manager.admission = self.module.BuildAdmission(
            capacity=1,
            per_user=1,
        )
        original = self.manager._import_archive

        async def convert(
            operation,
            directory: Path,
            archive: Path,
            tag: str,
        ) -> dict:
            if operation.dockerfile == "bad":
                raise RuntimeError("injected conversion failure")
            return await original(
                operation,
                directory,
                archive,
                tag,
            )

        self.manager._import_archive = convert
        failed = self.submit("bad")
        self.build_gates["bad"].set()
        with self.assertRaisesRegex(
            RuntimeError,
            "injected conversion failure",
        ):
            await asyncio.wait_for(
                failed,
                2,
            )
        good = self.submit("good")
        self.build_gates["good"].set()
        self.conversion_gates["good"].set()
        await asyncio.wait_for(
            good,
            2,
        )

    async def test_cleanup_exception_blocks_a_waiting_conversion(
        self,
    ) -> None:
        original = self.manager._cleanup_directory

        def cleanup(
            directory: Path,
            *,
            allow_missing: bool = False,
        ) -> bool:
            if (directory / "recipe/Dockerfile").read_text() == "first":
                raise OSError("injected cleanup I/O failure")
            return original(
                directory,
                allow_missing=allow_missing,
            )

        self.manager._cleanup_directory = cleanup
        first = self.submit("first")
        self.build_gates["first"].set()
        await self.reached(self.converting["first"])
        second = self.submit("second")
        self.build_gates["second"].set()
        await self.reached(self.building["second"])
        self.conversion_gates["first"].set()
        with self.assertRaisesRegex(
            OSError,
            "injected cleanup I/O failure",
        ):
            await asyncio.wait_for(
                first,
                2,
            )
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "cleanup could not confirm",
        ):
            await asyncio.wait_for(
                second,
                2,
            )
        self.assertFalse(self.converting["second"].is_set())
        third = self.submit("third")
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "cleanup could not confirm",
        ):
            await asyncio.wait_for(
                third,
                2,
            )
        self.assertNotIn(
            "third",
            self.calls,
        )

    async def test_cleanup_fault_during_admission_io_prevents_next_solve(
        self,
    ) -> None:
        first = self.submit("first")
        self.build_gates["first"].set()
        await self.reached(self.converting["first"])
        directory = next(self.manager.temporary_root.glob("build-*"))
        (directory / "process.json").write_text(json.dumps({"state": "spawning"}))
        collecting = asyncio.Event()
        release = asyncio.Event()
        self.build_gates["collection"] = release
        original = self.manager._collect_cache

        async def collect() -> None:
            collecting.set()
            await release.wait()
            await original()

        self.manager._collect_cache = collect
        second = self.submit("second")
        await self.reached(collecting)
        self.conversion_gates["first"].set()
        await asyncio.wait_for(
            first,
            2,
        )
        release.set()
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "cleanup could not confirm",
        ):
            await asyncio.wait_for(
                second,
                2,
            )
        self.assertEqual(
            self.calls,
            ["first"],
        )

    async def test_execution_deadline_also_bounds_prebuild_collection(
        self,
    ) -> None:
        release = asyncio.Event()
        self.build_gates["collection"] = release
        original = self.manager._collect_cache

        async def collect() -> None:
            await release.wait()
            await original()

        self.manager._collect_cache = collect
        self.manager.config["timeout_seconds"] = 0.03
        failed = self.submit("blocked")
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "Image build exceeded",
        ):
            await asyncio.wait_for(
                failed,
                2,
            )
        self.assertEqual(
            self.calls,
            [],
        )
        self.assertEqual(
            list(self.manager.temporary_root.glob("build-*")),
            [],
        )
        self.manager.config["timeout_seconds"] = 10
        release.set()
        good = self.submit("good")
        self.build_gates["good"].set()
        self.conversion_gates["good"].set()
        await asyncio.wait_for(
            good,
            2,
        )

    async def test_failed_mkdir_without_a_process_does_not_close_admission(
        self,
    ) -> None:
        original = self.manager._prepare_directory

        def prepare(
            directory: Path,
            recipe: str,
        ) -> None:
            if recipe == "bad":
                raise OSError("injected mkdir failure")
            original(
                directory,
                recipe,
            )

        self.manager._prepare_directory = prepare
        failed = self.submit("bad")
        with self.assertRaisesRegex(
            OSError,
            "injected mkdir failure",
        ):
            await asyncio.wait_for(
                failed,
                2,
            )
        self.assertIsNone(self.manager.cleanup_error)
        good = self.submit("good")
        self.build_gates["good"].set()
        self.conversion_gates["good"].set()
        await asyncio.wait_for(
            good,
            2,
        )


if __name__ == "__main__":
    unittest.main()
