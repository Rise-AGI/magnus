"""Recovery must not reclaim files while a surviving pull still owns them."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_image_cache as image_fixtures
from test_image_cache import commands, pull_scratch


class PullRecoveryTests(unittest.TestCase):
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
        self.cache = self.root / "oci"
        self.outputs = self.root / "sif"
        self.cache.mkdir()
        self.outputs.mkdir()
        self.scratch = pull_scratch.PullScratch.create(
            str(
                self.cache,
            ),
            str(
                self.outputs,
            ),
        )
        self.scratch.output.write_bytes(
            b"partial SIF",
        )

    def recover(
        self,
    ) -> None:
        pull_scratch.recover_pull_scratch(
            str(
                self.cache,
            ),
            str(
                self.outputs,
            ),
        )

    def lease(
        self,
        document: dict,
    ) -> None:
        pull_scratch.write_json(
            self.scratch.cache / "process.json",
            document,
        )

    def assert_preserved(
        self,
    ) -> None:
        self.assertTrue(
            self.scratch.cache.is_dir(),
        )
        self.assertEqual(
            self.scratch.output.read_bytes(),
            b"partial SIF",
        )

    def test_live_backend_keeps_its_scratch_even_between_commands(
        self,
    ) -> None:
        self.recover()
        self.assert_preserved()

    def test_abandoned_idle_scratch_is_removed_without_touching_final_image(
        self,
    ) -> None:
        final = self.outputs / "complete.sif"
        final.write_bytes(
            b"published image",
        )
        with patch.object(
            pull_scratch,
            "process_exists",
            return_value=False,
        ):
            self.recover()
        self.assertFalse(
            self.scratch.cache.exists(),
        )
        self.assertFalse(
            self.scratch.output_directory.exists(),
        )
        self.assertEqual(
            final.read_bytes(),
            b"published image",
        )

    def test_surviving_writer_is_preserved_until_it_exits(
        self,
    ) -> None:
        ready = self.root / "ready"
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import pathlib,sys,time; "
                "stream=pathlib.Path(sys.argv[1]).open('ab'); "
                "pathlib.Path(sys.argv[2]).touch(); "
                "time.sleep(60)",
                str(
                    self.scratch.output,
                ),
                str(
                    ready,
                ),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(
                    0.01,
                )
            self.assertTrue(
                ready.exists(),
            )
            self.lease(
                {"state": "running", "pid": process.pid},
            )
            real_probe = pull_scratch.process_exists

            def backend_exited(
                pid: int,
            ) -> bool:
                return False if pid == os.getpid() else real_probe(
                    pid,
                )

            with patch.object(
                pull_scratch,
                "process_exists",
                side_effect=backend_exited,
            ):
                self.recover()
                self.assert_preserved()
                self.assertIsNone(
                    process.poll(),
                )
                process.terminate()
                process.wait(
                    timeout=3,
                )
                self.recover()
                self.assertFalse(
                    self.scratch.cache.exists(),
                )
                self.assertFalse(
                    self.scratch.output_directory.exists(),
                )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(
                    timeout=3,
                )

    @unittest.skipUnless(
        os.name == "posix",
        "Process groups are POSIX-specific",
    )
    def test_surviving_process_group_keeps_output_after_leader_exit(
        self,
    ) -> None:
        self.lease(
            {"state": "running", "pid": 42},
        )
        with (
            patch.object(
                pull_scratch,
                "process_exists",
                return_value=False,
            ),
            patch.object(
                pull_scratch,
                "process_group_exists",
                return_value=True,
            ) as group_probe,
        ):
            self.recover()
            self.assert_preserved()
            group_probe.assert_called_once_with(
                42,
            )
            group_probe.return_value = False
            self.recover()
        self.assertFalse(
            self.scratch.cache.exists(),
        )

    def test_incomplete_legacy_or_malformed_lease_is_preserved(
        self,
    ) -> None:
        path = self.scratch.cache / "process.json"
        for document in (None, [], {"state": "spawning"}, {"state": "running", "pid": True},
                         {"state": "running", "pid": 2**100}, {"state": "invalid"}):
            with self.subTest(
                document=document,
            ):
                path.write_text(
                    json.dumps(
                        document,
                    ),
                )
                with (
                    patch.object(
                        pull_scratch,
                        "process_exists",
                        return_value=False,
                    ),
                    self.assertLogs(
                        pull_scratch.logger,
                        level="WARNING",
                    ),
                ):
                    self.recover()
                self.assert_preserved()
        path.unlink()
        with self.assertLogs(
            pull_scratch.logger,
            level="WARNING",
        ):
            self.recover()
        self.assert_preserved()

    def test_cleanup_failure_retains_lease_for_later_recovery(
        self,
    ) -> None:
        with (
            patch.object(
                pull_scratch,
                "process_exists",
                return_value=False,
            ),
            patch.object(
                pull_scratch.shutil,
                "rmtree",
                side_effect=PermissionError,
            ),
            self.assertLogs(
                pull_scratch.logger,
                level="WARNING",
            ),
        ):
            self.recover()
        self.assert_preserved()
        self.assertEqual(
            json.loads(
                (self.scratch.cache / "process.json").read_text(),
            ),
            {"state": "idle"},
        )

    @unittest.skipUnless(
        os.name == "posix",
        "Symlink creation is available without elevated privileges on POSIX",
    )
    def test_recovery_does_not_follow_linked_output_or_cache_directories(
        self,
    ) -> None:
        outside = self.root / "unrelated"
        outside.mkdir()
        valuable = outside / "result"
        valuable.write_bytes(
            b"keep",
        )
        shutil.rmtree(
            self.scratch.output_directory,
        )
        self.scratch.output_directory.symlink_to(
            outside,
            target_is_directory=True,
        )
        (self.cache / "pull-link").symlink_to(
            outside,
            target_is_directory=True,
        )
        with (
            patch.object(
                pull_scratch,
                "process_exists",
                return_value=False,
            ),
            self.assertLogs(
                pull_scratch.logger,
                level="WARNING",
            ),
        ):
            self.recover()
        self.assertTrue(
            self.scratch.cache.exists(),
        )
        self.assertEqual(
            valuable.read_bytes(),
            b"keep",
        )

    def test_failed_allocation_does_not_remove_preexisting_output_directory(
        self,
    ) -> None:
        cache = self.cache / "pull-collision"
        cache.mkdir()
        output = self.outputs / cache.name
        output.mkdir()
        valuable = output / "result"
        valuable.write_bytes(
            b"keep",
        )
        with (
            patch.object(
                pull_scratch.tempfile,
                "mkdtemp",
                return_value=str(
                    cache,
                ),
            ),
            self.assertRaises(
                FileExistsError,
            ),
        ):
            pull_scratch.PullScratch.create(
                str(
                    self.cache,
                ),
                str(
                    self.outputs,
                ),
            )
        self.assertEqual(
            valuable.read_bytes(),
            b"keep",
        )
        self.assertFalse(
            cache.exists(),
        )


@unittest.skipUnless(
    os.name == "posix",
    "Surviving process groups are POSIX-specific",
)
class PullWriterLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        self.fixture = image_fixtures.ImagePullTests()
        self.fixture.setUp()
        self.addCleanup(
            self.fixture.doCleanups,
        )

    async def check_leader_exit(
        self,
        exit_code: int,
    ) -> None:
        fixture = self.fixture
        ready = fixture.root / "writer-ready"
        finish = fixture.root / "writer-finish"
        leader_file = fixture.root / "leader-pid"
        final = Path(
            fixture.cache.path(
                "uri",
            ),
        )
        final.write_bytes(
            b"previous image",
        )
        helper = """
