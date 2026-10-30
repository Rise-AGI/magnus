"""Cache API ownership and worker teardown using temporary SQLite and files."""

import ast
import asyncio
import logging
import os
import re
import runpy
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Tuple
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy import DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from test_image_cache import SERVER, blocking_io, cache_module
from test_job_finalization import load_method


observations = runpy.run_path(str(SERVER / "_image_observation.py"))


class Base(DeclarativeBase):
    pass


class Image(Base):
    __tablename__ = "cached_images"
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    uri: Mapped[str] = mapped_column(
        String,
        unique=True,
    )
    filename: Mapped[str] = mapped_column(
        String,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String,
    )
    size_bytes: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    user_id = "owner"


class ImageRouteTests(unittest.IsolatedAsyncioTestCase):
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
            size_limit=100,
        )
        self.engine = create_engine(
            f"sqlite:///{self.root / 'images.db'}",
        )
        self.addCleanup(
            self.engine.dispose,
        )
        Base.metadata.create_all(
            self.engine,
        )
        self.sessions = sessionmaker(
            self.engine,
        )
        self.uri = "docker://example/image:tag"
        self.filename = cache_module.legacy_image_filename(
            self.uri,
        )
        self.cache.restore_legacy_names(
            [(self.uri, self.filename)],
        )
        with self.sessions() as db:
            db.add(
                Image(
                    id=1,
                    uri=self.uri,
                    filename=self.filename,
                    status="pulling",
                ),
            )
            db.commit()
        lifecycle = runpy.run_path(
            str(
                SERVER / "_lifecycle.py",
            ),
        )
        self.prepare = AsyncMock(
            return_value=(True, None),
        )
        self.namespace = {
            **lifecycle,
            **observations,
            "_run_blocking_io": blocking_io,
            "asyncio": asyncio,
            "datetime": datetime,
            "timezone": timezone,
            "logger": logging.getLogger(
                __name__,
            ),
            "os": os,
            "re": re,
            "subprocess": subprocess,
            "HTTPException": HTTPException,
            "database": SimpleNamespace(
                SessionLocal=self.sessions,
                get_db=lambda: None,
            ),
            "models": SimpleNamespace(
                CachedImage=Image,
                User=object,
            ),
            "Session": object,
            "Depends": lambda function: None,
            "get_current_user": lambda: None,
            "assert_can_manage_resource": lambda *args, **kwargs: None,
            "is_local_mode": False,
            "container_cache_path": str(
                self.root,
            ),
            "_uri_locks": {},
            "_pull_tasks": set(),
            "_recovered": False,
            "_recover_lock": threading.Lock(),
            "resource_manager": SimpleNamespace(
                ensure_image=self.prepare,
                get_sif_path=self.cache.path,
                get_docker_image=Mock(
                    return_value="mirror.invalid/example/image:tag",
                ),
                recover_stale_pull_caches=Mock(),
                images=self.cache,
            ),
        }
        source = SERVER / "routers/images.py"
        functions = {
            "_get_uri_lock",
            "_do_pull",
            "delete_image",
            "_schedule_pull",
            "shutdown_image_pulls",
            "_observe_image",
            "_pull_is_pending",
            "_settle_pull",
            "recover_stuck_images",
        }
        tree = ast.parse(
            source.read_text(),
        )
        tree.body = [
            node
            for node in tree.body
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.name in functions
        ]
        for node in tree.body:
            node.decorator_list = []
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

    async def test_restart_preserves_unowned_legacy_partial_files(
        self,
    ) -> None:
        partial = self.root / "legacy.sif.tmp"
        partial.write_bytes(
            b"a surviving pull may still be writing",
        )
        self.namespace["recover_stuck_images"]()
        self.assertTrue(
            partial.exists(),
        )
        self.namespace["resource_manager"].recover_stale_pull_caches.assert_called_once()

    async def test_restart_recovers_the_mirror_image_and_its_size(
        self,
    ) -> None:
        self.namespace["is_local_mode"] = True

        def inspect(
            command,
            **kwargs,
        ):
            self.assertEqual(
                command[-1],
                "mirror.invalid/example/image:tag",
            )
            return subprocess.CompletedProcess(
                command,
                0,
                "4321" if "--format" in command else "{}",
            )

        with patch.object(
            subprocess,
            "run",
            side_effect=inspect,
        ):
            self.namespace["recover_stuck_images"]()
        with self.sessions() as db:
            row = db.get(
                Image,
                1,
            )
            self.assertEqual(
                (row.status, row.size_bytes, row.uri),
                ("cached", 4321, self.uri),
            )

    async def test_manual_pull_and_failed_refresh_inspect_the_mirror(
        self,
    ) -> None:
        self.namespace["is_local_mode"] = True

        def inspect(
            command,
            **kwargs,
        ):
            self.assertEqual(
                command[-1],
                "mirror.invalid/example/image:tag",
            )
            self.assertEqual(kwargs["timeout"], 10)
            return subprocess.CompletedProcess(command, 0, "4321", "")

        with patch.object(
            subprocess,
            "run",
            side_effect=inspect,
        ):
            await self.namespace["_do_pull"](
                1,
                self.uri,
                is_refresh=False,
            )
            with self.sessions() as db:
                row = db.get(
                    Image,
                    1,
                )
                self.assertEqual(
                    (row.status, row.size_bytes),
                    ("cached", 4321),
                )
                row.status = "refreshing"
                db.commit()
            self.prepare.return_value = (False, "upstream unavailable")
            await self.namespace["_do_pull"](
                1,
                self.uri,
                is_refresh=True,
            )
        with self.sessions() as db:
            row = db.get(
                Image,
                1,
            )
            self.assertEqual(
                (row.status, row.size_bytes),
                ("cached", 4321),
            )

    async def test_unknown_refresh_preserves_registration_and_size(self):
        self.namespace["is_local_mode"] = True
        with self.sessions() as db:
            row = db.get(Image, 1)
            row.status = "refreshing"
            row.size_bytes = 4321
            db.commit()
        self.prepare.return_value = (False, "upstream unavailable")
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 10)):
            await self.namespace["_do_pull"](1, self.uri, is_refresh=True)
        with self.sessions() as db:
            row = db.get(Image, 1)
            self.assertEqual((row.status, row.size_bytes, row.uri), ("failed", 4321, self.uri))

    async def test_unavailable_runtime_does_not_abort_other_startup_recovery(self):
        self.namespace["is_local_mode"] = True
        with self.sessions() as db:
            db.add(Image(id=2, uri="docker://other:tag", filename="other.sif", status="pulling"))
            db.commit()
        with patch.object(subprocess, "run", side_effect=[
            FileNotFoundError("docker unavailable"),
            subprocess.CompletedProcess([], 0, "99", ""),
        ]):
            self.namespace["recover_stuck_images"]()
        with self.sessions() as db:
            self.assertEqual(db.get(Image, 1).status, "failed")
            self.assertEqual((db.get(Image, 2).status, db.get(Image, 2).size_bytes), ("cached", 99))

    async def test_cancelled_settlement_joins_probe_and_holds_uri_lock(self):
        self.namespace["is_local_mode"] = True
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()

        def inspect(command, **kwargs):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            self.assertEqual(kwargs["timeout"], 10)
            entered.set()
            if not release.wait(2):
                raise AssertionError("Event loop failed to release the worker")
            return subprocess.CompletedProcess(command, 0, "4321", "")

        with patch.object(subprocess, "run", side_effect=inspect):
            task = asyncio.create_task(self.namespace["_do_pull"](1, self.uri, is_refresh=False))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertTrue(self.namespace["_get_uri_lock"](self.uri).locked())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertFalse(self.namespace["_get_uri_lock"](self.uri).locked())
        with self.sessions() as db:
            self.assertEqual((db.get(Image, 1).status, db.get(Image, 1).size_bytes), ("cached", 4321))

    async def test_initial_record_check_runs_off_loop(self):
        original = self.namespace["_pull_is_pending"]
        loop_thread = threading.get_ident()
        checked = []

        def read(*args):
            checked.append(threading.get_ident())
            return original(*args)

        self.namespace["_pull_is_pending"] = read
        self.prepare.return_value = (False, "unavailable")
        await self.namespace["_do_pull"](1, self.uri, is_refresh=False)
        self.assertEqual(len(checked), 1)
        self.assertNotEqual(checked[0], loop_thread)

    def shared_pull_namespace(
        self,
    ) -> dict:
        namespace = {
            **self.namespace,
            "CachedImage": Image,
            "SessionLocal": self.sessions,
            "is_local_mode": True,
            "_run_blocking_io": blocking_io,
            "Optional": Optional,
            "Tuple": Tuple,
        }
        for name in ("_register_image_pulling", "_finalize_image_status", "_pull_image_shared"):
            load_method(
                "_scheduler/_resources.py",
                name,
                namespace,
            )
        return namespace

    async def test_registration_failure_removes_shared_pull_task(
        self,
    ) -> None:
        namespace = self.shared_pull_namespace()
        namespace["SessionLocal"] = Mock(
            side_effect=OSError(
                "registration store unavailable",
            ),
        )
        actor = SimpleNamespace(
            _image_pull_tasks={},
        )
        task = asyncio.create_task(
            namespace["_pull_image_shared"](
                actor,
                self.uri,
                "owner",
            ),
        )
        actor._image_pull_tasks[self.uri] = task
        with self.assertRaisesRegex(
            OSError,
            "registration store unavailable",
        ):
            await task
        self.assertEqual(
            actor._image_pull_tasks,
            {},
        )
        self.prepare.assert_not_awaited()

    async def test_registration_stays_off_loop_and_cancellation_joins_its_commit(
        self,
    ) -> None:
        namespace = self.shared_pull_namespace()
        with self.sessions() as db:
            db.get(
                Image,
                1,
            ).status = "failed"
            db.commit()
        register = namespace["_register_image_pulling"]
        entered, finish = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        registration_threads = []

        def delayed_register(
            *arguments,
        ):
            registration_threads.append(
                threading.get_ident(),
            )
            result = register(
                *arguments,
            )
            entered.set()
            if threading.get_ident() != loop_thread and not finish.wait(
                3,
            ):
                raise AssertionError("Registration worker not released")
            return result

        namespace["_register_image_pulling"] = delayed_register
        actor = SimpleNamespace(
            _image_pull_tasks={},
        )
        task = asyncio.create_task(
            namespace["_pull_image_shared"](
                actor,
                self.uri,
                "owner",
            ),
        )
        actor._image_pull_tasks[self.uri] = task
        try:
            self.assertTrue(
                await asyncio.to_thread(
                    entered.wait,
                    3,
                ),
            )
            self.assertNotIn(
                loop_thread,
                registration_threads,
            )
            task.cancel()
            await asyncio.sleep(
                0,
            )
            self.assertFalse(
                task.done(),
            )
            self.assertIs(
                actor._image_pull_tasks[self.uri],
                task,
            )
        finally:
            finish.set()
            await asyncio.gather(
                task,
                return_exceptions=True,
            )
        self.assertTrue(
            task.cancelled(),
        )
        self.assertEqual(
            actor._image_pull_tasks,
            {},
        )
        self.prepare.assert_not_awaited()
        with self.sessions() as db:
            self.assertEqual(
                db.get(
                    Image,
                    1,
                ).status,
                "pulling",
            )

    async def test_shared_pull_keeps_ownership_until_threaded_size_query_finishes(
        self,
    ) -> None:
        namespace = self.shared_pull_namespace()
        with self.sessions() as db:
            db.get(
                Image,
                1,
            ).status = "failed"
            db.commit()
        entered, finish = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        inspection_threads = []

        def inspect(
            command,
            **kwargs,
        ):
            inspection_threads.append(
                threading.get_ident(),
            )
            self.assertEqual(
                command[-1],
                "mirror.invalid/example/image:tag",
            )
            entered.set()
            if not finish.wait(
                2,
            ):
                raise AssertionError(
                    "The event loop must remain free to release the query",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                "4321",
            )

        actor = SimpleNamespace(
            _image_pull_tasks={},
        )
        with patch.object(
            subprocess,
            "run",
            side_effect=inspect,
        ):
            task = asyncio.create_task(
                namespace["_pull_image_shared"](
                    actor,
                    self.uri,
                    "owner",
                ),
            )
            actor._image_pull_tasks[self.uri] = task
            try:
                self.assertTrue(
                    await asyncio.to_thread(
                        entered.wait,
                        2,
                    ),
                )
                self.assertNotIn(
                    loop_thread,
                    inspection_threads,
                )
                task.cancel()
                await asyncio.sleep(
                    0,
                )
                self.assertFalse(
                    task.done(),
                )
                self.assertIs(
                    actor._image_pull_tasks[self.uri],
                    task,
                )
            finally:
                finish.set()
                await asyncio.gather(
                    task,
                    return_exceptions=True,
                )
        self.assertTrue(
            task.cancelled(),
        )
        self.assertEqual(
            actor._image_pull_tasks,
            {},
        )
        with self.sessions() as db:
            row = db.get(
                Image,
                1,
            )
            self.assertEqual(
                (row.status, row.size_bytes),
                ("cached", 4321),
            )

    async def test_failed_spawn_persists_failure_instead_of_staying_pulling(
        self,
    ) -> None:
        self.prepare.side_effect = FileNotFoundError(
            "runtime missing",
        )
        await self.namespace["_do_pull"](
            1,
            self.uri,
            is_refresh=False,
        )
        with self.sessions() as db:
            self.assertEqual(
                db.get(
                    Image,
                    1,
                ).status,
                "failed",
            )

    async def test_successful_legacy_refresh_keeps_record_bound_to_actual_path(
        self,
    ) -> None:
        payload = b"refreshed SIF"
        (self.root / self.filename).write_bytes(
            payload,
        )
        with self.sessions() as db:
            db.get(
                Image,
                1,
            ).status = "refreshing"
            db.commit()
        await self.namespace["_do_pull"](
            1,
            self.uri,
            is_refresh=True,
        )
        with self.sessions() as db:
            row = db.get(
                Image,
                1,
            )
            self.assertEqual(
                (row.filename, row.size_bytes, row.status),
                (
                    self.filename,
                    len(
                        payload,
                    ),
                    "cached",
                ),
            )

    async def test_delete_of_active_cache_returns_conflict_without_losing_registration(
        self,
    ) -> None:
        path = self.root / self.filename
        path.write_bytes(
            b"SIF",
        )
        self.cache.pin(
            "queued-job",
            self.uri,
        )
        with self.sessions() as db:
            db.get(
                Image,
                1,
            ).status = "cached"
            db.commit()
            with self.assertRaises(
                HTTPException,
            ) as caught:
                await self.namespace["delete_image"](
                    1,
                    db,
                    SimpleNamespace(
                        id="owner",
                    ),
                )
            self.assertEqual(
                caught.exception.status_code,
                409,
            )
            self.assertIsNotNone(
                db.get(
                    Image,
                    1,
                ),
            )
            self.assertTrue(
                path.exists(),
            )
            self.cache.release(
                "queued-job",
            )
            await self.namespace["delete_image"](
                1,
                db,
                SimpleNamespace(
                    id="owner",
                ),
            )
            self.assertIsNone(
                db.get(
                    Image,
                    1,
                ),
            )
        self.assertFalse(
            path.exists(),
        )
        self.assertNotEqual(
            self.cache.path(
                self.uri,
            ),
            str(path),
        )

    async def test_shutdown_waits_for_manual_pull_cleanup(
        self,
    ) -> None:
        started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def pull(
            *args,
            **kwargs,
        ):
            try:
                started.set()
                await asyncio.Future()
            finally:
                cleaning.set()
                await finish.wait()

        self.namespace["_do_pull"] = pull
        self.namespace["_schedule_pull"](
            1,
            self.uri,
            False,
        )
        await asyncio.wait_for(
            started.wait(),
            2,
        )
        closing = asyncio.create_task(
            self.namespace["shutdown_image_pulls"](),
        )
        try:
            await asyncio.wait_for(
                cleaning.wait(),
                2,
            )
            self.assertFalse(
                closing.done(),
            )
            self.assertEqual(
                len(
                    self.namespace["_pull_tasks"],
                ),
                1,
            )
        finally:
            finish.set()
            await asyncio.wait_for(
                closing,
                2,
            )
        self.assertEqual(
            self.namespace["_pull_tasks"],
            set(),
        )


if __name__ == "__main__":
    unittest.main()
