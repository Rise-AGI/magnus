"""Runtime failure is not evidence that a container has disappeared."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("isolated_docker_manager", Path(__file__).resolve().parents[2] / "server/_docker_manager.py")
docker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docker)


class DockerReleaseTests(unittest.TestCase):
    def test_failed_stop_and_remove_propagate_while_container_still_exists(self):
        for operation in ("stop_container", "remove_container"):
            with self.subTest(operation=operation):
                outcomes = [subprocess.CompletedProcess([], 1, "", "failure"), subprocess.CompletedProcess([], 0, "job\n", "")]
                with patch.object(docker.subprocess, "run", side_effect=outcomes), self.assertRaises(docker.DockerError):
                    getattr(docker.DockerManager(), operation)("job")

    def test_daemon_or_spawn_failure_is_never_confirmed_as_absence(self):
        for outcomes in (
            [subprocess.CompletedProcess([], 1, "", "failure")] * 2,
            [OSError("unavailable")],
        ):
            with self.subTest(outcomes=outcomes), patch.object(docker.subprocess, "run", side_effect=outcomes):
                with self.assertRaises(docker.DockerError):
                    docker.DockerManager().stop_container("job")

    def test_confirmed_absence_keeps_stop_and_remove_idempotent(self):
        for operation in ("stop_container", "remove_container"):
            with self.subTest(operation=operation):
                outcomes = [subprocess.CompletedProcess([], 1, "", "missing"), subprocess.CompletedProcess([], 0, "other-job\n", "")]
                with patch.object(docker.subprocess, "run", side_effect=outcomes):
                    getattr(docker.DockerManager(), operation)("job")

    def test_status_distinguishes_daemon_failure_from_confirmed_missing_container(self):
        for listing, expected in ((subprocess.CompletedProcess([], 1, "", "unavailable"), "UNKNOWN"), (subprocess.CompletedProcess([], 0, "", ""), "MISSING")):
            with self.subTest(expected=expected), patch.object(docker.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 1, "", "inspect failed"), listing]):
                self.assertEqual(docker.DockerManager().check_container_status("job"), expected)


if __name__ == "__main__":
    unittest.main()
