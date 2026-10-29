"""Local mount parsing must preserve paths without running user shell code."""

import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "isolated_bind_mounts",
    Path(__file__).resolve().parents[2] / "server/_scheduler/_bind_mounts.py",
)
BINDS = importlib.util.module_from_spec(
    SPEC,
)
SPEC.loader.exec_module(
    BINDS,
)


class DockerBindTests(unittest.TestCase):
    def test_parentheses_and_spaces_inside_paths_do_not_end_the_array(self):
        self.assertEqual(
            BINDS.docker_bind_mounts(
                'mounts=("/data/Experiment (2026):/input (fixed):ro" "cache:/cache:rw")',
                host_home="/home/fixture",
            ),
            ["/data/Experiment (2026):/input (fixed):ro", "cache:/cache:rw"],
        )

    def test_explicit_and_expanded_windows_sources_use_the_same_mapping(self):
        for source in (r"C:\Users\Fixture", "C:/Users/Fixture", "$HOME", "${HOME}"):
            with self.subTest(
                source=source,
            ):
                self.assertEqual(
                    BINDS.docker_bind_mounts(
                        f'mounts=("{source}:/root/input:ro")',
                        host_home=r"C:\Users\Fixture",
                    ),
                    ["/c/Users/Fixture:/root/input:ro"],
                )

    def test_home_expansion_respects_variable_boundaries_and_container_home(self):
        self.assertEqual(
            BINDS.docker_bind_mounts(
                'mounts=("$HOME/a:${HOME}/a" "$HOMELESS:/other" "$HOME_2:/other2")',
                host_home="/home/fixture",
            ),
            ["/home/fixture/a:/root/a", "$HOMELESS:/other", "$HOME_2:/other2"],
        )

    def test_read_write_flags_order_and_duplicate_targets_remain_literal(self):
        self.assertEqual(
            BINDS.docker_bind_mounts(
                'mounts=(\n"/first:/data:ro"\n"/second:/data:rw"\n"/third:/data/child:ro"\n)',
                host_home="/home/fixture",
            ),
            ["/first:/data:ro", "/second:/data:rw", "/third:/data/child:ro"],
        )

    def test_only_the_existing_literal_double_quoted_array_contract_is_interpreted(self):
        for command in (
            "",
            'export APPTAINER_BIND="/data:/input"',
            'other_mounts=("/data:/input")',
            "mounts=('/data:/input')",
            'mounts=("/data-without-destination")',
        ):
            with self.subTest(
                command=command,
            ):
                self.assertEqual(
                    BINDS.docker_bind_mounts(
                        command,
                        host_home="/home/fixture",
                    ),
                    [],
                )

    def test_dynamic_shell_expressions_are_not_evaluated(self):
        self.assertEqual(
            BINDS.docker_bind_mounts(
                'mounts=("$(printf /data):/input" "$PWD:/current")',
                host_home="/home/fixture",
            ),
            ["$(printf /data):/input", "$PWD:/current"],
        )

    def test_unclosed_array_does_not_publish_partial_mounts(self):
        for command in (
            'mounts=("/valid:/input"',
            'mounts=("/unclosed:/input)',
            "mounts=(" * 131072,
        ):
            self.assertEqual(
                BINDS.docker_bind_mounts(
                    command,
                    host_home="/home/fixture",
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
