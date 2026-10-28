"""Lost submission replies never authorize duplicate execution or cleanup."""
import importlib.util
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_job_finalization import Base, Job, JobStatus, SERVER, load_mixin


spec = importlib.util.spec_from_file_location('submission_fixture', SERVER / '_slurm_manager' / '_submission.py')
submission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submission)


class SubmissionProtocolTests(unittest.TestCase):
    def test_reply_and_rejection_classification(self):
        cases = [
            (subprocess.CompletedProcess([], 0, '42\n', ''), '42'),
            (subprocess.CompletedProcess([], 0, '42;cluster\n', ''), '42'),
            (subprocess.CompletedProcess([], 0, '', ''), submission.SubmissionUncertain),
            (subprocess.CompletedProcess([], 0, 'garbled', ''), submission.SubmissionUncertain),
            (subprocess.CompletedProcess([], 0, '42\n43', ''), submission.SubmissionUncertain),
            (subprocess.TimeoutExpired([], 60), submission.SubmissionUncertain),
            (subprocess.CalledProcessError(255, [], stderr='connection lost'), submission.SubmissionUncertain),
            (subprocess.CalledProcessError(1, [], stderr='Socket timed out on send/recv operation'), submission.SubmissionUncertain),
            (subprocess.CalledProcessError(1, [], stderr='AssocMaxSubmitJobLimit'), submission.SubmissionDeferred),
            (subprocess.CalledProcessError(1, [], stderr='Job violates accounting/QOS policy'), submission.SubmissionRejected),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                def run(command, **kwargs):
                    self.assertEqual(kwargs['timeout'], 60)
                    if isinstance(value, Exception):
                        raise value
                    return value
                transport = SimpleNamespace(run=run)
                if isinstance(expected, str):
                    self.assertEqual(submission.submit_batch(transport, ['sbatch'], 'script', {}), expected)
                else:
                    with self.assertRaises(expected):
                        submission.submit_batch(transport, ['sbatch'], 'script', {})

    def test_reconciliation_requires_exact_unique_identity(self):
        for queue, accounting, expected in (
            ('42|exact\n', '', {'exact': '42'}),
            ('', '42|exact\n', {'exact': '42'}),
            ('42|exact\n', '42|exact\n', {'exact': '42'}),
            ('42|exact\n', '43|exact\n', {}),
            ('42|exact-extra\n', '', {}),
            ('', '', {}),
        ):
            with self.subTest(queue=queue, accounting=accounting):
                outputs = iter([queue, accounting])
                transport = SimpleNamespace(run=lambda *a, **k: subprocess.CompletedProcess([], 0, next(outputs), ''))
                self.assertEqual(submission.find_submissions(transport, {'exact'}, '2026-10-28T00:00:00', '2026-10-28T00:20:00'), expected)


class SubmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.temporary.name) / 'state.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.job = Job(id='job', status=JobStatus.PENDING)
        self.db.add(self.job)
        self.db.commit()
        namespace = {
            '__name__': __name__, 'Job': Job, 'JobStatus': JobStatus,
            'SessionLocal': self.sessions, 'logger': logging.getLogger(__name__), 'is_local_mode': False,
            'SubmissionDeferred': submission.SubmissionDeferred,
            'SubmissionRejected': submission.SubmissionRejected,
            'SubmissionUncertain': submission.SubmissionUncertain,
            'find_submissions': submission.find_submissions,
        }
        load_mixin('_scheduler/_submission_recovery.py', namespace)
        load_mixin('_scheduler/_submit.py', namespace)
        self.namespace = namespace
        actor_class = type('Actor', (namespace['_SubmissionRecoveryMixin'], namespace['_SubmitMixin']), {})
        self.actor = actor_class()
        self.actor._job_query = lambda db, *entities: db.query(*entities)
        self.actor._staged_job_ids = {'job'}
        self.actor._preparing_jobs_lock = threading.Lock()
        self.actor._submitting_jobs = set()
        self.actor._submission_reconcile_after = 0
        self.actor._submission_reconcile_cursor = 0
        self.actor._submission_cancel_event = Mock()
        self.actor.config = {'transport': {'mode': 'ssh', 'ssh': {'host': 'cluster', 'user': 'account'}}, 'execution': {'slurm': {'account': 'allocation'}}}
        self.actor.terminate_job = Mock()
        self.submissions = 0
        self.reply = subprocess.CompletedProcess([], 0, '42', '')
        self.fail_before_dispatch = False
        self.cancellation = False

        def run(command, **kwargs):
            if self.fail_before_dispatch:
                raise TimeoutError('authentication timed out')
            kwargs['before_start']()
            self.submissions += 1
            with self.sessions() as observer:
                persisted = observer.get(Job, 'job')
                self.assertIsNotNone(persisted.submission_attempt)
                if self.cancellation:
                    persisted.status = JobStatus.TERMINATED
                    observer.commit()
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

        self.transport = SimpleNamespace(run=run)
        def dispatch(**kwargs):
            return submission.submit_batch(self.transport, ['sbatch'], 'script', {}, kwargs['before_start'])
        self.actor.slurm_manager = SimpleNamespace(submit_job_simple=dispatch, transport=self.transport)

    def dispatch(self):
        return self.actor._submit_prepared_slurm(self.db, self.job)

    def test_success_and_concurrent_cancel_preserve_binding(self):
        self.cancellation = True
        self.assertFalse(self.dispatch())
        self.db.refresh(self.job)
        self.assertEqual(self.job.status, JobStatus.TERMINATED)
        self.assertEqual(self.job.slurm_job_id, '42')
        self.assertIsNone(self.job.submission_attempt)
        self.actor.terminate_job.assert_called_once()

    def test_lost_reply_survives_restart_and_reconciles_once(self):
        self.reply = subprocess.TimeoutExpired(['sbatch'], 60)
        self.assertFalse(self.dispatch())
        self.db.refresh(self.job)
        attempt = self.job.submission_attempt
        self.assertIsNotNone(attempt)
        self.assertEqual(self.job.status, JobStatus.PENDING)
        comment = json.loads(attempt)['comment']
        self.actor._submission_reconcile_after = 0
        self.actor._submission_reconcile_cursor = 0
        self.actor._submission_cancel_event = Mock()
        self.actor.slurm_manager.transport = SimpleNamespace(run=lambda *a, **k: subprocess.CompletedProcess([], 0, f'42|{comment}\n', ''))
        self.actor._reconcile_submissions()
        self.db.refresh(self.job)
        self.assertEqual(self.job.slurm_job_id, '42')
        self.assertEqual(self.job.status, JobStatus.QUEUED)
        self.assertIsNone(self.job.submission_attempt)
        self.assertEqual(self.submissions, 1)

    def test_no_match_and_cancelled_unknown_remain_protected(self):
        self.cancellation = True
        self.reply = subprocess.CalledProcessError(255, ['ssh'], stderr='connection lost')
        self.dispatch()
        self.actor.slurm_manager.transport = SimpleNamespace(run=lambda *a, **k: subprocess.CompletedProcess([], 0, '', ''))
        self.actor._reconcile_submissions()
        self.db.refresh(self.job)
        self.assertIsNotNone(self.job.submission_attempt)
        self.assertEqual(self.job.status, JobStatus.TERMINATED)
        self.assertIsNone(self.job.finalization_status)
        self.assertEqual(self.submissions, 1)
        self.actor._fail_job_before_submission(self.db, 'job', 'must not overwrite unknown acceptance')
        self.db.refresh(self.job)
        self.assertIsNotNone(self.job.submission_attempt)

    def test_accounting_windows_rotate_without_growing_for_old_attempts(self):
        for index, at in enumerate(("2020-01-01T00:00:00+00:00", "2026-10-28T00:00:00+00:00")):
            self.db.add(Job(id=f"old-{index}", status=JobStatus.PENDING, submission_attempt=json.dumps({"at": at, "comment": f"old-{index}"})))
        self.db.commit()
        commands = []
        def query(command, **kwargs):
            commands.append(command)
            self.assertEqual(command[:2], ["env", "TZ=UTC"])
            return subprocess.CompletedProcess([], 0, "", "")
        self.actor.slurm_manager.transport = SimpleNamespace(run=query)
        for _ in range(2):
            self.actor._submission_reconcile_after = 0
            self.actor._reconcile_submissions()
        accounting = [command for command in commands if command[2] == "sacct"]
        self.assertEqual(len(accounting), 2)
        starts = []
        for command in accounting:
            start = datetime.fromisoformat(next(value.split("=", 1)[1] for value in command if value.startswith("--starttime=")))
            end = datetime.fromisoformat(next(value.split("=", 1)[1] for value in command if value.startswith("--endtime=")))
            self.assertEqual((end - start).total_seconds(), 1200)
            starts.append(start)
        self.assertNotEqual(starts[0].year, starts[1].year)

    def test_shutdown_during_connection_prevents_late_dispatch(self):
        self.actor._submission_cancel_event.side_effect = InterruptedError("stopping")
        self.assertFalse(self.dispatch())
        self.db.refresh(self.job)
        self.assertIsNone(self.job.submission_attempt)
        self.assertEqual(self.submissions, 0)

    def test_authentication_failure_has_no_submission_intent(self):
        self.fail_before_dispatch = True
        self.assertFalse(self.dispatch())
        self.db.refresh(self.job)
        self.assertIsNone(self.job.submission_attempt)
        self.assertEqual(self.job.status, JobStatus.PENDING)
        self.assertEqual(self.submissions, 0)

    def test_explicit_quota_rejection_clears_intent_and_shares_backoff(self):
        self.reply = subprocess.CalledProcessError(1, ['sbatch'], stderr='AssocMaxSubmitJobLimit')
        self.assertFalse(self.dispatch())
        self.db.refresh(self.job)
        self.assertIsNone(self.job.submission_attempt)
        self.assertEqual(self.job.status, JobStatus.PENDING)
        self.assertTrue(self.actor._submission_deferred())
        sibling = type(self.actor)()
        sibling.config = {**self.actor.config, 'target_id': 'different-target', 'remote_root': '/different-root'}
        self.assertTrue(sibling._submission_deferred())


if __name__ == '__main__':
    unittest.main()
