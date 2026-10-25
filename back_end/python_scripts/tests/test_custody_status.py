"""Real HTTP/SQLite/custody behavior without importing production singletons."""
import ast
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_file_custody_lifecycle import load_manager, PausedReader, SERVER
from test_job_finalization import Base, Job, JobStatus


class CustodyStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.namespace = load_manager()
        self.config = {"server": {
            "root": str(self.root),
            "file_custody": {
                "max_size": "1M", "max_file_size": None, "max_processes": 10,
                "default_ttl_minutes": 60, "max_ttl_minutes": 60,
            },
        }}
        self.namespace["magnus_config"] = self.config
        self.manager = self.namespace["FileCustodyManager"]()
        self.engine = create_engine(f"sqlite:///{self.root / 'test.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.token = "relay-" + "a" * 32
        self.job_id = "producer"
        self.set_job(JobStatus.SUCCESS, "COMPLETED", "42")
        receipts = runpy.run_path(str(SERVER / "_custody_receipts.py"))
        self.write_receipts = receipts["_write_custody_receipts"]

        def get_db():
            with self.sessions() as db:
                yield db

        def get_user(authorization: str = Header(default="")):
            if authorization != "Bearer test":
                raise HTTPException(status_code=401)
            return object()

        namespace = {
            **receipts,
            "__name__": __name__,
            "database": SimpleNamespace(get_db=get_db),
            "models": SimpleNamespace(Job=Job, JobStatus=JobStatus, User=object),
            "get_current_user": get_user,
            "magnus_config": self.config,
            "file_custody_manager": self.manager,
        }
        for name in ("CustodyLimitError", "CustodyStorageFullError", "FileTooLargeError", "FILE_SECRET_PREFIX"):
            namespace[name] = self.namespace[name]
        source = SERVER / "routers" / "files.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not node.level]
        exec(compile(tree, str(source), "exec"), namespace)
        self.router_namespace = namespace
        app = FastAPI()
        app.include_router(namespace["router"], prefix="/api")
        self.http = TestClient(app)
        self.addCleanup(self.http.close)

    def set_job(self, status, finalization, allocation):
        with self.sessions() as db:
            job = db.get(Job, self.job_id)
            if job is None:
                job = Job(id=self.job_id)
                db.add(job)
            job.status = status
            job.finalization_status = finalization
            job.slurm_job_id = allocation
            db.commit()

    def query(self, job_id="producer"):
        return self.http.get(
            f"/api/files/status/{self.token}",
            params = {"job_id": job_id} if job_id is not None else {},
            headers = {"Authorization": "Bearer test"},
        )

    def store(self, **kwargs):
        return self.manager.store_file(
            "answer.bin",
            io.BytesIO(b"answer"),
            explicit_token = self.token,
            **kwargs,
        )

    def register_receipt(self):
        self.write_receipts(
            str(self.root / "workspace" / "jobs" / self.job_id),
            {"entries": {self.token: "d" * 64}, "logs_complete": True},
        )

    def test_success_before_import_is_unknown_with_pending_job_context(self):
        response = self.query()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "unknown", "job_transfers_pending": True})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.query(None).json(), {"status": "unknown", "job_transfers_pending": None})

    def test_queries_do_not_spend_downloads_or_renew_ttl(self):
        self.store(max_downloads=1)
        entry = self.manager.get_entry(self.token)
        expiry = entry.expires_at
        for _ in range(3):
            self.assertEqual(self.query().json()["status"], "ready")
        self.assertEqual(entry.download_count, 0)
        self.assertEqual(entry.active_readers, 0)
        self.assertEqual(entry.expires_at, expiry)
        response = self.http.get(f"/api/files/download/{self.token}")
        self.assertEqual(response.content, b"answer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        response = self.http.get(f"/api/files/download/{self.token}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "File not found or expired"})

    def test_consumed_receipt_does_not_wait_for_other_imports(self):
        self.store(max_downloads=1)
        self.register_receipt()
        self.http.get(f"/api/files/download/{self.token}")
        self.assertEqual(self.query().json(), {"status": "unavailable", "job_transfers_pending": True})
        self.assertEqual(self.manager._entries, {})

    def test_restart_does_not_turn_imported_receipt_into_pending(self):
        self.store()
        self.register_receipt()
        restarted = self.namespace["FileCustodyManager"]()
        self.router_namespace["file_custody_manager"] = restarted
        self.assertEqual(self.query().json()["status"], "unavailable")
        self.assertEqual(restarted._entries, {})

    def test_exhausted_active_download_and_expired_entry_are_unavailable(self):
        self.store(max_downloads=1)
        self.manager.get_file_path(self.token)
        self.assertEqual(self.query().json()["status"], "unavailable")
        self.manager.release_file(self.token)
        self.store()
        self.manager._entries[self.token].expires_at = 1
        self.assertEqual(self.query().json()["status"], "unavailable")

    def test_upload_publication_is_pending_until_complete(self):
        reader = PausedReader(b"answer")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self.manager.store_file,
                "answer.bin",
                reader,
                explicit_token = self.token,
            )
            try:
                self.assertTrue(reader.paused.wait(timeout=3))
                self.assertEqual(self.query().json()["status"], "pending")
            finally:
                reader.resume.set()
            future.result(timeout=3)
        self.assertEqual(self.query().json()["status"], "ready")

    def test_terminal_cleanup_and_local_completion_do_not_wait(self):
        for finalization, allocation in (("CLEANUP", "42"), (None, None)):
            with self.subTest(finalization=finalization):
                self.set_job(JobStatus.SUCCESS, finalization, allocation)
                self.assertEqual(self.query().json(), {"status": "unavailable", "job_transfers_pending": False})

    def test_active_and_recovering_failures_may_still_publish(self):
        for status, finalization, allocation in (
            (JobStatus.RUNNING, None, "42"),
            (JobStatus.PAUSED, None, None),
            (JobStatus.FAILED, "TIMEOUT", "42"),
            (JobStatus.TERMINATED, None, "42"),
        ):
            with self.subTest(status=status):
                self.set_job(status, finalization, allocation)
                self.assertIs(self.query().json()["job_transfers_pending"], True)

    def test_auth_and_invalid_job_fail_without_consuming_ready_file(self):
        self.store(max_downloads=1)
        self.assertEqual(self.http.get(f"/api/files/status/{self.token}").status_code, 401)
        self.assertEqual(self.query("missing").status_code, 404)
        self.assertEqual(self.manager.get_entry(self.token).download_count, 0)

    def test_corrupt_receipts_report_service_failure_not_expiry(self):
        self.register_receipt()
        path = self.root / "workspace" / "jobs" / self.job_id / ".magnus_custody_receipts.json"
        path.write_text("broken", encoding="utf-8")
        self.assertEqual(self.query().status_code, 503)
        self.store()
        self.assertEqual(self.query().json()["status"], "ready")


if __name__ == "__main__":
    unittest.main()
