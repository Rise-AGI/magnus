"""Exercise the real custody command without loading user SDK configuration."""
import ast
from contextlib import nullcontext
import importlib
from pathlib import Path
import sys
import tempfile
import types
from typing import Optional
import unittest
from unittest.mock import Mock

import typer
from typer.testing import CliRunner


PACKAGE = "_magnus_custody_cli_tests"
package = types.ModuleType(PACKAGE)
SOURCE = Path(__file__).resolve().parents[1] / "src" / "magnus"
package.__path__ = [str(SOURCE)]
sys.modules[PACKAGE] = package
shared = importlib.import_module(f"{PACKAGE}.cli._shared")
exceptions = importlib.import_module(f"{PACKAGE}.exceptions")


class CustodyCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "result.bin"
        self.path.write_bytes(b"TEST")
        self.secret = "magnus-secret:7919-calm-boat-fire"
        self.upload = Mock(return_value=self.secret)
        self.spinner = Mock(side_effect=lambda message: nullcontext())
        self.app = typer.Typer()
        self.app.callback()(lambda: None)
        namespace = {
            "app": self.app,
            "typer": typer,
            "Path": Path,
            "Optional": Optional,
            "nullcontext": nullcontext,
            "SignalSafeSpinner": self.spinner,
            "api_custody_file": self.upload,
            "MagnusError": exceptions.MagnusError,
            "console": shared.console,
            "print_msg": shared.print_msg,
            "print_error": shared.print_error,
        }
        source_file = SOURCE / "cli" / "commands.py"
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "custody_cmd")
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source_file), "exec"), namespace)
        self.runner = CliRunner()

    def test_secret_only_stdout_is_exact_and_has_no_progress(self):
        result = self.runner.invoke(self.app, ["custody", str(self.path), "--secret-only"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout, self.secret + "\n")
        self.assertEqual(result.stderr, "")
        self.spinner.assert_not_called()

    def test_default_output_remains_interactive(self):
        result = self.runner.invoke(self.app, ["custody", str(self.path)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn(self.secret, result.stderr)
        self.spinner.assert_called_once()

    def test_failure_has_no_stdout_secret_and_nonzero_exit(self):
        self.upload.side_effect = exceptions.MagnusError("HTTP 503: storage full")
        result = self.runner.invoke(self.app, ["custody", str(self.path), "--secret-only"])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("HTTP 503", result.stderr)

    def test_missing_secret_cannot_report_success(self):
        self.upload.return_value = ""
        result = self.runner.invoke(self.app, ["custody", str(self.path), "--secret-only"])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("did not return a file secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
