"""Real filesystem, SQLite and transfer workers; no running Magnus instance."""
import ast
from concurrent.futures import ThreadPoolExecutor
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_custody_stage_out import FileTransport, load_staging
from test_file_custody_lifecycle import load_manager
from test_job_finalization import Base, Job, JobStatus, load_method
from test_transport_timeouts import load_transport


JOB = "0123456789abcdef"


class LiveCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager_namespace = load_manager()
        self.manager_namespace['magnus_config'] = {'server': {
            'root': str(self.root / 'host'),
            'file_custody': {
                'max_size': '64M', 'max_file_size': None, 'max_processes': 20,
                'default_ttl_minutes': 60, 'max_ttl_minutes': 60,
            },
        }}
        self.manager = self.manager_namespace['FileCustodyManager']()
        self.staging = load_staging()
        self.transport = FileTransport()
        self.remote = self.root / 'remote'
        self.local = self.root / 'local'
        self.relay = self.make_relay()

    def make_relay(self):
        return self.staging['CustodyRelay'](
            self.transport, self.manager,
            lambda job: str(self.local), lambda job: str(self.remote),
            self.staging['_validate_custody_metadata'], self.staging['logger'], 30,
        )

    def publish(self, suffix='a', data=b'result', **metadata):
        token = 'relay-' + suffix * 32
        directory = self.remote / '.magnus_custody_drop' / token
        directory.mkdir(parents=True)
        (directory / 'payload').write_bytes(data)
        (directory / 'meta.json').write_text(json.dumps({
            'token': token, 'filename': 'result', 'storage_filename': 'payload',
            'size_bytes': len(data), 'expire_minutes': 5, 'max_downloads': 2,
            **metadata,
        }))
        return token

    def poll(self):
        return self.relay.poll(JOB, time.monotonic() + 5)

    def test_runtime_restart_and_terminal_ttl_preserve_claims(self):
        token = self.publish()
        self.assertTrue(self.poll())
        self.assertTrue(math.isinf(self.manager.get_entry(token).expires_at))
        self.manager.get_file_path(token)
        self.manager.release_file(token)
        self.manager.shutdown()
        self.manager = self.manager_namespace['FileCustodyManager']()
        entry = self.manager.get_entry(token)
        self.assertEqual(entry.download_count, 1)
        self.assertTrue(math.isinf(entry.expires_at))
        with patch.object(self.manager_namespace['time'], 'time', return_value=1000):
            self.manager.finish_job(JOB)
        with patch.object(self.manager_namespace['time'], 'time', return_value=1100):
            self.manager.finish_job(JOB)
            self.manager = self.manager_namespace['FileCustodyManager']()
            self.assertEqual(self.manager.get_entry(token).expires_at, 1300)
            self.assertIsNotNone(self.manager.get_file_path(token))
            self.manager.release_file(token)
        self.assertEqual(self.manager.get_file_status(token), 'unknown')

    def test_late_import_gets_its_window_without_renewing_earlier_files(self):
        first = self.publish()
        self.poll()
        with patch.object(self.manager_namespace['time'], 'time', return_value=1000):
            self.manager.finish_job(JOB)
        later = self.publish('b')
        with patch.object(self.manager_namespace['time'], 'time', return_value=1100):
            self.poll()
            self.manager.finish_job(JOB)
            self.assertEqual(self.manager.get_entry(first).expires_at, 1300)
            self.assertEqual(self.manager.get_entry(later).expires_at, 1400)

    def test_full_descriptor_cache_still_discovers_new_small_files(self):
        for number in range(300):
            token = "relay-" + f"{number:032x}"
            directory = self.remote / ".magnus_custody_drop" / token
            directory.mkdir(parents=True)
            with (directory / "payload").open("wb") as payload:
                payload.truncate(9 * 1024 * 1024)
            (directory / "meta.json").write_text(json.dumps({
                "token": token, "filename": "large", "storage_filename": "payload",
                "size_bytes": 9 * 1024 * 1024,
            }))
        for _ in range(4):
            self.assertFalse(self.poll())
        self.assertEqual(len(self.relay._state(JOB).pending), 256)
        small = self.publish("f", b"new diagnostic")
        self.assertFalse(self.poll())
        self.assertEqual(self.manager.get_file_status(small), "ready")
        self.assertLessEqual(len(self.relay._state(JOB).pending), 256)
        self.assertEqual(len(self.transport.fetches), 1)

    def test_final_logs_keep_full_budget_and_do_not_block_new_checkpoints(self):
        started, release = threading.Event(), threading.Event()
        budgets = []
        def logs(job, *, timeout, cancel_event):
            budgets.append(timeout)
            started.set()
            self.assertTrue(release.wait(5))
        self.relay.transfer_timeout = 900
        self.relay.transfer_logs = logs
        self.assertFalse(self.relay.request_final_logs(JOB))
        self.assertIn(JOB, self.relay.bulk_job_ids())
        with ThreadPoolExecutor(max_workers=1) as pool:
            transfer = pool.submit(self.relay.transfer_bulk, JOB)
            try:
                self.assertTrue(started.wait(3))
                small = self.publish(data=b"independent JSON")
                self.assertTrue(self.poll())
                self.assertEqual(self.manager.get_file_status(small), "ready")
                self.assertFalse(transfer.done())
            finally:
                release.set()
            transfer.result(timeout=3)
        self.assertEqual(budgets, [900])
        self.assertTrue(self.relay.request_final_logs(JOB))
        self.assertNotIn(JOB, self.relay.bulk_job_ids())

    def test_small_checkpoint_arriving_during_bulk_transfer_is_downloadable(self):
        bulk = self.publish(data=b'x' * (9 * 1024 * 1024))
        self.assertFalse(self.poll())
        started, release = threading.Event(), threading.Event()
        original = self.transport.fetch_bounded
        def receive(remote, local, **kwargs):
            if bulk in remote:
                started.set()
                self.assertTrue(release.wait(5))
            original(remote, local, **kwargs)
        self.transport.fetch_bounded = receive
        with ThreadPoolExecutor(max_workers=1) as pool:
            transfer = pool.submit(self.relay.transfer_bulk, JOB)
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(self.manager._current_size, 9 * 1024 * 1024)
                small = self.publish('b', b'{"converged": true}')
                self.assertFalse(self.poll())
                acquired = self.manager.get_file_path(small)
                self.assertEqual(acquired[0].read_bytes(), b'{"converged": true}')
                self.manager.release_file(small)
                self.assertFalse(transfer.done())
            finally:
                release.set()
            transfer.result(timeout=3)
        self.assertTrue(self.poll())
        self.assertEqual(self.manager.get_entry(bulk).file_size, 9 * 1024 * 1024)
        self.assertFalse((self.local / '.magnus_custody_drop').exists())

    def test_capacity_is_reserved_before_network_and_source_survives_backpressure(self):
        token = self.publish(data=b'123456')
        self.manager._max_size = 4
        with self.assertLogs('custody-stage-test', level='WARNING'):
            self.assertFalse(self.poll())
        self.assertEqual(self.transport.fetches, [])
        self.assertEqual(self.manager._current_size, 0)
        self.assertTrue((self.remote / '.magnus_custody_drop' / token / 'payload').exists())
        self.manager._max_size = 10
        self.relay = self.make_relay()
        self.assertTrue(self.poll())
        self.assertEqual(self.manager.get_entry(token).file_size, 6)

    def test_consumption_between_publication_and_receipt_commit_never_resurrects(self):
        token = self.publish(max_downloads=1)
        with patch.dict(self.staging, {'_write_custody_receipts': lambda *args: (_ for _ in ()).throw(OSError('full'))}):
            with self.assertLogs('custody-stage-test', level='WARNING'):
                self.assertFalse(self.poll())
        acquired = self.manager.get_file_path(token)
        self.assertEqual(acquired[0].read_bytes(), b'result')
        self.manager.release_file(token)
        self.manager.shutdown()
        self.manager = self.manager_namespace['FileCustodyManager']()
        self.relay = self.make_relay()
        self.assertTrue(self.poll())
        self.assertEqual(len(self.transport.fetches), 1)
        self.assertIsNone(self.manager.get_file_path(token))
        self.assertIsNone(self.manager.import_receipt(JOB, token))
        self.assertIn(token, self.staging['_read_custody_receipts'](str(self.local))['entries'])

    def test_index_acknowledgement_after_crash_is_reconciled_from_receipts(self):
        token = self.publish()
        with patch.object(self.manager, 'acknowledge_import', side_effect=OSError('interrupted')):
            with self.assertLogs('custody-stage-test', level='WARNING'):
                self.assertFalse(self.poll())
        self.manager = self.manager_namespace['FileCustodyManager']()
        self.relay = self.make_relay()
        self.assertIsNotNone(self.manager.import_receipt(JOB, token))
        self.assertTrue(self.poll())
        self.assertIsNone(self.manager.import_receipt(JOB, token))
        self.assertEqual(len(self.transport.fetches), 1)

    def test_visible_receipt_does_not_retire_guard_when_directory_sync_fails(self):
        token = self.publish()
        globals_ = self.staging['_write_custody_receipts'].__globals__
        with patch.dict(globals_, {'_sync_directory': lambda path: (_ for _ in ()).throw(OSError('sync failed'))}):
            with self.assertLogs('custody-stage-test', level='WARNING'):
                self.assertFalse(self.poll())
            self.relay = self.make_relay()
            with self.assertRaises(OSError):
                self.poll()
            self.assertIsNotNone(self.manager.import_receipt(JOB, token))
        self.assertTrue(self.poll())
        self.assertIsNone(self.manager.import_receipt(JOB, token))
        self.assertEqual(len(self.transport.fetches), 1)

    def test_failed_guard_ack_without_restart_cannot_revive_consumed_file(self):
        token = self.publish(max_downloads=1)
        with patch.object(self.manager, 'acknowledge_import', side_effect=OSError('interrupted')):
            with self.assertLogs('custody-stage-test', level='WARNING'):
                self.poll()
        acquired = self.manager.get_file_path(token)
        self.assertEqual(acquired[0].read_bytes(), b'result')
        self.manager.release_file(token)
        for pending in self.relay._state(JOB).pending.values():
            pending.retry_after = 0
        self.assertTrue(self.poll())
        self.assertIsNone(self.manager.get_file_path(token))
        self.assertEqual(len(self.transport.fetches), 1)
        self.assertIsNone(self.manager.import_receipt(JOB, token))

    def test_duplicate_token_from_another_job_is_rejected(self):
        token = self.publish()
        with patch.dict(self.staging, {'_write_custody_receipts': lambda *args: (_ for _ in ()).throw(OSError('full'))}):
            with self.assertLogs('custody-stage-test', level='WARNING'):
                self.poll()
        with self.assertRaises(ValueError):
            self.manager.import_receipt('f' * 16, token)

    def test_terminal_detection_does_not_wait_for_capacity_or_close_paused_jobs(self):
        engine = create_engine(f"sqlite:///{self.root / 'jobs.db'}")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine)
        namespace = {
            'file_custody_manager': self.manager, 'SessionLocal': sessions,
            'Job': Job, 'JobStatus': JobStatus,
        }
        finish = load_method('_scheduler/_coordinator.py', '_finish_custody_jobs', namespace)
        for status, allocation, finalization, closes in (
            (JobStatus.RUNNING, '42', None, False),
            (JobStatus.TERMINATED, '42', None, False),
            (JobStatus.PAUSED, '42', 'CANCELLED', False),
            (JobStatus.PAUSED, None, None, False),
            (JobStatus.SUCCESS, '42', 'COMPLETED', True),
            (JobStatus.FAILED, '42', 'TIMEOUT', True),
            (JobStatus.TERMINATED, '42', 'CANCELLED', True),
            (JobStatus.SUCCESS, None, None, True),
        ):
            with self.subTest(status=status, allocation=allocation, finalization=finalization):
                with sessions() as db:
                    db.merge(Job(id=JOB, status=status, slurm_job_id=allocation, finalization_status=finalization))
                    db.commit()
                token = self.manager.store_file('result', io.BytesIO(b'x'), job_id=JOB, expire_minutes=5)
                finish(SimpleNamespace())
                self.assertEqual(math.isfinite(self.manager.get_entry(token).expires_at), closes)
                self.manager.delete_entry(token)

    def test_corrupt_metadata_index_fails_before_orphan_cleanup(self):
        token = self.publish()
        self.poll()
        with self.manager._index.transaction() as db:
            db.execute('UPDATE entries SET metadata = ?', ('{"bad": true}',))
        with self.assertRaises(ValueError):
            self.manager_namespace['FileCustodyManager']()
        self.assertTrue((self.manager._storage_root / token / 'result').exists())


