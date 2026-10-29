"""Preparation owns its resources until the Pending transaction commits."""

import asyncio
import json
import logging
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from sqlalchemy import Enum, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from test_image_cache import blocking_io
from test_job_finalization import JobStatus, load_method


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus))
    container_image: Mapped[str | None] = mapped_column(String)
    dockerfile: Mapped[str | None] = mapped_column(String)
    build_result: Mapped[str | None] = mapped_column(String)
    runner: Mapped[str | None] = mapped_column(String)
    namespace: Mapped[str | None] = mapped_column(String)
    repo_name: Mapped[str | None] = mapped_column(String)
    branch: Mapped[str | None] = mapped_column(String)
    commit_sha: Mapped[str | None] = mapped_column(String)
    user_id: Mapped[str | None] = mapped_column(String)
    result: Mapped[str | None] = mapped_column(String)


class PreparationCommitTests(unittest.IsolatedAsyncioTestCase):
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
        self.engine = create_engine(
            f"sqlite:///{self.root / 'state.db'}",
        )
        self.addCleanup(
            self.engine.dispose,
        )
        Base.metadata.create_all(
            self.engine,
        )
        self.sessions = sessionmaker(
            bind=self.engine,
        )
        self.manager = SimpleNamespace(
            ensure_repo=AsyncMock(
                return_value=(True, "a" * 40, "main"),
            ),
            builds=SimpleNamespace(
                ensure=AsyncMock(
                    return_value={"sif_path": "prepared.sif"},
                ),
                release=Mock(),
            ),
            images=SimpleNamespace(
                pin=Mock(),
                release=Mock(),
            ),
        )

        def create_directory(
            path,
            **kwargs,
        ) -> None:
            Path(
                path,
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        self.prepare = load_method(
            "_scheduler/_resources.py",
            "_prepare_job_resources",
            {
                "asyncio": asyncio,
                "json": json,
                "SessionLocal": self.sessions,
                "Job": Job,
                "JobStatus": JobStatus,
                "logger": logging.getLogger(
                    __name__,
                ),
                "magnus_workspace_path": str(
                    self.root,
                ),
                "resource_manager": self.manager,
                "_run_blocking_io": blocking_io,
                "guarantee_file_exist": create_directory,
            },
        )
        self.actor = SimpleNamespace(
            config={"cluster": {"default_runner": "runner"}},
            _preparing_jobs_lock=threading.Lock(),
            _pinned_jobs=set(),
            _ensure_image_decoupled=AsyncMock(
                return_value=(True, None),
            ),
            _finish_unsubmitted_job=Mock(),
        )

    def add_job(
        self,
        job_id: str,
        *,
        build: bool,
    ) -> None:
        with self.sessions() as db:
            db.add(
                Job(
                    id=job_id,
                    status=JobStatus.PREPARING,
                    container_image=None if build else "example/image",
                    dockerfile="FROM example/image" if build else None,
                    namespace="org",
                    repo_name="repository",
                    branch="main",
                    commit_sha="HEAD",
                ),
            )
            db.commit()

    async def test_failed_pending_commit_releases_prepared_resources(
        self,
    ) -> None:
        rejected = set()

        def reject_pending_commit(
            db,
        ) -> None:
            for job_id, in db.query(
                Job.id,
            ).filter(
                Job.status == JobStatus.PENDING,
            ).all():
                if job_id not in rejected:
                    rejected.add(
                        job_id,
                    )
                    raise RuntimeError(
                        "injected transaction commit failure",
                    )

        event.listen(
            self.sessions,
            "before_commit",
            reject_pending_commit,
        )
        for build in (False, True):
            with self.subTest(
                build=build,
            ):
                job_id = f"job-{build}"
                self.add_job(
                    job_id,
                    build=build,
                )
                await self.prepare(
                    self.actor,
                    job_id,
                )
                with self.sessions() as db:
                    self.assertEqual(
                        db.get(
                            Job,
                            job_id,
                        ).status,
                        JobStatus.FAILED,
                    )
                self.assertIn(
                    job_id,
                    rejected,
                )
                self.assertNotIn(
                    job_id,
                    self.actor._pinned_jobs,
                )
                self.manager.images.release.assert_called_with(
                    job_id,
                )
                self.manager.builds.release.assert_called_with(
                    job_id,
                )
                self.actor._finish_unsubmitted_job.assert_called_with(
                    job_id,
                )

    async def test_successful_pending_commit_keeps_prepared_resources(
        self,
    ) -> None:
        for build in (False, True):
            with self.subTest(
                build=build,
            ):
                job_id = f"job-{build}"
                self.add_job(
                    job_id,
                    build=build,
                )
                await self.prepare(
                    self.actor,
                    job_id,
                )
                with self.sessions() as db:
                    self.assertEqual(
                        db.get(
                            Job,
                            job_id,
                        ).status,
                        JobStatus.PENDING,
                    )
                self.assertIn(
                    job_id,
                    self.actor._pinned_jobs,
                )
        self.manager.images.release.assert_not_called()
        self.manager.builds.release.assert_not_called()
        self.actor._finish_unsubmitted_job.assert_not_called()
