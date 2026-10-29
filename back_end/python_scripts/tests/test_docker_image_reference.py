"""Pulls and launches must address the same Docker image, including mirrors."""

import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import test_image_cache as image_fixtures
import test_runtime_recovery as submission_fixtures
from test_docker_release import docker


class DockerImageReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        self.images = self.enterContext(
            image_fixtures.image_pull_fixture(),
        )
        self.images.namespace["is_local_mode"] = True
        self.submission = submission_fixtures.DockerSubmissionTests()
        self.submission.setUp()
        self.addCleanup(
            self.submission.doCleanups,
        )
        self.submission.namespace["resource_manager"] = self.images.manager
        self.submission.actor.docker_manager = docker.DockerManager()
        self.available = set()
        self.pulled = []
        self.launched = []

        async def prepare(
            command,
            **kwargs,
        ):
            image = command[-1]
            if command[1:3] == ["image", "inspect"]:
                if image not in self.available:
                    raise image_fixtures.commands.ImageBuildError(
                        "image unavailable",
                    )
            elif command[1] == "pull":
                self.pulled.append(
                    image,
                )
                self.available.add(
                    image,
                )
            else:
                self.fail(
                    f"Unexpected preparation command: {command[:3]}",
                )
            return ""

        def launch(
            command,
            **kwargs,
        ):
            self.assertEqual(
                command[:3],
                ["docker", "run", "-d"],
            )
            image = command[-4]
            self.launched.append(
                image,
            )
            # Only the prepared reference exists; launching an upstream alias
            # would otherwise trigger another network pull inside docker run.
            if image not in self.available:
                raise subprocess.CalledProcessError(
                    125,
                    command,
                    stderr="unprepared image requires unavailable upstream",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                "container-id\n",
                "",
            )

        self.images.namespace["run_command"] = prepare
        self.enterContext(
            patch.object(
                docker.subprocess,
                "run",
                side_effect=launch,
            ),
        )

    async def assert_prepared_launch(
        self,
        requested: str,
        expected: str,
    ) -> None:
        self.submission.job.container_image = requested
        self.assertEqual(
            await self.images.manager.ensure_image(
                requested,
            ),
            (True, None),
        )
        self.assertTrue(
            self.submission.actor._submit_to_docker(
                self.submission.db,
                self.submission.job,
            ),
        )
        self.assertEqual(
            self.launched,
            [expected],
        )
        self.assertEqual(
            self.submission.job.container_image,
            requested,
        )

    async def test_mirror_pull_is_used_for_launch_without_upstream_access(
        self,
    ) -> None:
        self.images.namespace["_rewrite_image_for_mirror"] = (
            lambda image: "docker://mirror.invalid/library/application:tag"
        )
        expected = "mirror.invalid/library/application:tag"
        await self.assert_prepared_launch(
            "docker://application:tag",
            expected,
        )
        self.assertEqual(
            self.pulled,
            [expected],
        )

    async def test_cached_mirror_is_used_without_another_pull(
        self,
    ) -> None:
        expected = "mirror.invalid/library/application:tag"
        self.images.namespace["_rewrite_image_for_mirror"] = lambda image: "docker://" + expected
        self.available.add(
            expected,
        )
        await self.assert_prepared_launch(
            "docker://application:tag",
            expected,
        )
        self.assertEqual(
            self.pulled,
            [],
        )

    async def test_unmirrored_tag_keeps_its_runtime_reference(
        self,
    ) -> None:
        await self.assert_prepared_launch(
            "docker://example/application:tag",
            "example/application:tag",
        )

    async def test_unmirrored_digest_keeps_its_runtime_reference(
        self,
    ) -> None:
        expected = "registry.invalid/application@sha256:" + "a" * 64
        await self.assert_prepared_launch(
            expected,
            expected,
        )

    async def test_dockerfile_result_bypasses_legacy_mirror_resolution(
        self,
    ) -> None:
        expected = "magnus-build:resolved"
        self.submission.job.dockerfile = "FROM example/application:tag"
        self.submission.job.build_result = json.dumps(
            {"docker_image": expected},
        )
        self.images.manager.builds = Mock()
        self.images.manager.builds.is_available.return_value = True
        self.images.manager.get_docker_image = Mock(
            side_effect=AssertionError(
                "A built image must not be rewritten to a registry mirror",
            ),
        )
        self.available.add(
            expected,
        )
        self.assertTrue(
            self.submission.actor._submit_to_docker(
                self.submission.db,
                self.submission.job,
            ),
        )
        self.assertEqual(
            self.launched,
            [expected],
        )
        self.images.manager.get_docker_image.assert_not_called()


if __name__ == "__main__":
    unittest.main()
