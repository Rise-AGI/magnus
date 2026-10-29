"""SIF verification serializes one artifact without blocking unrelated hits."""

import asyncio
import hashlib
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_image_build_concurrency as fixtures


class SifVerificationTests(unittest.IsolatedAsyncioTestCase):
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
        self.manager = fixture.manager
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = []
        self.calls_lock = threading.Lock()
        self.workers = []
        self.slow = self.image(
            b"image awaiting verification",
        )
        self.warm = self.image(
            b"another verified image",
        )
        self.assertTrue(
            self.manager._verify_sif(
                *self.warm,
            ),
        )
        actual_digest = hashlib.file_digest

        def digest(
            stream,
            *arguments,
            **keywords,
        ):
            path = Path(
                stream.name,
            )
            with self.calls_lock:
                self.calls.append(
                    path,
                )
            if path == self.slow[0]:
                self.entered.set()
                if not self.release.wait(
                    5,
                ):
                    raise AssertionError(
                        "Verification worker not released",
                    )
            return actual_digest(
                stream,
                *arguments,
                **keywords,
            )

        self.enterContext(
            patch.object(
                hashlib,
                "file_digest",
                side_effect=digest,
            ),
        )

    async def asyncTearDown(
        self,
    ) -> None:
        self.release.set()
        results = await asyncio.gather(
            *self.workers,
            return_exceptions=True,
        )
        self.assertEqual(
            [result for result in results if isinstance(result, BaseException)],
            [],
        )
        self.assertEqual(
            self.manager._verification_locks,
            {},
        )

    def image(
        self,
        contents: bytes,
    ) -> tuple[Path, str, int]:
        digest = "sha256:" + hashlib.sha256(
            contents,
        ).hexdigest()
        path = self.manager._sif_path(
            digest,
        )
        path.write_bytes(
            contents,
        )
        return path, digest, len(contents)

    def worker(
        self,
        function,
        *arguments,
    ) -> asyncio.Future:
        future = asyncio.get_running_loop().run_in_executor(
            None,
            function,
            *arguments,
        )
        self.workers.append(
            future,
        )
        return future

    async def start_slow_verification(
        self,
    ) -> asyncio.Future:
        slow = self.worker(
            self.manager._verify_sif,
            *self.slow,
        )
        self.assertTrue(
            await asyncio.to_thread(
                self.entered.wait,
                2,
            ),
        )
        return slow

    async def test_verified_hit_does_not_wait_for_another_image_hash(
        self,
    ) -> None:
        slow = await self.start_slow_verification()
        warm = self.worker(
            self.manager._verify_sif,
            *self.warm,
        )
        self.assertTrue(
            await asyncio.wait_for(
                asyncio.shield(
                    warm,
                ),
                2,
            ),
        )
        self.assertFalse(
            slow.done(),
        )
        self.assertEqual(
            self.calls,
            [self.slow[0]],
        )

    async def test_same_image_waiters_share_one_hash(
        self,
    ) -> None:
        first = await self.start_slow_verification()
        second = self.worker(
            self.manager._verify_sif,
            *self.slow,
        )

        async def wait_for_registration() -> None:
            while True:
                with self.manager.verification_lock:
                    owner = self.manager._verification_locks.get(
                        self.slow[1],
                    )
                    if owner is not None and owner.users == 2:
                        return
                await asyncio.sleep(
                    0,
                )

        await asyncio.wait_for(
            wait_for_registration(),
            2,
        )
        self.assertFalse(
            second.done(),
        )
        self.release.set()
        self.assertEqual(
            await asyncio.gather(
                first,
                second,
            ),
            [True, True],
        )
        self.assertEqual(
            self.calls,
            [self.slow[0]],
        )

    async def test_cold_hashes_keep_one_reader_while_memo_hits_can_proceed(
        self,
    ) -> None:
        lock = self.manager._cold_hash_lock
        entered = threading.Event()
        calls_lock = threading.Lock()
        attempts = 0

        class ObservedWorkLock:
            def __enter__(
                self,
            ) -> None:
                nonlocal attempts
                with calls_lock:
                    attempts += 1
                    if attempts == 2:
                        entered.set()
                lock.acquire()

            def __exit__(
                self,
                *exception,
            ) -> None:
                lock.release()

        self.manager._cold_hash_lock = ObservedWorkLock()
        first = await self.start_slow_verification()
        other = self.image(
            b"a second cold image",
        )
        second = self.worker(
            self.manager._verify_sif,
            *other,
        )
        self.assertTrue(
            await asyncio.to_thread(
                entered.wait,
                2,
            ),
        )
        warm = self.worker(
            self.manager._verify_sif,
            *self.warm,
        )
        self.assertTrue(
            await asyncio.wait_for(
                asyncio.shield(
                    warm,
                ),
                2,
            ),
        )
        self.assertFalse(
            second.done(),
        )
        self.assertEqual(
            self.calls,
            [self.slow[0]],
        )
        self.release.set()
        self.assertEqual(
            await asyncio.gather(
                first,
                second,
            ),
            [True, True],
        )
        self.assertEqual(
            self.calls,
            [self.slow[0], other[0]],
        )

    async def test_removal_waits_only_for_its_own_image_verification(
        self,
    ) -> None:
        slow = await self.start_slow_verification()
        remove_slow = self.worker(
            self.manager._remove_sif,
            self.slow[1],
        )
        remove_warm = self.worker(
            self.manager._remove_sif,
            self.warm[1],
        )
        await asyncio.wait_for(
            asyncio.shield(
                remove_warm,
            ),
            2,
        )
        self.assertFalse(
            self.warm[0].exists(),
        )
        self.assertTrue(
            self.slow[0].exists(),
        )
        self.assertFalse(
            remove_slow.done(),
        )
        self.release.set()
        self.assertTrue(
            await slow,
        )
        await remove_slow
        self.assertFalse(
            self.slow[0].exists(),
        )
        self.assertNotIn(
            self.slow[1],
            self.manager.verified_sifs,
        )

    async def test_failed_hash_releases_ownership_for_a_later_verification(
        self,
    ) -> None:
        for error in (OSError, RuntimeError):
            with self.subTest(
                error=error.__name__,
            ):
                image = self.image(
                    error.__name__.encode(),
                )
                with patch.object(
                    hashlib,
                    "file_digest",
                    side_effect=error(
                        "failed checksum read",
                    ),
                ):
                    if error is OSError:
                        self.assertFalse(
                            self.manager._verify_sif(
                                *image,
                            ),
                        )
                    else:
                        with self.assertRaises(
                            RuntimeError,
                        ):
                            self.manager._verify_sif(
                                *image,
                            )
                self.assertEqual(
                    self.manager._verification_locks,
                    {},
                )
                self.assertTrue(
                    self.manager._verify_sif(
                        *image,
                    ),
                )