class BoundedReceiveTests(unittest.TestCase):
    def test_shutdown_after_stdout_eof_still_reaps_sender_promptly(self):
        namespace = load_transport()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'received'
            marker = Path(directory) / 'closed'
            cancel = threading.Event()
            command = [
                sys.executable, '-c',
                'import os,sys,time; os.write(1,b"x"); os.close(1); open(sys.argv[1],"w").close(); time.sleep(30)',
                str(marker),
            ]
            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(
                    namespace['receive_file'], command, str(destination), 1, 10,
                    namespace['_stop_transfer_process'], cancel,
                )
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                began = time.monotonic()
                cancel.set()
                with self.assertRaises(InterruptedError):
                    task.result(timeout=1)
                self.assertLess(time.monotonic() - began, 1)

    def test_bytes_are_exact_and_oversized_stream_cannot_overrun_reservation(self):
        namespace = load_transport()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'received'
            for size, expected in ((6, None), (5, ValueError), (7, ValueError)):
                with self.subTest(size=size):
                    def receive():
                        namespace['receive_file'](
                            [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"abcdef")'],
                            str(path), size, 5, namespace['_stop_transfer_process'],
                        )
                    if expected:
                        with self.assertRaises(expected):
                            receive()
                        self.assertLessEqual(path.stat().st_size, size)
                    else:
                        receive()
                        self.assertEqual(path.read_bytes(), b'abcdef')

    def test_scp_cancel_reaps_child_without_waiting_for_configured_budget(self):
        namespace = load_transport()
        event = threading.Event()
        event.set()
        began = time.monotonic()
        with self.assertRaises(InterruptedError):
            namespace['_run_scp'](
                [sys.executable, '-c', 'import time; time.sleep(30)'],
                timeout=900, cancel_event=event,
            )
        self.assertLess(time.monotonic() - began, 2)

    def test_timeout_and_shutdown_reap_the_sender(self):
        namespace = load_transport()
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), tempfile.TemporaryDirectory() as directory:
                event = threading.Event()
                if cancelled:
                    event.set()
                children = []
                original = subprocess.Popen
                def spawn(*args, **kwargs):
                    child = original(*args, **kwargs)
                    children.append(child)
                    return child
                began = time.monotonic()
                with patch.object(subprocess, 'Popen', spawn), self.assertRaises((TimeoutError, InterruptedError)):
                    namespace['receive_file'](
                        [sys.executable, '-c', 'import time; time.sleep(30)'],
                        str(Path(directory) / 'received'), 5, 0.1,
                        namespace['_stop_transfer_process'], event,
                    )
                self.assertLess(time.monotonic() - began, 2)
                self.assertIsNotNone(children[0].poll())


if __name__ == '__main__':
    unittest.main()
