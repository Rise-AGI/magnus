"""Ordinary image identity, eviction and pull ownership without a runtime daemon."""

import ast
import asyncio
import importlib.util
import json
import logging
import os
import runpy
import sys
import tempfile
import threading
import types
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

SERVER = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
    / "server"
)
SOURCE = SERVER / "_resource_manager"
package = types.ModuleType(
    "isolated_image_cache",
)
package.__path__ = [
    str(
        SERVER,
    )
]
sys.modules[package.__name__] = package
resource_package = types.ModuleType(
    "isolated_image_cache.resources",
)
resource_package.__path__ = [
    str(
        SOURCE,
    )
]
sys.modules[resource_package.__name__] = resource_package


def load(
    name: str,
):
    spec = importlib.util.spec_from_file_location(
        f"{resource_package.__name__}.{name}",
        SOURCE / f"{name}.py",
    )
    module = importlib.util.module_from_spec(
        spec,
    )
    sys.modules[spec.name] = module
    spec.loader.exec_module(
        module,
    )
    return module


cache_module = load(
    "_image_cache",
)
commands = load(
    "_buildkit",
)
pull_scratch = load(
    "_pull_scratch",
)
blocking_io = runpy.run_path(
    str(
        SERVER / "_blocking_io.py",
    ),
)["_run_blocking_io"]


