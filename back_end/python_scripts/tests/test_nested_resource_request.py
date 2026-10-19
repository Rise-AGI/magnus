"""Run the proxy Blueprint without importing the SDK or submitting a Job."""
import __future__
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "blueprints" / "magnus-slurm.py"


class ProxyResourceRequestTests(unittest.TestCase):
    def setUp(
        self,
    ):
        self.submit = Mock()
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "blueprint"
        )
        namespace = {
            "submit_job": self.submit,
            "JobType": SimpleNamespace(A2="A2"),
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(SOURCE),
                "exec",
                flags=__future__.annotations.compiler_flag,
            ),
            namespace,
        )
        self.blueprint = namespace["blueprint"]

    def test_explicit_resources_reach_submission_with_both_call_styles(
        self,
    ):
        for positional in (False, True):
            with self.subTest(positional=positional):
                self.submit.reset_mock()
                if positional:
                    self.blueprint(
                        "fixture-user",
                        "/work",
                        "python solve.py",
                        8,
                        "32G",
                        2,
                    )
                else:
                    self.blueprint(
                        user_name="fixture-user",
                        working_directory="/work",
                        slurm_script="python solve.py",
                        cpu_count=8,
                        memory_demand="32G",
                        gpu_count=2,
                    )
                self.submit.assert_called_once()
                submitted = self.submit.call_args.kwargs
                self.assertEqual(submitted["cpu_count"], 8)
                self.assertEqual(submitted["memory_demand"], "32G")
                self.assertEqual(submitted["gpu_count"], 2)
                self.assertEqual(submitted["runner"], "fixture-user")

    def test_declared_defaults_reach_submission(
        self,
    ):
        self.blueprint(
            user_name="fixture-user",
            working_directory="/work",
            slurm_script="python solve.py",
        )
        self.submit.assert_called_once()
        submitted = self.submit.call_args.kwargs
        self.assertEqual(submitted["cpu_count"], 1)
        self.assertEqual(submitted["memory_demand"], "1600M")
        self.assertEqual(submitted["gpu_count"], 0)


if __name__ == "__main__":
    unittest.main()
