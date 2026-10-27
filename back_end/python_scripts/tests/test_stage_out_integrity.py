"""Remote artifact failures must remain retryable and never trigger cleanup."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_custody_stage_out import FileTransport, load_staging
from test_file_custody_lifecycle import load_manager


class ArtifactTransport(FileTransport):
    def __init__(self):
        super().__init__()
        self.query_failure = False

    def run(self, command, **kwargs):
        if self.query_failure:
            raise OSError('connection lost')
        return super().run(command, **kwargs)


class StageOutTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_staging()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.namespace["magnus_workspace_path"] = str(self.root)
        (self.root / "jobs").mkdir()
        self.transport = ArtifactTransport()
        self.scheduler = self.namespace["_StagingMixin"]()
        self.scheduler.slurm_manager = SimpleNamespace(transport=self.transport, check_job_status=lambda identifier: "COMPLETED", observe_job=lambda identifier: SimpleNamespace(allocation_released=True))
        self.scheduler._remote_job_working_table = lambda job: str(self.root / "remote" / job)
        self.scheduler._local_job_working_table = lambda job: str(self.root / "jobs" / job)
        manager_namespace = load_manager()
        manager_namespace['magnus_config'] = {'server': {
            'root': str(self.root / 'custody'),
            'file_custody': {
                'max_size': '64M', 'max_file_size': None, 'max_processes': 20,
                'default_ttl_minutes': 60, 'max_ttl_minutes': 60,
            },
        }}
        self.custody = manager_namespace['FileCustodyManager']()
        self.relay = self.namespace['CustodyRelay'](
            transport = self.transport,
            custody = self.custody,
            local_table = self.scheduler._local_job_working_table,
            remote_table = self.scheduler._remote_job_working_table,
            validate_metadata = self.namespace['_validate_custody_metadata'],
            logger = self.namespace['logger'],
            transfer_timeout = 30,
            transfer_logs = self.scheduler._stage_out_logs,
        )
        self.scheduler._custody_relay = self.relay

    def test_optional_absence_succeeds_but_transport_failure_stays_pending(self):
        self.assertFalse(self.scheduler._stage_out_final("fedcba9876543210"))
        self.relay.transfer_bulk('fedcba9876543210')
        self.assertTrue(self.scheduler._stage_out_final("fedcba9876543210"))
        self.transport.query_failure = True
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.assertFalse(self.scheduler._stage_out_final("fedcba9876543210"))

    def test_partial_fetch_keeps_previous_log_and_remote_original(self):
        remote = Path(self.scheduler._remote_job_working_table("0123456789abcdef")) / "slurm" / "output.txt"
        local = Path(self.scheduler._local_job_working_table("0123456789abcdef")) / "slurm" / "output.txt"
        remote.parent.mkdir(parents=True)
        local.parent.mkdir(parents=True)
        remote.write_text("complete log")
        local.write_text("previous log")
        self.transport.failed_path = remote
        self.assertFalse(self.scheduler._stage_out_final("0123456789abcdef"))
        with self.assertLogs("custody-stage-test", level="WARNING"):
            self.relay.transfer_bulk('0123456789abcdef')
        self.assertFalse(self.scheduler._stage_out_final("0123456789abcdef"))
        self.assertEqual(local.read_text(), "previous log")
        self.assertEqual(remote.read_text(), "complete log")
        self.assertEqual(list(local.parent.iterdir()), [local])
        self.transport.failed_path = None
        self.relay._state('0123456789abcdef').logs_retry_after = 0
        self.relay.transfer_bulk('0123456789abcdef')
        self.assertTrue(self.scheduler._stage_out_final("0123456789abcdef"))
        self.assertEqual(local.read_text(), "complete log")

    def test_custody_registration_failure_is_retryable_without_nested_copy(self):
        token = "relay-" + "a" * 32
        entry = Path(self.scheduler._remote_job_working_table("0123456789abcdef")) / ".magnus_custody_drop" / token
        entry.mkdir(parents=True)
        (entry / "payload").write_bytes(b"artifact")
        (entry / "meta.json").write_text(json.dumps({"token": token, "filename": "payload"}))
        with patch.object(self.custody, 'store_file', side_effect=RuntimeError('custody temporarily full')):
            with self.assertLogs("custody-stage-test", level="WARNING"):
                self.assertFalse(self.scheduler._stage_out_final("0123456789abcdef"))
        self.relay.transfer_bulk('0123456789abcdef')
        self.relay._state('0123456789abcdef').pending[token].retry_after = 0
        self.assertTrue(self.scheduler._stage_out_final("0123456789abcdef"))
        received = self.custody.get_file_path(token)
        self.assertEqual(received[0].read_bytes(), b"artifact")
        self.custody.release_file(token)
        self.assertTrue(entry.exists())
        self.assertFalse((Path(self.scheduler._local_job_working_table('0123456789abcdef')) / '.magnus_custody_drop').exists())



if __name__ == "__main__":
    unittest.main()
