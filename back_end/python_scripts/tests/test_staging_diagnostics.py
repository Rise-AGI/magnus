"""Preparation evidence remains readable after failed workspace cleanup."""
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest

from test_job_outputs import JobOutputTests, JobStatus, SERVER


diagnostics = runpy.run_path(str(SERVER / "_staging_diagnostics.py"))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_failed_transfer_retains_safe_evidence_after_cleanup(self):
        workspace = self.root / "workspace/jobs/example"
        workspace.mkdir(parents=True)
        progress = diagnostics["StagingProgress"](str(self.root), "example")
        with self.assertRaises(TimeoutError):
            with progress.operation("custody_payload", size=8192, index=3, total=17, budget=900):
                raise TimeoutError("private-token /remote/private-path")
        shutil.rmtree(workspace)
        result = diagnostics["read_staging"](str(self.root), "example")
        self.assertEqual(result["phase"], "custody_payload")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["index"], 3)
        self.assertNotIn("private", json.dumps(result))
        self.assertLessEqual((self.root / "staging_diagnostics/example/progress.json").stat().st_size, 4096)
        self.assertIn("8192 bytes", diagnostics["staging_message"](result))

    def test_completed_operations_and_bytes_are_not_claimed_on_timeout(self):
        progress = diagnostics["StagingProgress"](str(self.root), "example")
        with progress.operation("wrapper", size=7):
            pass
        with self.assertRaises(TimeoutError):
            with progress.operation("repository"):
                raise TimeoutError()
        result = diagnostics["read_staging"](str(self.root), "example")
        self.assertEqual((result["completed_operations"], result["completed_bytes"]), (1, 7))

    def test_gc_uses_a_separate_retention_bound_and_rechecks_activity(self):
        for job in ("active", "became-active", "old", "new"):
            diagnostics["StagingProgress"](str(self.root), job).state("ready")
        diagnostics["prune_staging"](
            str(self.root), {"active"}, lambda job: job == "became-active", retained=1,
        )
        for job in ("active", "became-active", "new"):
            self.assertIsNotNone(diagnostics["read_staging"](str(self.root), job))
        self.assertIsNone(diagnostics["read_staging"](str(self.root), "old"))

    def test_restart_between_operations_is_reported_as_interrupted(self):
        progress = diagnostics["StagingProgress"](str(self.root), "example")
        with progress.operation("wrapper", size=7):
            pass
        restarted = runpy.run_path(str(SERVER / "_staging_diagnostics.py"))
        row = restarted["read_staging"](str(self.root), "example")
        self.assertEqual(row["state"], "interrupted")
        self.assertEqual(row["error"], "controller_restarted")
        progress.state("ready")
        self.assertEqual(restarted["read_staging"](str(self.root), "example")["state"], "ready")

    def test_unpublished_image_does_not_count_as_prepared_bytes(self):
        progress = diagnostics["StagingProgress"](str(self.root), "example")
        with progress.operation("image_upload", size=100, count_bytes=False):
            pass
        with self.assertRaises(RuntimeError):
            with progress.operation("image_publish", size=100):
                raise RuntimeError("publish failed")
        row = diagnostics["read_staging"](str(self.root), "example")
        self.assertEqual(row["phase"], "image_publish")
        self.assertEqual(row["completed_bytes"], 0)

    def test_reader_refuses_linked_evidence(self):
        folder = self.root / "staging_diagnostics"
        folder.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            diagnostics["read_staging"](str(self.root), "example")


class LogFallbackTests(JobOutputTests):
    def test_empty_execution_log_shows_preparation_without_mixing_real_output(self):
        self.set_job(JobStatus.FAILED)
        job_id = self.job_id if hasattr(self, "job_id") else "job"
        # Use the same pure module instance for the writer and route reader.
        self.namespace["read_staging"] = diagnostics["read_staging"]
        self.namespace["staging_message"] = diagnostics["staging_message"]
        progress = diagnostics["StagingProgress"](str(self.root), job_id)
        with self.assertRaises(TimeoutError):
            with progress.operation("repository"):
                raise TimeoutError()
        output = self.root / "workspace/jobs" / job_id / "slurm/output.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.touch()
        response = self.http.get(f"/api/jobs/{job_id}/logs", headers={"Authorization": "Bearer test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("repository", response.json()["logs"])
        output.write_text("actual scientific output\n")
        response = self.http.get(f"/api/jobs/{job_id}/logs", headers={"Authorization": "Bearer test"})
        self.assertEqual(response.json()["logs"], "actual scientific output\n")


if __name__ == "__main__":
    unittest.main()
