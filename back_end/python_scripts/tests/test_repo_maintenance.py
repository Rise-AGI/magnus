"""Repository invalidation shares preparation ownership and never follows links."""

import ast
import asyncio
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from test_git_preparation import RESOURCE, load_repos


def load_maintenance(
    namespace: dict,
) -> dict:
    path = RESOURCE / "_repo_maintenance.py"
    tree = ast.parse(
        path.read_text(),
    )
    tree.body = [
        n
        for n in tree.body
        if not isinstance(
            n,
            ast.ImportFrom,
        )
    ]
    exec(
        compile(
            tree,
            str(
                path,
            ),
            "exec",
        ),
        namespace,
    )
    return namespace


class RepoMaintenanceTests(unittest.IsolatedAsyncioTestCase):
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
        self.cache = self.root / "cache"
        self.cache.mkdir()
        (self.cache / "pack").write_bytes(
            b"12345",
        )
        self.namespace = load_maintenance(
            load_repos(
                self.cache,
            ),
        )
        self.lock = asyncio.Lock()

    async def invalidate(
        self,
        dry_run: bool = False,
    ) -> dict:
        return await self.namespace["invalidate_cache"](
            str(
                self.cache,
            ),
            self.lock,
            dry_run,
        )

    async def test_manager_uses_the_existing_preparation_lock(
        self,
    ) -> None:
        manager = self.namespace["_ReposMixin"]()
        manager._get_repo_cache_path = lambda *args: str(
            self.cache,
        )
        manager.repo_locks = {
            str(
                self.cache,
            ): self.lock
        }
        await self.lock.acquire()
        try:
            result = await manager.invalidate_repo_cache(
                namespace="example",
                repo_name="repo",
                branch="branch/one",
                dry_run=False,
            )
            self.assertEqual(
                result["status"],
                "busy",
            )
            self.assertTrue(
                self.cache.exists(),
            )
        finally:
            self.lock.release()

    async def test_preview_remove_and_repeated_request(
        self,
    ) -> None:
        neighbor = self.root / "other-repo"
        neighbor.mkdir()
        self.assertEqual(
            await self.invalidate(
                True,
            ),
            {"status": "present", "cache_bytes": 5},
        )
        self.assertTrue(
            self.cache.exists(),
        )
        self.assertEqual(
            await self.invalidate(),
            {"status": "removed", "cache_bytes": 5},
        )
        self.assertEqual(
            await self.invalidate(),
            {"status": "absent", "cache_bytes": 0},
        )
        self.assertTrue(
            neighbor.exists(),
        )

    async def test_busy_and_woken_waiter_are_not_overtaken(
        self,
    ) -> None:
        await self.lock.acquire()
        self.assertEqual(
            (await self.invalidate())["status"],
            "busy",
        )
        waiter = asyncio.create_task(
            self.lock.acquire(),
        )
        await asyncio.sleep(
            0,
        )
        self.lock.release()
        self.assertEqual(
            (await self.invalidate())["status"],
            "busy",
        )
        await waiter
        self.lock.release()
        self.assertTrue(
            self.cache.exists(),
        )

    async def test_cancel_keeps_lock_until_deletion_thread_finishes(
        self,
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        original = self.namespace["_remove_tree"]

        def delayed(
            path,
        ):
            started.set()
            release.wait(
                5,
            )
            original(
                path,
            )

        with patch.dict(
            self.namespace,
            {"_remove_tree": delayed},
        ):
            task = asyncio.create_task(
                self.invalidate(),
            )
            try:
                self.assertTrue(
                    await asyncio.to_thread(
                        started.wait,
                        2,
                    ),
                )
                task.cancel()
                await asyncio.sleep(
                    0,
                )
                self.assertTrue(
                    self.lock.locked(),
                )
                self.assertEqual(
                    (await self.invalidate())["status"],
                    "busy",
                )
            finally:
                release.set()
            with self.assertRaises(
                asyncio.CancelledError,
            ):
                await task
        self.assertFalse(
            self.lock.locked(),
        )
        self.assertFalse(
            self.cache.exists(),
        )

    @unittest.skipIf(
        os.name == "nt",
        "Symlink privileges are not required on Windows",
    )
    async def test_symlink_root_rejected_and_nested_target_preserved(
        self,
    ) -> None:
        target = self.root / "scientific-result"
        target.write_bytes(
            b"preserve",
        )
        (self.cache / "external").symlink_to(
            target,
        )
        await self.invalidate()
        self.assertEqual(
            target.read_bytes(),
            b"preserve",
        )
        self.cache.symlink_to(
            self.root,
            target_is_directory=True,
        )
        with self.assertRaises(
            ValueError,
        ):
            await self.invalidate()
        self.assertTrue(
            target.exists(),
        )

    async def test_partial_removal_is_not_acknowledged(
        self,
    ) -> None:
        with patch.dict(
            self.namespace,
            {"_remove_tree": lambda path: None},
        ):
            with self.assertRaises(
                OSError,
            ):
                await self.invalidate()
        self.assertFalse(
            self.lock.locked(),
        )

    async def test_admin_gate_and_busy_status(
        self,
    ) -> None:
        path = RESOURCE.parent / "routers/github.py"
        tree = ast.parse(
            path.read_text(),
        )
        node = next(
            n
            for n in tree.body
            if isinstance(
                n,
                ast.AsyncFunctionDef,
            )
            and n.name == "invalidate_repository_cache"
        )
        node.decorator_list = []
        node.args.defaults = []
        namespace = {
            "HTTPException": HTTPException,
            "RepositoryCacheInvalidation": object,
            "models": SimpleNamespace(
                User=object,
            ),
            "is_admin_user": lambda user: user.admin,
            "resource_manager": SimpleNamespace(
                invalidate_repo_cache=AsyncMock(
                    return_value={"status": "busy"},
                ),
            ),
            "logger": logging.getLogger(
                "maintenance-test",
            ),
        }
        exec(
            compile(
                ast.Module(
                    body=[node],
                    type_ignores=[],
                ),
                str(
                    path,
                ),
                "exec",
            ),
            namespace,
        )
        call = namespace["invalidate_repository_cache"]
        request = SimpleNamespace(
            branch="branch/one",
            dry_run=True,
        )
        with self.assertRaises(
            HTTPException,
        ) as error:
            await call(
                "namespace",
                "repo",
                request,
                SimpleNamespace(
                    admin=False,
                ),
            )
        self.assertEqual(
            error.exception.status_code,
            403,
        )
        namespace["resource_manager"].invalidate_repo_cache.assert_not_awaited()
        with self.assertRaises(
            HTTPException,
        ) as error:
            await call(
                "namespace",
                "repo",
                request,
                SimpleNamespace(
                    admin=True,
                ),
            )
        self.assertEqual(
            error.exception.status_code,
            409,
        )
