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
    def test_launch_persists_identity_before_cli_and_has_a_deadline(self):
        events = []

        def execute(command, **kwargs):
            self.assertEqual(events, ["durable"])
            self.assertEqual(kwargs["timeout"], 60)
            label = command.index("--label")
            self.assertEqual(command[label + 1], "io.magnus.submission=attempt")
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")

        with patch.object(docker.subprocess, "run", side_effect=execute):
            result = docker.DockerManager().run_container(
                "job",
                "docker://image",
                "echo run",
                [],
                {},
                "/work",
                submission_identity="attempt",
                before_start=lambda: events.append("durable"),
            )
        self.assertEqual(result, "container-id")

    def test_launch_errors_distinguish_rejection_from_lost_reply_without_secrets(self):
        token = "test-secret-only"
        for error, expected in (
            (subprocess.CalledProcessError(125, [token], stderr="rejected " + token), docker.DockerSubmissionRejected),
            (FileNotFoundError(token), docker.DockerSubmissionRejected),
            (subprocess.CalledProcessError(-9, [token]), docker.DockerError),
            (subprocess.TimeoutExpired([token], 60), docker.DockerError),
        ):
            with self.subTest(error=type(error).__name__), patch.object(docker.subprocess, "run", side_effect=error):
                with self.assertRaises(docker.DockerError) as raised:
                    docker.DockerManager().run_container(
                        "job",
                        "image",
                        "run",
                        [],
                        {"MAGNUS_TOKEN": token},
                        "/work",
                    )
                self.assertIs(type(raised.exception), expected)
                self.assertNotIn(token, str(raised.exception))

    def test_recovery_requires_exact_label_and_distinguishes_absence_from_failure(self):
        for output, code, listing, expected in (
            ('{"io.magnus.submission":"attempt"}', 0, None, "OWNED"),
            ('{"io.magnus.submission":"another"}', 0, None, "CONFLICT"),
            ('null', 0, None, "CONFLICT"),
            ('invalid json', 0, None, "UNKNOWN"),
            ('', 1, subprocess.CompletedProcess([], 0, "other\n", ""), "MISSING"),
            ('', 1, subprocess.CompletedProcess([], 1, "", "daemon unavailable"), "UNKNOWN"),
        ):
            responses = [subprocess.CompletedProcess([], code, output, "")]
            if listing is not None:
                responses.append(listing)
            with self.subTest(expected=expected, output=output), patch.object(docker.subprocess, "run", side_effect=responses) as execute:
                self.assertEqual(docker.DockerManager().find_submission("job", "attempt"), expected)
                for invocation in execute.call_args_list:
                    self.assertEqual(invocation.kwargs["timeout"], 30)

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
