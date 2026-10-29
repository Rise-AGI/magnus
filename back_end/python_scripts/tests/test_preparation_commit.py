"""Preparation owns its resources until the Pending transaction commits."""

import asyncio
import json
import logging
from pathlib import Path
import runpy
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from sqlalchemy import Enum, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from test_image_cache import blocking_io
from test_job_finalization import SERVER, JobStatus, load_method


PreparationInputs = runpy.run_path(
    str(
        SERVER / "_scheduler/_preparation_inputs.py",
    ),
)["PreparationInputs"]


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
    slurm_job_id: Mapped[str | None] = mapped_column(String)
    submission_attempt: Mapped[str | None] = mapped_column(String)
    finalization_status: Mapped[str | None] = mapped_column(String)


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
                "PreparationInputs": PreparationInputs,
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

    async def test_revision_commit_is_joined_before_cancelled_preparation_cleans_up(
        self,
    ) -> None:
        self.add_job(
            "job",
            build=False,
        )
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        write_threads = []

        def hold_revision_commit(
            db,
        ) -> None:
            job = db.get(
                Job,
                "job",
            )
            if job.commit_sha != "a" * 40 or entered.is_set():
                return
            write_threads.append(
                threading.get_ident(),
            )
            entered.set()
            if threading.get_ident() != loop_thread and not release.wait(
                3,
            ):
                raise AssertionError(
                    "Revision transaction not released",
                )

        event.listen(
            self.sessions,
            "before_commit",
            hold_revision_commit,
        )

        async def prepare_image(
            *arguments,
        ) -> None:
            await asyncio.Event().wait()

        self.actor._ensure_image_decoupled = prepare_image
        task = asyncio.create_task(
            self.prepare(
                self.actor,
                "job",
            ),
        )
        try:
            self.assertTrue(
                await asyncio.to_thread(
                    entered.wait,
                    3,
                ),
            )
            self.assertNotIn(
                loop_thread,
                write_threads,
            )
            task.cancel()
            await asyncio.sleep(
                0,
            )
            self.assertFalse(
                task.done(),
            )
            self.manager.images.release.assert_not_called()
            self.actor._finish_unsubmitted_job.assert_not_called()
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
        with self.sessions() as db:
            job = db.get(
                Job,
                "job",
            )
            self.assertEqual(
                (job.status, job.commit_sha, job.branch),
                (JobStatus.PREPARING, "a" * 40, "main"),
            )
        self.manager.images.release.assert_called_once_with(
            "job",
        )
        self.actor._finish_unsubmitted_job.assert_called_once_with(
            "job",
        )

    async def test_all_preparation_database_and_directory_work_leaves_the_loop(
        self,
    ) -> None:
        threads = []

        def observed(
            *arguments,
        ) -> None:
            threads.append(
                threading.get_ident(),
            )

        create = self.prepare.__globals__["guarantee_file_exist"]

        def create_directory(
            *arguments,
            **keywords,
        ) -> None:
            observed()
            create(
                *arguments,
                **keywords,
            )

        self.prepare.__globals__["guarantee_file_exist"] = create_directory
        for build, failed in ((False, False), (True, False), (False, True)):
            with self.subTest(
                build=build,
                failed=failed,
            ):
                job_id = f"job-{build}-{failed}"
                self.add_job(
                    job_id,
                    build=build,
                )
                if failed:
                    self.manager.ensure_repo.return_value = (False, "repository unavailable", None)
                threads.clear()
                event.listen(
                    self.engine,
                    "before_cursor_execute",
                    observed,
                )
                try:
                    await self.prepare(
                        self.actor,
                        job_id,
                    )
                finally:
                    event.remove(
                        self.engine,
                        "before_cursor_execute",
                        observed,
                    )
                self.assertTrue(
                    threads,
                )
                self.assertNotIn(
                    threading.get_ident(),
                    threads,
                )

    async def test_cancel_at_pending_commit_keeps_shutdown_pins_and_cleans_termination(
        self,
    ) -> None:
        finish = load_method(
            "_scheduler/_job_lifecycle.py",
            "_finish_unsubmitted_job",
            self.prepare.__globals__,
        )
        self.actor._job_query = lambda db, *entities: db.query(*entities)
        self.actor._finish_unsubmitted_job = lambda job_id: finish(self.actor, job_id)
        terminate_job = load_method(
            "_scheduler/_core.py",
            "terminate_job",
            self.prepare.__globals__,
        )
        self.actor.enabled = True
        self.actor.preparing_jobs = {}
        self.actor._submitting_jobs = set()
        self.actor._loop = asyncio.get_running_loop()
        for phase in ("before_commit", "after_commit"):
            for terminated in (False, True):
                with self.subTest(
                    phase=phase,
                    terminated=terminated,
                ):
                    job_id = f"job-{phase}-{terminated}"
                    self.add_job(
                        job_id,
                        build=False,
                    )
                    entered, release = threading.Event(), threading.Event()
                    pending_commits = set()
                    self.actor._finish_job_cleanup = Mock()

                    def before_commit(
                        db,
                    ) -> None:
                        job = db.get(
                            Job,
                            job_id,
                        )
                        if job.status == JobStatus.PENDING and not entered.is_set():
                            pending_commits.add(
                                id(
                                    db,
                                ),
                            )
                            if phase == "before_commit":
                                hold()

                    def hold() -> None:
                        entered.set()
                        if not release.wait(
                            3,
                        ):
                            raise AssertionError(
                                "Pending transaction not released",
                            )

                    def after_commit(
                        db,
                    ) -> None:
                        if phase == "after_commit" and id(db) in pending_commits:
                            hold()

                    def terminate() -> None:
                        with self.sessions() as db:
                            terminate_job(
                                self.actor,
                                db,
                                db.get(
                                    Job,
                                    job_id,
                                ),
                            )

                    event.listen(
                        self.sessions,
                        "before_commit",
                        before_commit,
                    )
                    event.listen(
                        self.sessions,
                        "after_commit",
                        after_commit,
                    )
                    task = asyncio.create_task(
                        self.prepare(
                            self.actor,
                            job_id,
                        ),
                    )
                    self.actor.preparing_jobs[job_id] = task
                    termination = None
                    try:
                        self.assertTrue(
                            await asyncio.to_thread(
                                entered.wait,
                                3,
                            ),
                        )
                        if terminated:
                            termination = asyncio.create_task(
                                asyncio.to_thread(
                                    terminate,
                                ),
                            )
                            if phase == "after_commit":
                                await termination
                        else:
                            task.cancel()
                        await asyncio.sleep(
                            0,
                        )
                        self.assertFalse(
                            task.done(),
                        )
                        self.manager.images.release.assert_not_called()
                        release.set()
                        if termination is not None:
                            await termination
                            await asyncio.gather(
                                task,
                                return_exceptions=True,
                            )
                            cleanup = self.actor.preparing_jobs[job_id]
                            if cleanup is not task:
                                await cleanup
                        else:
                            with self.assertRaises(
                                asyncio.CancelledError,
                            ):
                                await task
                    finally:
                        release.set()
                        if termination is not None:
                            await asyncio.gather(
                                termination,
                                return_exceptions=True,
                            )
                        task.cancel()
                        await asyncio.gather(
                            task,
                            return_exceptions=True,
                        )
                        event.remove(
                            self.sessions,
                            "before_commit",
                            before_commit,
                        )
                        event.remove(
                            self.sessions,
                            "after_commit",
                            after_commit,
                        )
                    with self.sessions() as db:
                        job = db.get(
                            Job,
                            job_id,
                        )
                        self.assertEqual(
                            job.status,
                            JobStatus.TERMINATED if terminated else JobStatus.PENDING,
                        )
                        self.assertEqual(
                            job.finalization_status,
                            "CLEANUP" if terminated else None,
                        )
                    if terminated:
                        self.actor._finish_job_cleanup.assert_called_once_with(
                            job_id,
                            None,
                        )
                    else:
                        self.actor._finish_job_cleanup.assert_not_called()
                    self.assertIn(
                        job_id,
                        self.actor._pinned_jobs,
                    )
                    self.manager.images.release.assert_not_called()

    async def test_cancel_during_directory_creation_joins_before_cleanup(
        self,
    ) -> None:
        self.add_job(
            "job",
            build=False,
        )
        entered, release = threading.Event(), threading.Event()
        create = self.prepare.__globals__["guarantee_file_exist"]

        def hold_directory_creation(
            *arguments,
            **keywords,
        ) -> None:
            create(
                *arguments,
                **keywords,
            )
            entered.set()
            if not release.wait(
                3,
            ):
                raise AssertionError(
                    "Directory creation not released",
                )

        self.prepare.__globals__["guarantee_file_exist"] = hold_directory_creation
        task = asyncio.create_task(
            self.prepare(
                self.actor,
                "job",
            ),
        )
        try:
            self.assertTrue(
                await asyncio.to_thread(
                    entered.wait,
                    3,
                ),
            )
            task.cancel()
            await asyncio.sleep(
                0,
            )
            self.assertFalse(
                task.done(),
            )
            self.actor._finish_unsubmitted_job.assert_not_called()
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
        self.assertTrue(
            (self.root / "jobs/job").is_dir(),
        )
        self.manager.ensure_repo.assert_not_called()
        self.manager.builds.ensure.assert_not_called()
        self.actor._finish_unsubmitted_job.assert_called_once_with(
            "job",
        )

    async def test_stale_preparation_candidate_preserves_pending_ownership(
        self,
    ) -> None:
        self.add_job(
            "job",
            build=False,
        )
        with self.sessions() as db:
            db.get(
                Job,
                "job",
            ).status = JobStatus.PENDING
            db.commit()
        self.actor._pinned_jobs.add(
            "job",
        )
        await self.prepare(
            self.actor,
            "job",
        )
        self.manager.ensure_repo.assert_not_called()
        self.manager.builds.ensure.assert_not_called()
        self.manager.images.release.assert_not_called()
        self.manager.builds.release.assert_not_called()
        self.assertEqual(
            self.actor._pinned_jobs,
            {"job"},
        )
