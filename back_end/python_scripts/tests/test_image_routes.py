"""Cache API ownership and worker teardown using temporary SQLite and files."""

import ast
import asyncio
import logging
import os
import runpy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from sqlalchemy import DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from test_image_cache import SERVER, cache_module


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
            "asyncio": asyncio,
            "datetime": datetime,
            "timezone": timezone,
            "logger": logging.getLogger(
                __name__,
            ),
            "os": os,
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
            "resource_manager": SimpleNamespace(
                ensure_image=self.prepare,
                get_sif_path=self.cache.path,
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
