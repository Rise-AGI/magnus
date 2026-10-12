"""Validate the actual build-configuration prefix without importing the server."""
from __future__ import annotations

import ast
import copy
import logging
from pathlib import Path
import tempfile
import typing
import unittest


SERVER = Path(__file__).resolve().parents[2] / "server"


def load_validator():
    namespace = {
        "Path": Path,
        "logger": logging.getLogger("image_build_configuration_test"),
        "Type": typing.Type,
        "Set": typing.Set,
        "Dict": typing.Dict,
        "Any": typing.Any,
    }
    size_tree = ast.parse((SERVER / "_size_utils.py").read_text(encoding="utf-8"))
    functions = [node for node in size_tree.body if getattr(node, "name", None) == "_parse_size_string"]
    tree = ast.parse((SERVER / "_magnus_config.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if getattr(node, "name", None) in {"_check_key", "_warn_extra_keys"}:
            functions.append(node)
        elif getattr(node, "name", None) == "_prepare_and_validate_magnus_config":
            prefix = copy.deepcopy(node)
            # Stop before transport/server validation; these have independent
            # requirements and may refer to credentials or deployment files.
            boundary = next(
                index for index, statement in enumerate(prefix.body)
                if isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr == "setdefault"
                and statement.value.args
                and isinstance(statement.value.args[0], ast.Constant)
                and statement.value.args[0].value == "transport"
            )
            prefix.body = prefix.body[:boundary]
            functions.append(prefix)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SERVER / "_magnus_config.py"), "exec"), namespace)
    return namespace["_prepare_and_validate_magnus_config"]


class BuildConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.validate = load_validator()

    def configuration(self, **settings):
        return {"server": {}, "execution": {"backend": "slurm", "image_build": {
            "enabled": True,
            "address": "unix:///builder.sock",
            **settings,
        }}}

    def test_old_configuration_remains_disabled(self):
        config = {"server": {}, "execution": {"backend": "local"}}
        self.validate(config)
        self.assertFalse(config["execution"]["image_build"]["enabled"])
        self.assertIsNone(config["execution"]["image_build"]["scratch_root"])

    def test_defaults_and_repeated_validation(self):
        config = self.configuration(cache_size="2G")
        self.validate(config)
        build = config["execution"]["image_build"]
        self.assertEqual(build["scratch_size_bytes"], 2 * 1024**3)
        self.assertEqual(build["conversion_memory_bytes"], 1024**3)
        self.assertEqual(build["conversion_cpus"], 2)
        expected = copy.deepcopy(config)
        self.validate(config)
        self.assertEqual(config, expected)

    def test_explicit_scratch_and_conversion_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.configuration(scratch_root=directory, scratch_size="128M", conversion_memory="32M", conversion_cpus=1)
            self.validate(config)
            build = config["execution"]["image_build"]
            self.assertEqual(build["scratch_root"], str(Path(directory).resolve()))
            self.assertEqual(build["scratch_size_bytes"], 128 * 1024**2)
            self.assertEqual(build["conversion_memory_bytes"], 32 * 1024**2)

    def test_invalid_paths_and_limits_fail_before_startup(self):
        for field, value in (
            ("scratch_root", ""), ("scratch_root", "relative/path"), ("scratch_root", 12),
            ("scratch_size", ""), ("scratch_size", "0M"), ("scratch_size", "-1G"), ("scratch_size", True),
            ("conversion_cpus", True), ("conversion_cpus", 0), ("conversion_cpus", 1.5),
            ("conversion_memory", "512K"), ("conversion_memory", "-1M"), ("conversion_memory", False),
        ):
            with self.subTest(field=field, value=value), self.assertRaises((TypeError, ValueError)):
                self.validate(self.configuration(**{field: value}))


if __name__ == "__main__":
    unittest.main()
