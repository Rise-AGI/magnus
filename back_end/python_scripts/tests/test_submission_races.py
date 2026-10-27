"""External execution replies must preserve a concurrent user cancellation."""
from datetime import datetime, timezone
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_job_finalization import Base, Job, JobStatus, load_method


class SubmissionRaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(temporary.name) / 'state.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.job = Job(id="job", status=JobStatus.PENDING)
        self.db.add(self.job)
        self.db.commit()
        namespace = {"Job": Job, "JobStatus": JobStatus, "logger": logging.getLogger(__name__)}
        self.record = load_method("_scheduler/_submit.py", "_record_submission", namespace)
        self.fail = load_method("_scheduler/_submit.py", "_fail_job_before_submission", namespace)
        self.cancel = Mock()
        self.actor = SimpleNamespace(_job_query=lambda db, *entities: db.query(*entities), terminate_job=self.cancel)

    def cancel_from_other_session(self):
        with self.sessions() as db:
            job = db.get(Job, "job")
            job.status = JobStatus.TERMINATED
            db.commit()

    def test_late_success_keeps_terminal_status_and_external_association(self):
        for external_id in ("123", "magnus-job-job"):
            self.cancel_from_other_session()
            self.assertFalse(self.record(self.actor, self.db, self.job, external_id))
            self.assertEqual(self.job.status, JobStatus.TERMINATED)
            self.assertIsNotNone(self.job.slurm_job_id)
            self.cancel.assert_called_with(self.db, self.job)

    def test_late_failure_does_not_overwrite_cancellation(self):
        self.cancel_from_other_session()
        self.fail(self.actor, self.db, "job", "rejected")
        self.db.refresh(self.job)
        self.assertEqual(self.job.status, JobStatus.TERMINATED)
        self.assertIsNone(self.job.result)
        self.assertIsNone(self.job.finalization_status)

    def test_failed_late_cancellation_keeps_id_for_reconciliation(self):
        self.cancel_from_other_session()
        self.cancel.side_effect = RuntimeError("not acknowledged")
        self.assertFalse(self.record(self.actor, self.db, self.job, "123"))
        with self.sessions() as db:
            persisted = db.get(Job, "job")
            self.assertEqual(persisted.status, JobStatus.TERMINATED)
            self.assertEqual(persisted.slurm_job_id, "123")
            self.assertIsNone(persisted.finalization_status)

    def test_normal_success_is_queued(self):
        self.assertTrue(self.record(self.actor, self.db, self.job, "123"))
        self.assertEqual(self.job.status, JobStatus.QUEUED)
        self.assertEqual(self.job.slurm_job_id, "123")
        self.cancel.assert_not_called()

    def test_cancellation_between_id_commit_and_refresh_is_observed(self):
        original = self.db.commit

        def commit_then_cancel():
            original()
            self.cancel_from_other_session()

        self.db.commit = commit_then_cancel
        self.assertFalse(self.record(self.actor, self.db, self.job, "123"))
        self.assertEqual(self.job.status, JobStatus.TERMINATED)
        self.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
