"""Transport selection and validation without importing a live server."""

import runpy
import unittest
from pathlib import Path

policy = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "server/_repository_transport.py")
)


class RepositoryTransportTests(unittest.TestCase):
    def test_defaults_preserve_backend_contract(self):
        url = policy["github_repository_url"]
        self.assertEqual(
            url("owner", "repo", local=False), "git@github.com:owner/repo.git"
        )
        self.assertEqual(
            url("owner", "repo", local=True), "https://github.com/owner/repo.git"
        )

    def test_explicit_proxy_applies_to_both_backends(self):
        for local in (False, True):
            self.assertEqual(
                policy["github_repository_url"](
                    "owner", "repo", local=local, https_url="https://proxy.example/"
                ),
                "https://proxy.example/owner/repo.git",
            )

    def test_rejects_credentials_and_non_origins(self):
        for value in (
            "http://proxy.example",
            "https://user:secret@proxy.example",
            "https://proxy.example/path",
            "https://proxy.example?secret=1",
            "https://proxy.example#x",
            "https://proxy.example:bad",
            "https://proxy.example\n",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy["validate_github_https_url"](value)
        with self.assertRaises(TypeError):
            policy["validate_github_https_url"](42)

    def test_valid_origins(self):
        for value in (
            None,
            "https://github.com",
            "https://proxy.example:8443/",
            "https://[::1]:8443",
        ):
            policy["validate_github_https_url"](value)
