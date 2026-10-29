"""Local Git, controlled child processes and isolated repository preparation."""

import ast
import asyncio
import importlib.util
import logging
import os
import re
import runpy
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SERVER = Path(__file__).resolve().parents[2] / "server"
RESOURCE = SERVER / "_resource_manager"
spec = importlib.util.spec_from_file_location(
    "isolated_git_process",
    RESOURCE / "_git_process.py",
)
git = importlib.util.module_from_spec(
    spec,
)
spec.loader.exec_module(
    git,
)


def load_repos(
    cache_root: Path,
) -> dict:
    disk = runpy.run_path(
        str(
            SERVER.parent / "library/fundamental/disk_tools.py",
        ),
    )
    namespace = {
        "__name__": "isolated_git_repos",
        "logger": logging.getLogger(
            "isolated_git_repos",
        ),
        "magnus_repo_cache_path": str(
            cache_root,
        ),
        "is_local_mode": True,
        "_run_git": git._run_git,
        "GitOperationError": git.GitOperationError,
        "describe_git_failure": git.describe_git_failure,
        "disk_full_message": disk["disk_full_message"],
        "is_disk_full_stderr": disk["is_disk_full_stderr"],
    }
    namespace.update(
        runpy.run_path(
            str(
                SERVER / "_blocking_io.py",
            ),
        ),
    )
    constants = {
        "DEFAULT_BRANCH_CACHE_TTL_SECONDS", "REPO_FRESHNESS_TTL_SECONDS",
        "GIT_FETCH_MAX_RETRIES", "GIT_FETCH_TIMEOUT_SECONDS", "GIT_CLONE_MAX_RETRIES",
        "GIT_LOCAL_QUERY_TIMEOUT_SECONDS", "CACHE_FETCH_TIMESTAMP_FILENAME", "FULL_SHA_PATTERN",
    }
    tree = ast.parse(
        (RESOURCE / "_config.py").read_text(),
    )
    tree.body = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets)
    ]
    namespace["re"] = re
    exec(
        compile(
            tree,
            str(RESOURCE / "_config.py"),
            "exec",
        ),
        namespace,
    )
    for filename in ("_helpers.py", "_repos.py"):
        tree = ast.parse(
            (RESOURCE / filename).read_text(),
        )
        tree.body = [
            node for node in tree.body
            if not isinstance(node, ast.ImportFrom) or not (node.level or node.module == "library")
        ]
        exec(
            compile(
                tree,
                str(RESOURCE / filename),
                "exec",
            ),
            namespace,
        )
    return namespace


class DefaultBranchCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(
        self,
    ) -> None:
        self.namespace = load_repos(
            Path("unused-cache"),
        )
        self.clock = SimpleNamespace(
            wall=1000.0,
            elapsed=100.0,
        )
        self.namespace["time"] = SimpleNamespace(
            time=lambda: self.clock.wall,
            monotonic=lambda: self.clock.elapsed,
        )
        self.manager = self.namespace["_ReposMixin"]()
        self.manager._default_branch_cache = {}
        self.manager._default_branch_locks = {}
        self.manager._resolve_default_branch = AsyncMock(
            side_effect=["original", "renamed"],
        )

    async def test_clock_rollback_does_not_extend_the_cache_lifetime(
        self,
    ) -> None:
        await self.manager._resolve_default_branch_cached(
            "fixture",
        )
        self.clock.wall -= 3600
        self.clock.elapsed += self.namespace["DEFAULT_BRANCH_CACHE_TTL_SECONDS"]
        self.assertEqual(
            await self.manager._resolve_default_branch_cached(
                "fixture",
            ),
            "renamed",
        )
        self.assertEqual(
            self.manager._resolve_default_branch.await_count,
            2,
        )

    async def test_clock_jump_does_not_expire_a_fresh_cache(
        self,
    ) -> None:
        await self.manager._resolve_default_branch_cached(
            "fixture",
        )
        self.clock.wall += 3600
        self.clock.elapsed += 1
        self.assertEqual(
            await self.manager._resolve_default_branch_cached(
                "fixture",
            ),
            "original",
        )
        self.assertEqual(
            self.manager._resolve_default_branch.await_count,
            1,
        )

    async def test_waiting_requests_share_the_refreshed_branch(
        self,
    ) -> None:
        await self.manager._resolve_default_branch_cached(
            "fixture",
        )
        self.clock.elapsed += self.namespace["DEFAULT_BRANCH_CACHE_TTL_SECONDS"]
        self.clock.wall += self.namespace["DEFAULT_BRANCH_CACHE_TTL_SECONDS"]
        entered, finish = asyncio.Event(), asyncio.Event()

        async def resolve(
            uri,
        ):
            entered.set()
            await finish.wait()
            return "renamed"

        query = AsyncMock(
            side_effect=resolve,
        )
        self.manager._resolve_default_branch = query
        tasks = [
            asyncio.create_task(
                self.manager._resolve_default_branch_cached(
                    "fixture",
                ),
            )
            for _ in range(5)
        ]
        try:
            await asyncio.wait_for(
                entered.wait(),
                2,
            )
            self.clock.wall -= 3600
        finally:
            finish.set()
            branches = await asyncio.gather(
                *tasks,
            )
        self.assertEqual(
            branches,
            ["renamed"] * 5,
        )
        self.assertEqual(
            query.await_count,
            1,
        )


class GitProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(
            temporary.cleanup,
        )
        self.root = Path(
            temporary.name,
        )
        self.real_spawn = asyncio.create_subprocess_exec
        self.processes = []

    def python_child(
        self,
        script: str,
    ):
        async def spawn(
            *arguments,
            **keywords,
        ):
            process = await self.real_spawn(
                sys.executable,
                "-c",
                script,
                **keywords,
            )
            self.processes.append(
                process,
            )
            return process
        return patch.object(
            git.asyncio,
            "create_subprocess_exec",
            side_effect=spawn,
        )

    async def test_stderr_is_bounded_and_stdout_remains_complete(
        self,
    ) -> None:
        with self.python_child(
            "import sys; sys.stderr.buffer.write(b'x' * (5 * 1024 * 1024) + b'\\xffEND'); "
            "sys.stdout.buffer.write(b'y' * (128 * 1024))",
        ):
            code, stdout, stderr = await git._run_git(
                "fixture",
                timeout=5,
            )
        self.assertEqual(
            (code, stdout),
            (0, b"y" * (128 * 1024)),
        )
        self.assertEqual(
            stderr,
            b"x" * (git.GIT_STDERR_LIMIT - 4) + b"\xffEND",
        )
        with self.python_child(
            "import sys; print('ignored'); print('ignored', file=sys.stderr)",
        ):
            self.assertEqual(
                await git._run_git(
                    "fixture",
                    capture_stdout=False,
                    capture_stderr=False,
                ),
                (0, b"", b""),
            )

    async def test_timeout_reaps_process_and_preserves_timeout_type(
        self,
    ) -> None:
        with self.python_child(
            "import time; time.sleep(60)",
        ):
            with self.assertRaises(
                TimeoutError,
            ):
                await git._run_git(
                    "fixture",
                    timeout=0.05,
                )
        self.assertIsNotNone(
            self.processes[0].returncode,
        )

    async def test_early_disk_failure_survives_tail_truncation_and_split_reads(
        self,
    ) -> None:
        stream = asyncio.StreamReader()
        task = asyncio.create_task(
            git._read_stderr_tail(
                stream,
            ),
        )
        stream.feed_data(
            b"private-response: No space left on ",
        )
        await asyncio.sleep(
            0,
        )
        stream.feed_data(
            b"device\n" + b"x" * (git.GIT_STDERR_LIMIT * 2),
        )
        stream.feed_eof()
        stderr = await task
        self.assertEqual(
            len(
                stderr,
            ),
            git.GIT_STDERR_LIMIT,
        )
        self.assertIn(
            b"no space left on device",
            stderr,
        )
        self.assertNotIn(
            b"private-response",
            stderr,
        )

    async def test_cancellation_during_spawn_joins_cleanup_despite_recancellation(
        self,
    ) -> None:
        spawned = asyncio.Event()
        release = asyncio.Event()

        async def spawn(
            *arguments,
            **keywords,
        ):
            process = await self.real_spawn(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                **keywords,
            )
            self.processes.append(
                process,
            )
            spawned.set()
            await release.wait()
            return process

        with patch.object(
            git.asyncio,
            "create_subprocess_exec",
            side_effect=spawn,
        ):
            task = asyncio.create_task(
                git._run_git(
                    "fixture",
                ),
            )
            await asyncio.wait_for(
                spawned.wait(),
                timeout=5,
            )
            task.cancel()
            await asyncio.sleep(
                0,
            )
            task.cancel()
            self.assertFalse(
                task.done(),
            )
            release.set()
            with self.assertRaises(
                asyncio.CancelledError,
            ):
                await asyncio.wait_for(
                    task,
                    timeout=5,
                )
        self.assertIsNotNone(
            self.processes[0].returncode,
        )

    @unittest.skipUnless(
        os.name == "posix",
        "POSIX helper process group",
    )
    async def test_timeout_stops_helper_holding_pipes_after_parent_exit(
        self,
    ) -> None:
        heartbeat = self.root / "helper-writes"
        helper = (
            "import time; "
            f"f = open({str(heartbeat)!r}, 'ab', buffering=0); "
            "exec('while True:\\n f.write(b\"x\"); time.sleep(0.01)')"
        )
        script = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {helper!r}])"
        with self.python_child(
            script,
        ):
            with self.assertRaises(
                TimeoutError,
            ):
                await git._run_git(
                    "fixture",
                    timeout=0.3,
                )
        size = heartbeat.stat().st_size
        await asyncio.sleep(
            0.05,
        )
        self.assertGreater(
            size,
            0,
        )
        self.assertEqual(
            heartbeat.stat().st_size,
            size,
        )

    async def test_spawn_failure_does_not_echo_command_or_filename(
        self,
    ) -> None:
        with patch.object(
            git.asyncio,
            "create_subprocess_exec",
            side_effect=FileNotFoundError(2, "synthetic-secret", "private-path"),
        ):
            with self.assertRaisesRegex(
                git.GitOperationError,
                r"^Git process could not run \(OS error 2\)$",
            ):
                await git._run_git(
                    "fixture",
                )