import pathlib, sys, time
with pathlib.Path(sys.argv[1]).open('wb') as output:
    output.write(b'partial')
    output.flush()
    pathlib.Path(sys.argv[2]).touch()
    deadline = time.monotonic() + 5
    while not pathlib.Path(sys.argv[3]).exists() and time.monotonic() < deadline:
        time.sleep(.01)
    output.write(b'-complete')
"""
        leader = """
import os, pathlib, subprocess, sys, time
subprocess.Popen(
    [sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
deadline = time.monotonic() + 3
while not pathlib.Path(sys.argv[3]).exists() and time.monotonic() < deadline:
    time.sleep(.01)
pathlib.Path(sys.argv[5]).write_text(str(os.getpid()))
print('manifest unknown', file=sys.stderr)
sys.exit(int(sys.argv[6]))
"""

        async def command(
            arguments,
            **keywords,
        ):
            return await commands.run_command(
                [
                    sys.executable, "-c", leader, helper, arguments[2],
                    str(ready), str(finish), str(leader_file), str(exit_code),
                ],
                environment=keywords["environment"],
                process_directory=keywords["process_directory"],
                timeout=5,
            )

        fixture.namespace["run_command"] = command
        task = asyncio.create_task(
            fixture.manager.ensure_image(
                "uri",
                force=True,
            ),
        )
        leader_pid = None
        try:
            async with asyncio.timeout(
                3,
            ):
                while not leader_file.exists():
                    await asyncio.sleep(
                        0.01,
                    )
                leader_pid = int(
                    leader_file.read_text(),
                )
                while pull_scratch.process_exists(
                    leader_pid,
                ):
                    await asyncio.sleep(
                        0.01,
                    )
            self.assertTrue(
                pull_scratch.process_group_exists(
                    leader_pid,
                ),
            )
            self.assertFalse(
                task.done(),
            )
            self.assertEqual(
                final.read_bytes(),
                b"previous image",
            )
            finish.touch()
            success, _ = await asyncio.wait_for(
                task,
                5,
            )
            self.assertEqual(
                success,
                exit_code == 0,
            )
            self.assertEqual(
                final.read_bytes(),
                b"partial-complete" if success else b"previous image",
            )
            self.assertFalse(
                pull_scratch.process_group_exists(
                    leader_pid,
                ),
            )
        finally:
            finish.touch()
            if not task.done():
                task.cancel()
            await asyncio.gather(
                task,
                return_exceptions=True,
            )
            if leader_pid is not None:
                async with asyncio.timeout(
                    3,
                ):
                    while pull_scratch.process_group_exists(
                        leader_pid,
                    ):
                        await asyncio.sleep(
                            0.01,
                        )

    async def test_success_waits_for_writer_before_publishing(
        self,
    ) -> None:
        await self.check_leader_exit(
            0,
        )

    async def test_failure_waits_for_writer_before_removing_output(
        self,
    ) -> None:
        await self.check_leader_exit(
            7,
        )

    async def test_unconfirmed_termination_preserves_lease_and_avoids_retry(
        self,
    ) -> None:
        calls = []

        async def command(
            arguments,
            **keywords,
        ):
            calls.append(
                arguments,
            )
            return await commands.run_command(
                [
                    sys.executable, "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'partial')",
                    arguments[2],
                ],
                process_directory=keywords["process_directory"],
                timeout=5,
            )

        self.fixture.namespace["run_command"] = command
        with (
            patch.object(
                commands,
                "_wait_for_group_exit",
                new=AsyncMock(
                    side_effect=TimeoutError,
                ),
            ),
            self.assertLogs(
                pull_scratch.logger,
                level="WARNING",
            ),
        ):
            success, error = await self.fixture.manager.ensure_image(
                "uri",
            )
        self.assertFalse(
            success,
        )
        self.assertIn(
            "did not exit after termination",
            error,
        )
        self.assertEqual(
            len(
                calls,
            ),
            1,
        )
        cache, = self.fixture.scratch.iterdir()
        self.assertEqual(
            json.loads(
                (cache / "process.json").read_text(),
            )["state"],
            "running",
        )
        self.assertEqual(
            Path(
                calls[0][2],
            ).read_bytes(),
            b"partial",
        )
