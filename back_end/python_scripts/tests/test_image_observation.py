"""A cache miss requires evidence; daemon and storage failures are unknown."""

import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


observations = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "server/_image_observation.py"),
)


class ImageObservationTests(unittest.TestCase):
    def test_docker_success_missing_and_failed_queries_are_distinct(self):
        reference = "mirror.invalid/image:tag"
        cases = (
            (0, "123\n", "", "cached", 123),
            (1, "", f"Error response from daemon: No such image: {reference}\n", "missing", None),
            (1, "", f"Error: No such image: {reference}\n", "missing", None),
            (1, "", "Cannot connect to the Docker daemon", "unknown", None),
            (1, "", "Error: No such image: another:tag", "unknown", None),
            (0, "-1", "", "unknown", None),
            (0, "broken output", "", "unknown", None),
        )
        for code, stdout, stderr, state, size in cases:
            with self.subTest(code=code, stdout=stdout, stderr=stderr):
                with patch.object(
                    subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], code, stdout, stderr),
                ) as command:
                    result = observations["inspect_docker_image"](reference)
                self.assertEqual((result.status, result.size_bytes), (state, size))
                self.assertEqual(command.call_args.kwargs["timeout"], 10)
                self.assertEqual(command.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_timeout_and_missing_runtime_are_safe_diagnostics(self):
        for error, code in (
            (subprocess.TimeoutExpired(["private"], 10, stderr="credential"), "timeout"),
            (FileNotFoundError("private executable"), "runtime_unavailable"),
        ):
            with self.subTest(code=code):
                with patch.object(subprocess, "run", side_effect=error):
                    result = observations["inspect_docker_image"]("example:tag")
                self.assertEqual(result.status, "unknown")
                self.assertEqual(result.error, code)
                self.assertNotIn("private", str(result))
                self.assertNotIn("credential", str(result))

    def test_sif_missing_is_distinct_from_permission_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.sif"
            self.assertEqual(observations["inspect_sif_image"](str(path)).status, "missing")
            path.write_bytes(b"sif data")
            self.assertEqual(observations["inspect_sif_image"](str(path)).size_bytes, 8)
            with patch("os.stat", side_effect=PermissionError("private path")):
                result = observations["inspect_sif_image"](str(path))
            self.assertEqual((result.status, result.error), ("unknown", "storage_unavailable"))


if __name__ == "__main__":
    unittest.main()