@unittest.skipUnless(
    shutil.which(
        "git",
    ),
    "Git executable required",
)
class RepoPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(
            temporary.cleanup,
        )
        self.root = Path(
            temporary.name,
        )
        self.cache = self.root / "cache"
        self.namespace = load_repos(
            self.cache,
        )
        self.manager = self.namespace["_ReposMixin"]()
        self.manager._default_branch_cache = {}
        self.manager._default_branch_locks = {}
        self.manager.repo_locks = {}
        self.manager._get_repo_cache_path = lambda *args: str(self.cache)
        self.manager._evict_lru_repos = AsyncMock()
        self.source = self.root / "source"
        self.source.mkdir()
        self.enterContext(
            patch.dict(
                git._git_env,
                {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
            ),
        )
        await self.command(
            "init",
            "--initial-branch=trunk",
        )
        await self.command(
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "first",
        )
        self.sha = (await self.command("rev-parse", "HEAD")).decode().strip()

        async def local_git(
            *arguments,
            **keywords,
        ):
            return await git._run_git(
                *(str(self.source) if arg == "https://github.com/example/repo.git" else arg
                  for arg in arguments),
                **keywords,
            )
        self.namespace["_run_git"] = local_git

    async def command(
        self,
        *arguments: str,
    ) -> bytes:
        code, stdout, stderr = await git._run_git(
            *arguments,
            cwd=str(
                self.source,
            ),
        )
        self.assertEqual(
            code,
            0,
            stderr,
        )
        return stdout

    async def prepare(
        self,
        name: str = "job",
        branch: str | None = None,
        commit: str = "HEAD",
    ):
        return await self.manager.ensure_repo(
            namespace="example",
            repo_name="repo",
            branch=branch,
            commit_sha=commit,
            target_dir=str(
                self.root / name,
            ),
            runner="unused",
            job_working_dir=str(
                self.root,
            ),
        )

    async def test_head_is_pinned_and_full_sha_reuses_cache_without_fetch(
        self,
    ) -> None:
        self.assertEqual(
            await self.prepare(),
            (True, self.sha, "trunk"),
        )
        fetch = AsyncMock(
            side_effect=AssertionError("immutable commit must not fetch"),
        )
        with patch.object(
            self.manager,
            "_fetch_in_cache",
            fetch,
        ):
            self.assertEqual(
                await self.prepare(
                    name="second-job",
                    branch="trunk",
                    commit=self.sha,
                ),
                (True, self.sha, "trunk"),
            )

    async def test_expired_head_fails_closed_and_keeps_fetch_reason(
        self,
    ) -> None:
        await self.prepare()
        timestamp = Path(
            self.manager._cache_fetch_ts_path(
                str(
                    self.cache,
                ),
            ),
        )
        timestamp.write_text(
            "0",
        )
        original = self.namespace["_run_git"]

        async def fail_fetch(
            *arguments,
            **keywords,
        ):
            if arguments[0] == "fetch":
                return 128, b"", b"fatal: Could not resolve host: synthetic-secret"
            return await original(
                *arguments,
                **keywords,
            )

        with patch.dict(
            self.namespace,
            {"_run_git": fail_fetch, "GIT_FETCH_MAX_RETRIES": 1},
        ):
            success, reason, branch = await self.prepare(
                name="second-job",
                branch="trunk",
            )
        self.assertFalse(
            success,
        )
        self.assertIn(
            "host name resolution failed",
            reason,
        )
        self.assertIn(
            "cannot guarantee freshness",
            reason,
        )
        self.assertNotIn(
            "synthetic-secret",
            reason,
        )
        self.assertIsNone(
            branch,
        )
        self.assertFalse(
            (self.root / "second-job").exists(),
        )
        self.assertEqual(
            timestamp.read_text(),
            "0",
        )

    async def test_default_branch_failure_reaches_job_without_raw_credentials(
        self,
    ) -> None:
        failed = AsyncMock(
            return_value=(128, b"", b"\xff fatal: https://user:synthetic-secret@host: Forbidden"),
        )
        with patch.dict(
            self.namespace,
            {"_run_git": failed},
        ), patch.object(
            asyncio,
            "sleep",
            new_callable=AsyncMock,
        ), self.assertLogs(
            "isolated_git_repos",
            level="WARNING",
        ) as logs:
            success, reason, _ = await self.prepare()
        self.assertFalse(
            success,
        )
        self.assertEqual(
            failed.await_count,
            3,
        )
        self.assertIn(
            "Failed to determine default branch: git ls-remote failed (exit 128)",
            reason,
        )
        self.assertIn(
            "HTTP 403",
            reason,
        )
        self.assertNotIn(
            "synthetic-secret",
            reason + " ".join(logs.output),
        )
        self.assertEqual(
            self.manager._default_branch_cache,
            {},
        )

    async def test_invalid_freshness_timestamp_requires_successful_fetch(
        self,
    ) -> None:
        await self.prepare()
        timestamp = self.cache / ".git" / self.namespace["CACHE_FETCH_TIMESTAMP_FILENAME"]
        for value in ("nan", "inf", "-inf", "-1", str(time.time() + 3600), "broken"):
            with self.subTest(
                value=value,
            ):
                timestamp.write_text(
                    value,
                )
                fetch = AsyncMock(
                    return_value=(False, "git fetch failed: fixture offline"),
                )
                with patch.object(
                    self.manager,
                    "_fetch_in_cache",
                    fetch,
                ):
                    success, reason, _ = await self.prepare(
                        name="stale-job",
                        branch="trunk",
                    )
                fetch.assert_awaited_once()
                self.assertFalse(
                    success,
                )
                self.assertIn(
                    "fixture offline",
                    reason,
                )
                self.assertFalse(
                    (self.root / "stale-job").exists(),
                )

    async def test_spawn_failure_returns_a_failure_and_removes_partial_clone(
        self,
    ) -> None:
        async def failed(
            *arguments,
            **keywords,
        ):
            self.cache.mkdir()
            (self.cache / "partial").touch()
            raise git.GitOperationError(
                "Git process could not run (OS error 24)",
            )

        with patch.dict(
            self.namespace,
            {"_run_git": failed},
        ):
            success, reason, branch = await self.prepare(
                branch="trunk",
            )
        self.assertEqual(
            (success, reason, branch),
            (False, "Git process could not run (OS error 24)", None),
        )
        self.assertFalse(
            self.cache.exists(),
        )

    async def test_cache_query_spawn_failure_preserves_existing_cache(
        self,
    ) -> None:
        await self.prepare()
        failed = AsyncMock(
            side_effect=git.GitOperationError("Git process could not run (OS error 24)"),
        )
        with patch.dict(
            self.namespace,
            {"_run_git": failed},
        ):
            success, reason, _ = await self.prepare(
                name="second-job",
                branch="trunk",
            )
        self.assertFalse(
            success,
        )
        self.assertIn(
            "OS error 24",
            reason,
        )
        self.assertTrue(
            (self.cache / ".git" / "HEAD").exists(),
        )

    async def test_unusable_cache_diagnostic_omits_external_git_dir_output(
        self,
    ) -> None:
        self.cache.mkdir()
        (self.cache / ".git").write_text(
            f"gitdir: {self.source / '.git'}\n",
        )
        self.assertEqual(
            await self.manager._unusable_cache_reason(
                str(
                    self.cache,
                ),
            ),
            "repository cache does not own its .git directory",
        )

    async def test_linked_git_metadata_is_rebuilt_without_touching_external_repo(
        self,
    ) -> None:
        await self.prepare()
        shutil.rmtree(
            self.cache / ".git",
        )
        try:
            (self.cache / ".git").symlink_to(
                self.source / ".git",
                target_is_directory=True,
            )
        except OSError as error:
            self.skipTest(
                f"Directory symlink unavailable: {error}",
            )
        original_head = (self.source / ".git" / "HEAD").read_bytes()
        self.assertEqual(
            await self.prepare(
                name="second-job",
                branch="trunk",
            ),
            (True, self.sha, "trunk"),
        )
        self.assertEqual(
            (self.source / ".git" / "HEAD").read_bytes(),
            original_head,
        )
        self.assertFalse(
            (self.root / "second-job" / ".git").is_symlink(),
        )

    async def test_linked_cache_directory_cannot_modify_external_repo(
        self,
    ) -> None:
        try:
            self.cache.symlink_to(
                self.source,
                target_is_directory=True,
            )
        except OSError as error:
            self.skipTest(
                f"Directory symlink unavailable: {error}",
            )
        original_head = (self.source / ".git" / "HEAD").read_bytes()
        success, reason, _ = await self.prepare(
            branch="trunk",
        )
        self.assertFalse(
            success,
        )
        self.assertIn(
            "repository cache must own its directory and Git metadata",
            reason,
        )
        self.assertEqual(
            (self.source / ".git" / "HEAD").read_bytes(),
            original_head,
        )

    async def test_default_branch_timeout_retries_and_success_is_cached(
        self,
    ) -> None:
        query = AsyncMock(
            side_effect=[TimeoutError(), (0, b"ref: refs/heads/trunk\tHEAD\n", b"")],
        )
        with patch.dict(
            self.namespace,
            {"_run_git": query},
        ), patch.object(
            asyncio,
            "sleep",
            new_callable=AsyncMock,
        ):
            branches = await asyncio.gather(
                *(self.manager._resolve_default_branch_cached("fixture") for _ in range(5)),
            )
        self.assertEqual(
            branches,
            ["trunk"] * 5,
        )
        self.assertEqual(
            query.await_count,
            2,
        )

    async def test_clone_failure_removes_partial_repo_before_each_retry(
        self,
    ) -> None:
        async def clone(
            *arguments,
            **keywords,
        ):
            self.assertFalse(
                self.cache.exists(),
            )
            self.cache.mkdir()
            (self.cache / "partial").touch()
            return 128, b"", b"\xfffatal: connection reset by peer: synthetic-secret"

        with patch.dict(
            self.namespace,
            {"_run_git": clone},
        ), patch.object(
            asyncio,
            "sleep",
            new_callable=AsyncMock,
        ):
            success, reason, _ = await self.prepare(
                branch="trunk",
            )
        self.assertFalse(
            success,
        )
        self.assertIn(
            "connection interrupted",
            reason,
        )
        self.assertNotIn(
            "synthetic-secret",
            reason,
        )
        self.assertFalse(
            self.cache.exists(),
        )