class ImageCacheTests(unittest.TestCase):
    def setUp(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(
            temporary.cleanup,
        )
        self.root = Path(
            temporary.name,
        )
        self.cache = cache_module.ImageCache(
            str(
                self.root,
            ),
            size_limit=8,
        )

    def write_image(
        self,
        image: str,
    ) -> Path:
        path = Path(
            self.cache.path(
                image,
            ),
        )
        path.write_bytes(
            b"test SIF",
        )
        return path

    def test_lossy_legacy_collisions_and_long_uris_have_distinct_bounded_names(
        self,
    ) -> None:
        first = "docker://registry/a_b/c:tag"
        second = "docker://registry/a/b_c:tag"
        self.assertEqual(
            cache_module.legacy_image_filename(
                first,
            ),
            cache_module.legacy_image_filename(
                second,
            ),
        )
        names = {
            cache_module.image_filename(
                uri,
            )
            for uri in (first, second, "oras://registry/a_b/c:tag", "x/" * 300)
        }
        self.assertEqual(
            len(
                names,
            ),
            4,
        )
        for name in names:
            self.assertLess(
                len(
                    name,
                ),
                100,
            )
            self.assertEqual(
                Path(
                    name,
                ).name,
                name,
            )

    def test_hash_namespace_cannot_be_claimed_by_a_crafted_legacy_uri(
        self,
    ) -> None:
        image = "docker://alpine:3.20"
        filename = cache_module.image_filename(
            image,
        )
        crafted_uri = filename.removesuffix(
            ".sif",
        )
        legacy = cache_module.legacy_image_filename(
            crafted_uri,
        )
        self.assertNotEqual(
            legacy,
            filename,
        )
        self.cache.restore_legacy_names(
            [(crafted_uri, legacy)],
        )
        self.assertNotEqual(
            self.cache.path(
                image,
            ),
            self.cache.path(
                crafted_uri,
            ),
        )

    def test_legacy_path_is_reused_only_for_its_registered_owner(
        self,
    ) -> None:
        first = "docker://registry/a_b/c:tag"
        second = "docker://registry/a/b_c:tag"
        filename = cache_module.legacy_image_filename(
            first,
        )
        path = self.root / filename
        path.write_bytes(
            b"legacy",
        )
        self.cache.restore_legacy_names(
            [(first, filename)],
        )
        self.assertEqual(
            Path(
                self.cache.path(
                    first,
                ),
            ),
            path,
        )
        self.assertNotEqual(
            Path(
                self.cache.path(
                    second,
                ),
            ),
            path,
        )
        path.unlink()
        # The same path must survive repulls: queued wrappers already name it.
        self.assertEqual(
            Path(
                self.cache.path(
                    first,
                ),
            ),
            path,
        )

    def test_unregistered_legacy_file_is_not_inferred_from_a_colliding_uri(
        self,
    ) -> None:
        uri = "docker://registry/a_b/c:tag"
        legacy = self.root / cache_module.legacy_image_filename(
            uri,
        )
        legacy.write_bytes(
            b"unknown owner",
        )
        self.assertNotEqual(
            Path(
                self.cache.path(
                    uri,
                ),
            ),
            legacy,
        )
        self.cache.restore_legacy_names(
            [(uri, "../external.sif"), (uri, "unrelated.sif")],
        )
        self.assertNotEqual(
            Path(
                self.cache.path(
                    uri,
                ),
            ),
            legacy,
        )

    def test_restarted_job_protects_legacy_file_without_claiming_its_identity(
        self,
    ) -> None:
        uri = "docker://registry/a_b/c:tag"
        legacy = self.root / cache_module.legacy_image_filename(
            uri,
        )
        legacy.write_bytes(
            b"old running wrapper",
        )
        self.cache.pin(
            "restored",
            uri,
            preserve_legacy=True,
        )
        self.cache.size_limit = 0
        self.cache.collect()
        self.assertTrue(
            legacy.exists(),
        )
        self.assertNotEqual(
            Path(
                self.cache.path(
                    uri,
                ),
            ),
            legacy,
        )
        self.cache.release(
            "restored",
        )
        self.cache.collect()
        self.assertFalse(
            legacy.exists(),
        )

    def test_all_job_owners_must_release_before_eviction(
        self,
    ) -> None:
        first = self.write_image(
            "first",
        )
        second = self.write_image(
            "second",
        )
        os.utime(
            first,
            (1, 1),
        )
        os.utime(
            second,
            (2, 2),
        )
        self.cache.pin(
            "queued",
            "first",
        )
        self.cache.pin(
            "running",
            "first",
        )
        self.cache.collect()
        self.assertTrue(
            first.exists(),
        )
        self.assertFalse(
            second.exists(),
        )
        self.cache.size_limit = 0
        self.cache.release(
            "running",
        )
        self.cache.collect()
        self.assertTrue(
            first.exists(),
        )
        self.cache.release(
            "queued",
        )
        self.cache.collect()
        self.assertFalse(
            first.exists(),
        )

    def test_pin_added_during_inventory_is_checked_at_unlink(
        self,
    ) -> None:
        path = self.write_image(
            "first",
        )
        self.cache.size_limit = 0
        scanned = threading.Event()
        proceed = threading.Event()
        inventory = self.cache.inventory

        def delayed_inventory():
            rows = inventory()
            scanned.set()
            if not proceed.wait(
                2,
            ):
                raise AssertionError(
                    "collector was not released",
                )
            return rows

        with patch.object(
            self.cache,
            "inventory",
            delayed_inventory,
        ):
            thread = threading.Thread(
                target=self.cache.collect,
            )
            thread.start()
            try:
                self.assertTrue(
                    scanned.wait(
                        2,
                    ),
                )
                self.cache.pin(
                    "preparing",
                    "first",
                )
            finally:
                proceed.set()
                thread.join(
                    2,
                )
            self.assertFalse(
                thread.is_alive(),
            )
        self.assertTrue(
            path.exists(),
        )

    def test_disk_headroom_does_not_override_ownership_or_remove_partial_files(
        self,
    ) -> None:
        first = self.write_image(
            "first",
        )
        second = self.write_image(
            "second",
        )
        partial = self.root / "unfinished.sif.tmp"
        partial.write_bytes(
            b"partial",
        )
        self.cache.size_limit = 100
        self.cache.pin(
            "job",
            "first",
        )
        with patch.object(
            cache_module.shutil,
            "disk_usage",
            return_value=types.SimpleNamespace(
                free=0,
            ),
        ):
            self.cache.collect(
                target_free_bytes=1,
            )
        self.assertTrue(
            first.exists(),
        )
        self.assertFalse(
            second.exists(),
        )
        self.assertTrue(
            partial.exists(),
        )

    def test_manual_removal_respects_publishers_and_job_pins(
        self,
    ) -> None:
        path = self.write_image(
            "image",
        )
        with self.cache.hold(
            "image",
        ):
            self.assertFalse(
                self.cache.remove(
                    path.name,
                ),
            )
        self.cache.pin(
            "job",
            "image",
        )
        self.assertFalse(
            self.cache.remove(
                path.name,
            ),
        )
        self.cache.release(
            "job",
        )
        self.assertTrue(
            self.cache.remove(
                path.name,
            ),
        )
        self.assertFalse(
            path.exists(),
        )
        with self.assertRaises(
            ValueError,
        ):
            self.cache.remove(
                "../outside.sif",
            )


class ImagePullFixture:
    def __init__(
        self,
        root: Path,
    ) -> None:
        self.root = root
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.cache = cache_module.ImageCache(
            str(
                self.root,
            ),
            size_limit=100,
        )
        self.namespace = {
            "__name__": __name__,
            "logger": logging.getLogger(
                __name__,
            ),
            "_run_blocking_io": blocking_io,
            "run_command": commands.run_command,
            "ImageBuildError": commands.ImageBuildError,
            "PullScratch": pull_scratch.PullScratch,
            "process_lease_is_idle": pull_scratch.process_lease_is_idle,
            "_rewrite_image_for_mirror": lambda uri: uri,
            "MIN_FREE_BYTES_FOR_PULL": 0,
            "magnus_container_cache_path": str(
                self.root,
            ),
            "magnus_apptainer_cache_path": str(
                self.scratch,
            ),
            "disk_free_bytes": lambda path: 1024,
            "is_disk_full_stderr": lambda message: "no space left" in message.lower(),
            "disk_full_message": lambda path: "disk full",
            "is_local_mode": False,
        }
        source = SOURCE / "_images.py"
        tree = ast.parse(
            source.read_text(),
        )
        tree.body = [
            node
            for node in tree.body
            if not isinstance(
                node,
                ast.ImportFrom,
            )
            or (not node.level and node.module != "library")
        ]
        exec(
            compile(
                tree,
                str(
                    source,
                ),
                "exec",
            ),
            self.namespace,
        )
        self.manager = self.namespace["_ImagesMixin"]()
        self.manager.images = self.cache
        self.manager.image_locks = {}
        self.manager.get_sif_path = self.cache.path


@contextmanager
def image_pull_fixture() -> Iterator[ImagePullFixture]:
    with tempfile.TemporaryDirectory() as directory:
        yield ImagePullFixture(
            Path(
                directory,
            ),
        )


class ImagePullTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        fixture = self.enterContext(
            image_pull_fixture(),
        )
        self.root = fixture.root
        self.scratch = fixture.scratch
        self.cache = fixture.cache
        self.namespace = fixture.namespace
        self.manager = fixture.manager

    async def test_refreshes_use_distinct_outputs_and_preserve_legacy_partial(
        self,
    ) -> None:
        legacy = Path(
            self.cache.path(
                "uri",
            )
            + ".tmp",
        )
        legacy.write_bytes(
            b"possibly still owned by a previous process",
        )
        outputs = []

        async def command(
            arguments,
            **keywords,
        ):
            output = Path(
                arguments[2],
            )
            outputs.append(
                output,
            )
            output.write_bytes(
                b"complete SIF",
            )
            return ""

        self.namespace["run_command"] = command
        for _ in range(
            2,
        ):
            self.assertEqual(
                await self.manager.ensure_image(
                    "uri",
                    force=True,
                ),
                (True, None),
            )
        self.assertEqual(
            len(
                set(
                    outputs,
                ),
            ),
            2,
        )
        self.assertEqual(
            legacy.read_bytes(),
            b"possibly still owned by a previous process",
        )
        self.assertTrue(
            all(not output.exists() for output in outputs),
        )

    async def test_concurrent_pulls_share_one_file_and_keep_job_pin(
        self,
    ) -> None:
        calls = []
        entered, finish = asyncio.Event(), asyncio.Event()

        async def command(
            arguments,
            **keywords,
        ):
            calls.append(
                arguments,
            )
            entered.set()
            await finish.wait()
            Path(
                arguments[2],
            ).write_bytes(
                b"complete SIF",
            )
            return ""

        self.namespace["run_command"] = command
        self.cache.pin(
            "queued",
            "uri",
        )
        first = asyncio.create_task(
            self.manager.ensure_image(
                "uri",
            ),
        )
        await asyncio.wait_for(
            entered.wait(),
            2,
        )
        second = asyncio.create_task(
            self.manager.ensure_image(
                "uri",
            ),
        )
        finish.set()
        self.assertEqual(
            await asyncio.gather(
                first,
                second,
            ),
            [(True, None), (True, None)],
        )
        self.assertEqual(
            len(
                calls,
            ),
            1,
        )
        self.assertEqual(
            list(
                self.scratch.iterdir(),
            ),
            [],
        )
        self.cache.size_limit = 0
        self.cache.collect()
        self.assertTrue(
            Path(
                self.cache.path(
                    "uri",
                ),
            ).exists(),
        )

    async def test_failed_refresh_preserves_old_image_and_removes_partial(
        self,
    ) -> None:
        path = Path(
            self.cache.path(
                "uri",
            ),
        )
        path.write_bytes(
            b"known good",
        )

        async def reject(
            arguments,
            **keywords,
        ):
            Path(
                arguments[2],
            ).write_bytes(
                b"partial",
            )
            raise commands.ImageBuildError(
                "manifest unknown",
            )

        self.namespace["run_command"] = reject
        self.assertEqual(
            await self.manager.ensure_image(
                "uri",
                force=True,
            ),
            (False, "manifest unknown"),
        )
        self.assertEqual(
            path.read_bytes(),
            b"known good",
        )
        self.assertFalse(
            Path(
                str(
                    path,
                )
                + ".tmp",
            ).exists(),
        )
        self.assertEqual(
            list(
                self.scratch.iterdir(),
            ),
            [],
        )

    async def test_repeated_cancellation_joins_child_before_cleaning_scratch(
        self,
    ) -> None:
        entered = asyncio.Event()
        child_pid = None
        process_directory = None
        marker = self.root / "child-started"

        async def command(
            arguments,
            **keywords,
        ):
            nonlocal process_directory
            process_directory = keywords["process_directory"]
            script = (
                "import os,pathlib,time; "
                "pathlib.Path(os.environ['APPTAINER_CACHEDIR'], 'scratch').write_text('owned'); "
                f"pathlib.Path({
                    str(
                        marker,
                    )!r
                }).write_text(str(os.getpid())); "
                "time.sleep(60)"
            )
            entered.set()
            return await commands.run_command(
                [sys.executable, "-c", script],
                environment=keywords["environment"],
                timeout=None,
                process_directory=keywords["process_directory"],
            )

        self.namespace["run_command"] = command
        task = asyncio.create_task(
            self.manager.ensure_image(
                "uri",
            ),
        )
        try:
            await asyncio.wait_for(
                entered.wait(),
                2,
            )
            async with asyncio.timeout(
                3,
            ):
                while not marker.exists():
                    await asyncio.sleep(
                        0.01,
                    )
            child_pid = int(
                marker.read_text(),
            )
            async with asyncio.timeout(
                3,
            ):
                while True:
                    lease = json.loads(
                        (process_directory / "process.json").read_text(),
                    )
                    if lease.get("state") == "running":
                        break
                    await asyncio.sleep(
                        0.01,
                    )
            self.assertEqual(
                lease["pid"],
                child_pid,
            )
            task.cancel()
            await asyncio.sleep(
                0,
            )
            task.cancel()
            with self.assertRaises(
                asyncio.CancelledError,
            ):
                await asyncio.wait_for(
                    task,
                    10,
                )
            self.assertEqual(
                list(
                    self.scratch.iterdir(),
                ),
                [],
            )
            self.assertEqual(
                list(
                    self.root.glob(
                        "pull-*",
                    ),
                ),
                [],
            )
            self.assertFalse(
                Path(
                    self.cache.path(
                        "uri",
                    )
                    + ".tmp",
                ).exists(),
            )
            if os.name == "posix":
                with self.assertRaises(
                    ProcessLookupError,
                ):
                    os.kill(
                        child_pid,
                        0,
                    )
        finally:
            task.cancel()
            await asyncio.gather(
                task,
                return_exceptions=True,
            )

    async def test_failed_spawn_preserves_scratch_without_claiming_process_exit(
        self,
    ) -> None:
        with (
            patch.object(
                commands.asyncio,
                "create_subprocess_exec",
                new=AsyncMock(
                    side_effect=OSError(
                        "transport setup failed",
                    ),
                ),
            ),
            self.assertLogs(
                pull_scratch.logger,
                level="WARNING",
            ),
            self.assertRaisesRegex(
                OSError,
                "transport setup failed",
            ),
        ):
            await self.manager.ensure_image(
                "uri",
            )
        cache, = self.scratch.iterdir()
        self.assertEqual(
            json.loads(
                (cache / "process.json").read_text(),
            ),
            {"state": "spawning"},
        )
        self.assertTrue(
            (self.root / cache.name).is_dir(),
        )

    async def test_cancelled_allocation_joins_worker_and_removes_owned_scratch(
        self,
    ) -> None:
        allocated = threading.Event()
        release = threading.Event()
        create = pull_scratch.PullScratch.create

        def delayed_create(
            **keywords,
        ):
            scratch = create(
                **keywords,
            )
            allocated.set()
            if not release.wait(
                3,
            ):
                raise RuntimeError("allocation worker not released")
            return scratch

        command = AsyncMock()
        self.namespace["run_command"] = command
        with patch.object(
            pull_scratch.PullScratch,
            "create",
            side_effect=delayed_create,
        ):
            task = asyncio.create_task(
                self.manager.ensure_image(
                    "uri",
                ),
            )
            try:
                async with asyncio.timeout(
                    3,
                ):
                    while not allocated.is_set():
                        await asyncio.sleep(
                            0.01,
                        )
                task.cancel()
                await asyncio.sleep(
                    0,
                )
                self.assertFalse(
                    task.done(),
                )
                release.set()
                with self.assertRaises(
                    asyncio.CancelledError,
                ):
                    await task
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(
                    task,
                    return_exceptions=True,
                )
        command.assert_not_called()
        self.assertEqual(
            list(
                self.scratch.iterdir(),
            ),
            [],
        )
        self.assertEqual(
            list(
                self.root.glob(
                    "pull-*",
                ),
            ),
            [],
        )

    async def test_docker_pulls_are_serialized_and_inspect_uses_mirror(
        self,
    ) -> None:
        self.namespace["is_local_mode"] = True
        self.namespace["_rewrite_image_for_mirror"] = lambda uri: "docker://mirror/image:tag"
        calls = []
        ready = False

        async def command(
            arguments,
            **keywords,
        ):
            nonlocal ready
            calls.append(
                arguments,
            )
            self.assertEqual(
                arguments[-1],
                "mirror/image:tag",
            )
            if arguments[1] == "image" and not ready:
                raise commands.ImageBuildError(
                    "not found",
                )
            if arguments[1] == "pull":
                await asyncio.sleep(
                    0,
                )
                ready = True
            return ""

        self.namespace["run_command"] = command
        self.assertEqual(
            await asyncio.gather(
                self.manager.ensure_image(
                    "uri",
                ),
                self.manager.ensure_image(
                    "uri",
                ),
            ),
            [(True, None), (True, None)],
        )
        self.assertEqual(
            [args[1] for args in calls],
            ["image", "pull", "image"],
        )


if __name__ == "__main__":
    unittest.main()
