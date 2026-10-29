"""A Docker daemon outage must not invalidate a reusable build result."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock

import test_image_build_concurrency as fixtures


class DockerBuildCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        fixture = fixtures.ImageBuildConcurrencyTests()
        fixture.setUp()
        self.addCleanup(
            fixture.doCleanups,
        )
        self.addCleanup(
            fixture.tearDown,
        )
        self.module = fixture.module
        self.module.is_local_mode = True
        self.manager = fixture.manager
        self.operation = fixture.operation
        self.operation.progress.consume = Mock()
        self.result = {
            "digest": fixture.docker_digest,
            "docker_image": fixture.docker_digest,
            "docker_tag": self.manager._docker_tag(
                fixture.docker_digest,
            ),
            "size_bytes": 64,
            "accessed_at": 1,
        }
        self.manager.results[self.operation.key] = dict(
            self.result,
        )
        self.manager._ensure_cached_budget = AsyncMock()
        self.module.run_command = AsyncMock()

    async def save_index(
        self,
    ) -> bytes:
        await self.manager._save_index()
        return (self.manager.root / "index.json").read_bytes()

    async def test_daemon_errors_preserve_the_cache_index(
        self,
    ) -> None:
        before = await self.save_index()
        for error in (
            self.module.ImageBuildError(
                "Docker daemon unavailable",
            ),
            OSError(
                "Docker CLI could not start",
            ),
        ):
            with self.subTest(
                error=type(
                    error,
                ).__name__,
            ):
                self.manager.results[self.operation.key] = dict(
                    self.result,
                )
                await self.save_index()
                self.module.run_command.side_effect = error
                with self.assertRaises(
                    type(
                        error,
                    ),
                ):
                    await self.manager._cached_result(
                        self.operation,
                    )
                self.assertEqual(
                    self.manager.results[self.operation.key],
                    self.result,
                )
                self.assertEqual(
                    (self.manager.root / "index.json").read_bytes(),
                    before,
                )
        self.manager._ensure_cached_budget.assert_not_awaited()

    async def test_failed_lookup_does_not_begin_a_build(
        self,
    ) -> None:
        before = await self.save_index()
        self.module.run_command.side_effect = self.module.ImageBuildError(
            "Docker daemon unavailable",
        )
        self.manager._collect_cache = AsyncMock()
        self.manager._ensure_budget = AsyncMock()
        self.manager._flush = AsyncMock()
        self.manager.client.build = AsyncMock(
            side_effect=AssertionError(
                "A failed cache query is not permission to rebuild",
            ),
        )
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "Docker daemon unavailable",
        ):
            await self.manager._run(
                self.operation,
            )
        self.manager.client.build.assert_not_awaited()
        self.manager._collect_cache.assert_not_awaited()
        self.assertEqual(
            (self.manager.root / "index.json").read_bytes(),
            before,
        )

    async def test_missing_or_replaced_tag_invalidates_only_its_result(
        self,
    ) -> None:
        for output in ("", "sha256:" + "d" * 64 + "\n"):
            with self.subTest(
                output=output,
            ):
                self.manager.results[self.operation.key] = dict(
                    self.result,
                )
                self.manager.pins["another-job"] = self.result["digest"]
                self.module.run_command.return_value = output
                self.assertIsNone(
                    await self.manager._cached_result(
                        self.operation,
                    ),
                )
                self.assertNotIn(
                    self.operation.key,
                    self.manager.results,
                )
                self.assertIsNone(
                    self.operation.result,
                )
                self.assertEqual(
                    self.manager.pins,
                    {"another-job": self.result["digest"]},
                )
                self.assertEqual(
                    json.loads(
                        (self.manager.root / "index.json").read_text(),
                    )["results"],
                    {},
                )

    async def test_valid_cache_hit_uses_one_bounded_query(
        self,
    ) -> None:
        self.module.run_command.return_value = self.result["digest"] + "\n"
        self.assertEqual(
            await self.manager._cached_result(
                self.operation,
            ),
            self.result,
        )
        command = self.module.run_command.call_args
        self.assertEqual(
            command.args[0],
            [
                "docker", "image", "ls", "--all", "--no-trunc", "--quiet",
                "--filter", f"reference={self.result['docker_tag']}",
            ],
        )
        self.assertGreater(
            command.kwargs["timeout"],
            0,
        )
        self.assertLessEqual(
            command.kwargs["timeout"],
            60,
        )
        self.assertEqual(
            self.manager.pins,
            {"job": self.result["digest"]},
        )
        self.manager._ensure_cached_budget.assert_awaited_once()

    async def test_unrecognized_output_keeps_the_cache_index(
        self,
    ) -> None:
        before = await self.save_index()
        for output in (
            "truncated-digest\n",
            "sha256:" + "e" * 64 + "\nsha256:" + "f" * 64 + "\n",
        ):
            with self.subTest(
                output=output,
            ):
                self.manager.results[self.operation.key] = dict(
                    self.result,
                )
                await self.save_index()
                self.module.run_command.return_value = output
                with self.assertRaises(
                    self.module.ImageBuildError,
                ):
                    await self.manager._cached_result(
                        self.operation,
                    )
                self.assertEqual(
                    (self.manager.root / "index.json").read_bytes(),
                    before,
                )
                self.assertEqual(
                    self.manager.results[self.operation.key],
                    self.result,
                )

    async def test_timeout_keeps_the_cache_and_reports_the_lookup_deadline(
        self,
    ) -> None:
        before = await self.save_index()
        self.module.run_command.side_effect = TimeoutError()
        self.manager._flush = AsyncMock()
        with self.assertRaisesRegex(
            self.module.ImageBuildError,
            "Docker image cache lookup",
        ):
            await self.manager._run(
                self.operation,
            )
        self.assertEqual(
            (self.manager.root / "index.json").read_bytes(),
            before,
        )

    async def test_cancelled_lookup_preserves_the_cache_index(
        self,
    ) -> None:
        before = await self.save_index()
        self.module.run_command.side_effect = asyncio.CancelledError()
        with self.assertRaises(
            asyncio.CancelledError,
        ):
            await self.manager._cached_result(
                self.operation,
            )
        self.assertEqual(
            (self.manager.root / "index.json").read_bytes(),
            before,
        )


if __name__ == "__main__":
    unittest.main()
