"""Recover launch uncertainty and failed cleanup without losing runtime evidence."""
import asyncio
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_docker_release import docker
import test_job_finalization as fixtures
from test_job_finalization import Job, JobStatus, load_method, load_mixin
from test_protocol_files import protocol
from test_preparation_commit import PreparationInputs


class DockerSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SlurmFinalizationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.namespace = self.fixture.namespace
        self.namespace.update(
            DockerSubmissionRejected=docker.DockerSubmissionRejected,
            resource_manager=SimpleNamespace(
                get_docker_image=lambda image: image,
            ),
            is_local_mode=True,
        )
        load_mixin('_scheduler/_submission_recovery.py', self.namespace)
        load_mixin('_scheduler/_submit.py', self.namespace)
        actor_type = type('Actor', (
            self.namespace['_SubmissionRecoveryMixin'], self.namespace['_SubmitMixin'],
            self.fixture.scheduler_class,
        ), {})
        self.actor = actor_type()
        self.actor.__dict__.update(self.fixture.scheduler.__dict__)
        self.actor.__dict__.pop("_reconcile_submissions", None)
        self.actor.config = {
            "server": {"address": "http://local", "back_end_port": 1},
            "cluster": {"default_system_entry_command": ""},
            "execution": {"backend": "local"},
        }
        self.actor._submission_cancel_event = Mock()
        self.actor._begin_calls = 0
        self.actor._submission_reconcile_after = 0
        self.actor._submission_reconcile_cursor = 0
        self.actor._init_job_working_dir = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
        self.actor.terminate_job = Mock()
        self.fixture.add_job(status=JobStatus.PENDING)
        self.db = self.fixture.sessions()
        self.addCleanup(self.db.close)
        self.job = self.db.get(Job, 'job')
        self.job.slurm_job_id = None
        self.db.commit()
        self.job.dockerfile = None
        self.job.container_image = 'image'
        self.job.entry_command = 'echo application'
        self.job.system_entry_command = None
        self.job.gpu_count = 0
        self.job.user = SimpleNamespace(token='')
        self.created = {}
        self.starts = 0
        self.reject = False
        self.cancel_during_launch = False
        self.observation = 'OWNED'
        def run(**kwargs):
            kwargs['before_start']()
            self.starts += 1
            with self.fixture.sessions() as observer:
                durable = observer.get(Job, 'job')
                self.assertEqual(json.loads(durable.submission_attempt)['comment'], kwargs['submission_identity'])
                if self.cancel_during_launch:
                    durable.status = JobStatus.TERMINATED
                    observer.commit()
            if self.reject:
                raise docker.DockerSubmissionRejected('invalid image')
            self.created[kwargs['container_name']] = kwargs['submission_identity']
            raise docker.DockerError('reply lost')
        self.actor.docker_manager = SimpleNamespace(
            run_container=run,
            find_submission=lambda name, identity: self.observation,
            check_container_status=lambda name: 'RUNNING',
        )

    def test_lost_launch_reply_is_bound_after_restart_without_second_run(self):
        self.assertFalse(self.actor._submit_to_docker(self.db, self.job))
        self.db.refresh(self.job)
        self.assertEqual(self.job.status, JobStatus.PENDING)
        self.assertIsNotNone(self.job.submission_attempt)
        self.assertFalse(self.actor._clean_up_working_table('job'))
        self.assertFalse(self.actor._submit_to_docker(self.db, self.job))
        self.actor._staged_job_ids.clear()
        self.actor._reconcile_submissions()
        self.db.refresh(self.job)
        self.assertEqual(self.job.slurm_job_id, 'magnus-job-job')
        self.assertEqual(self.job.status, JobStatus.QUEUED)
        self.assertIsNone(self.job.submission_attempt)
        self.assertEqual(self.starts, 1)

    def test_cancel_during_lost_reply_keeps_identity_for_late_stop(self):
        self.cancel_during_launch = True
        self.actor._submit_to_docker(self.db, self.job)
        self.actor._reconcile_submissions()
        self.db.refresh(self.job)
        self.assertEqual(self.job.status, JobStatus.TERMINATED)
        self.assertEqual(self.job.slurm_job_id, 'magnus-job-job')
        self.actor.terminate_job.assert_called_once()
        self.assertEqual(self.starts, 1)

    def test_absence_or_foreign_name_after_lost_reply_never_authorizes_cleanup(self):
        self.actor._submit_to_docker(self.db, self.job)
        for observation in ('MISSING', 'CONFLICT', 'UNKNOWN'):
            self.observation = observation
            self.actor._submission_reconcile_after = 0
            self.actor._reconcile_submissions()
            self.db.refresh(self.job)
            self.assertIsNotNone(self.job.submission_attempt)
            self.assertIsNone(self.job.finalization_status)
        self.assertEqual(self.starts, 1)

    def test_definite_rejection_with_confirmed_absence_can_finalize(self):
        self.reject = True
        self.observation = 'MISSING'
        self.actor._submit_to_docker(self.db, self.job)
        self.actor._reconcile_submissions()
        self.db.refresh(self.job)
        self.assertEqual(self.job.status, JobStatus.FAILED)
        self.assertIsNone(self.job.submission_attempt)
        self.assertIsNone(self.job.slurm_job_id)


class RuntimeFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SlurmFinalizationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.actor = self.fixture.scheduler
        self.fixture.namespace['replace_protocol_file'] = protocol.replace_protocol_file
        self.fixture.namespace['is_local_mode'] = True
        self.table = self.fixture.add_job(status=JobStatus.RUNNING)
        self.actor._docker_log_cursors = {}
        self.actor._docker_job_timed_out = lambda job: False
        self.actor.docker_manager = SimpleNamespace(
            check_container_status=Mock(return_value='COMPLETED'),
            remove_container=Mock(),
            get_termination_info=lambda name: {'oom_killed': False},
        )

    def test_failed_final_log_retrieval_keeps_terminal_state_container_and_workspace(self):
        self.actor._dump_docker_logs = Mock(side_effect=RuntimeError('log transport failed'))
        self.actor._sync_reality_docker()
        self.assertEqual(self.fixture.state()[:3], (JobStatus.SUCCESS, 'slurm-job', 'COMPLETED'))
        self.actor.docker_manager.remove_container.assert_not_called()
        self.assertTrue((self.table / 'repository').exists())
        restarted = self.fixture.new_scheduler()
        restarted.docker_manager = self.actor.docker_manager
        restarted._dump_docker_logs = Mock(return_value='cursor')
        restarted._docker_log_cursors = {}
        restarted._sync_reality_docker()
        self.assertEqual(self.fixture.state()[2], "CLEANUP")
        self.fixture.cleanup(restarted)
        self.assertEqual(self.fixture.state()[:3], (JobStatus.SUCCESS, None, None))
        restarted.docker_manager.remove_container.assert_called_once()
        self.assertFalse((self.table / 'repository').exists())

    def test_failed_or_timed_out_snapshot_preserves_log_then_success_replaces_it(self):
        output = self.table / 'slurm/output.txt'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'previous durable log')
        failure = None
        code = 1
        def execute(command, **kwargs):
            self.assertEqual(kwargs['timeout'], 30)
            self.assertNotEqual(kwargs['stdout'], subprocess.PIPE)
            kwargs['stdout'].write(b'complete log' if code == 0 else b'partial')
            if failure:
                raise failure
            return subprocess.CompletedProcess(command, code)
        with patch.object(self.fixture.namespace['subprocess'], 'run', side_effect=execute):
            for failure in (None, subprocess.TimeoutExpired(['docker', 'logs'], 30)):
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                    self.actor._dump_docker_logs('job', 'container', final=True)
                self.assertEqual(output.read_bytes(), b'previous durable log')
            failure = None
            code = 0
            self.actor._dump_docker_logs('job', 'container', final=True)
            self.actor._dump_docker_logs('job', 'container', final=True)
        self.assertEqual(output.read_bytes(), b'complete log')
        self.assertFalse(list(self.fixture.root.glob('.docker-logs-*')))

    def test_cancel_stops_container_but_defers_removal_until_final_log_is_safe(self):
        namespace = {**self.fixture.namespace, 'is_local_mode': True}
        terminate = load_method('_scheduler/_core.py', 'terminate_job', namespace)
        self.actor.enabled = True
        self.actor.preparing_jobs = {}
        self.actor._loop = None
        self.actor.docker_manager.stop_container = Mock()
        with self.fixture.sessions() as db:
            terminate(self.actor, db, db.get(Job, 'job'))
        self.assertEqual(self.fixture.state()[:3], (JobStatus.TERMINATED, 'slurm-job', None))
        self.actor.docker_manager.stop_container.assert_called_once()
        self.actor.docker_manager.remove_container.assert_not_called()
        self.actor._dump_docker_logs = Mock(return_value='cursor')
        self.actor._sync_reality_docker()
        self.assertEqual(self.fixture.state()[2], "CLEANUP")
        self.fixture.cleanup(self.actor)
        self.assertEqual(self.fixture.state()[:3], (JobStatus.TERMINATED, None, None))

    def test_known_terminal_allocation_gets_short_release_recheck(self):
        self.fixture.namespace['is_local_mode'] = False
        with self.fixture.sessions() as db:
            job = db.get(Job, 'job')
            job.status = JobStatus.FAILED
            job.finalization_status = 'FAILED'
            db.commit()
        self.fixture.release_overrides['slurm-job'] = False
        self.assertEqual(self.actor._recover_job_artifacts('job', 'slurm-job'), 10)
        self.fixture.release_overrides['slurm-job'] = True
        self.actor._recover_job_artifacts('job', 'slurm-job')
        self.assertEqual(self.fixture.state()[2], 'CLEANUP')
        self.fixture.cleanup(self.actor)
        self.assertIsNone(self.fixture.state()[1])


class PreparationCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_preparation_persists_cleanup_and_shutdown_keeps_retryable_state(self):
        for cancelled, shutdown in ((False, False), (True, False), (True, True)):
            with self.subTest(cancelled=cancelled, shutdown=shutdown):
                fixture = fixtures.SlurmFinalizationTests()
                fixture.setUp()
                try:
                    fixture.add_job(status=JobStatus.PREPARING)
                    with fixture.sessions() as db:
                        db.get(Job, 'job').slurm_job_id = None
                        db.commit()
                    entered = asyncio.Event()
                    async def repository(**kwargs):
                        entered.set()
                        if cancelled:
                            await asyncio.Event().wait()
                        return False, 'repository unavailable', None
                    fixture.namespace.update(
                        asyncio=asyncio,
                        PreparationInputs=PreparationInputs,
                        resource_manager=SimpleNamespace(
                            ensure_repo=repository,
                            builds=SimpleNamespace(release=Mock()),
                            images=SimpleNamespace(pin=Mock(), release=Mock()),
                        ),
                        guarantee_file_exist=lambda path, **kwargs: Path(path).mkdir(parents=True, exist_ok=True),
                    )
                    async def blocking(function, *args):
                        return await asyncio.to_thread(function, *args)
                    fixture.namespace['_run_blocking_io'] = blocking
                    prepare = load_method('_scheduler/_resources.py', '_prepare_job_resources', fixture.namespace)
                    actor = fixture.scheduler
                    actor.config = {'cluster': {'default_runner': 'runner'}}
                    actor._pinned_jobs = set()
                    actor._ensure_image_decoupled = AsyncMock(return_value=(True, None))
                    fixture.cleanup_failure.add('job')
                    with patch.multiple(Job, container_image='image', dockerfile=None, runner=None, namespace='org', repo_name='repo', branch='main', commit_sha='HEAD', create=True):
                        task = asyncio.create_task(prepare(actor, 'job'))
                        await asyncio.wait_for(entered.wait(), 2)
                        if cancelled:
                            if not shutdown:
                                with fixture.sessions() as db:
                                    db.get(Job, 'job').status = JobStatus.TERMINATED
                                    db.commit()
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                        else:
                            await task
                    if shutdown:
                        self.assertEqual(fixture.state()[:3], (JobStatus.PREPARING, None, None))
                        self.assertFalse(any(item[0] == 'cleanup' for item in fixture.events))
                    else:
                        expected = JobStatus.TERMINATED if cancelled else JobStatus.FAILED
                        self.assertEqual(fixture.state()[:3], (expected, None, 'CLEANUP'))
                        fixture.cleanup_failure.clear()
                        fixture.cleanup(fixture.new_scheduler())
                        self.assertEqual(fixture.state()[:3], (expected, None, None))
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
