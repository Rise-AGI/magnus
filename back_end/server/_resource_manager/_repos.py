# back_end/server/_resource_manager/_repos.py
"""仓库 clone/fetch/checkout：默认分支解析、cache 时效性、ensure_repo 主流程。"""
import os
import re
import time
import shutil
import asyncio
import functools
import subprocess
from typing import Optional, Tuple

from .._magnus_config import magnus_config, is_local_mode
from . import logger
from ._config import (
    _git_env,
    DEFAULT_BRANCH_CACHE_TTL_SECONDS,
    REPO_FRESHNESS_TTL_SECONDS,
    GIT_FETCH_MAX_RETRIES,
    GIT_FETCH_TIMEOUT_SECONDS,
    GIT_CAT_FILE_TIMEOUT_SECONDS,
    CACHE_FETCH_TIMESTAMP_FILENAME,
    FULL_SHA_PATTERN,
)


class _ReposMixin:

    async def _resolve_default_branch(self, repo_url: str) -> Optional[str]:
        """
        通过 git ls-remote 解析仓库默认分支。
        带重试（网络瞬断）和超时（SSH 挂起）。
        """
        max_retries = 3
        timeout_seconds = 15

        proc: Optional[asyncio.subprocess.Process] = None
        for attempt in range(max_retries):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "ls-remote", "--symref", repo_url, "HEAD",
                    stdout = asyncio.subprocess.PIPE,
                    stderr = asyncio.subprocess.PIPE,
                    env = _git_env,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                logger.warning(f"git ls-remote timed out ({timeout_seconds}s), attempt {attempt + 1}/{max_retries}: {repo_url}")
                if proc is not None:
                    proc.kill()
                    await proc.wait()
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                continue

            if proc.returncode == 0:
                for line in stdout.decode().strip().splitlines():
                    if line.startswith("ref:"):
                        ref = line.split()[1]
                        return ref.replace("refs/heads/", "")
                return "main"

            error_msg = stderr.decode().strip()
            logger.warning(f"git ls-remote failed (rc={proc.returncode}), attempt {attempt + 1}/{max_retries}: {repo_url} — {error_msg}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        return None

    async def _resolve_default_branch_cached(self, repo_url: str) -> Optional[str]:
        """带 TTL 内存缓存 + per-repo 锁的默认分支解析。
        fanout 里 N 个 worker 命中同一个 repo 时，只有第一个打上游 ls-remote，
        其余在锁内等待 + 二次命中缓存直接返回。"""
        now = time.time()
        cached = self._default_branch_cache.get(repo_url)
        if cached is not None and now < cached[1]:
            return cached[0]

        if repo_url not in self._default_branch_locks:
            self._default_branch_locks[repo_url] = asyncio.Lock()

        async with self._default_branch_locks[repo_url]:
            # 二次检查：锁外已有其它协程完成解析
            cached = self._default_branch_cache.get(repo_url)
            if cached is not None and time.time() < cached[1]:
                return cached[0]

            branch = await self._resolve_default_branch(repo_url)
            if branch is not None:
                self._default_branch_cache[repo_url] = (
                    branch, time.time() + DEFAULT_BRANCH_CACHE_TTL_SECONDS,
                )
            return branch

    def _cache_fetch_ts_path(self, cache_path: str) -> str:
        return os.path.join(cache_path, ".git", CACHE_FETCH_TIMESTAMP_FILENAME)

    def _read_cache_fetch_ts(self, cache_path: str) -> float:
        """读取 cache 上次成功 fetch/clone 的 epoch 秒；缺失或损坏返回 0.0（视为极陈旧）"""
        try:
            with open(self._cache_fetch_ts_path(cache_path)) as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return 0.0

    def _write_cache_fetch_ts(self, cache_path: str) -> None:
        try:
            with open(self._cache_fetch_ts_path(cache_path), "w") as f:
                f.write(str(time.time()))
        except OSError as error:
            logger.warning(f"Failed to write fetch timestamp for {cache_path}: {error}")

    async def _cache_has_commit(self, cache_path: str, sha: str) -> bool:
        """检查 cache 的 object db 中是否已有该 commit。
        对具体 SHA 场景：若 cache 已有，可彻底 skip fetch（SHA 不可变，永远不会过时）。
        超时视为"不确定"—>返回 False，走 fetch 路径兜底，绝不因 I/O 挂起阻塞整个 fanout"""
        proc = await asyncio.create_subprocess_exec(
            "git", "cat-file", "-e", f"{sha}^{{commit}}",
            cwd = cache_path,
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.DEVNULL,
            env = _git_env,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=GIT_CAT_FILE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                f"git cat-file timed out ({GIT_CAT_FILE_TIMEOUT_SECONDS}s) "
                f"on {cache_path} for {sha}; treating as miss"
            )
            return False
        return proc.returncode == 0

    async def _fetch_in_cache(self, cache_path: str, repo_url: str) -> bool:
        """在 cache 里跑 git fetch origin，带 3 次重试和指数退避。
        重试语义与 _resolve_default_branch 对齐，便于日志对照分析。"""
        for attempt in range(GIT_FETCH_MAX_RETRIES):
            proc = await asyncio.create_subprocess_exec(
                "git", "fetch", "origin",
                cwd = cache_path,
                stdout = asyncio.subprocess.DEVNULL,
                stderr = asyncio.subprocess.PIPE,
                env = _git_env,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=GIT_FETCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning(
                    f"git fetch timed out ({GIT_FETCH_TIMEOUT_SECONDS}s), "
                    f"attempt {attempt + 1}/{GIT_FETCH_MAX_RETRIES}: {repo_url}"
                )
                if attempt < GIT_FETCH_MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                continue

            if proc.returncode == 0:
                return True

            error_msg = stderr.decode().strip()
            logger.warning(
                f"git fetch failed (rc={proc.returncode}), "
                f"attempt {attempt + 1}/{GIT_FETCH_MAX_RETRIES}: {repo_url} — {error_msg}"
            )
            if attempt < GIT_FETCH_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

        return False

    async def ensure_repo(
        self,
        namespace: str,
        repo_name: str,
        branch: Optional[str],
        commit_sha: Optional[str],
        target_dir: str,
        runner: str,
        job_working_dir: str,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        确保仓库可用。返回 (success, resolved_sha_or_error, resolved_branch)
        - 成功：(True, resolved_sha, resolved_branch)
        - 失败：(False, "error message", None)
        """
        if os.path.exists(target_dir):
            return True, None, branch

        # commit_sha=None 等同于 "HEAD"
        if commit_sha is None:
            commit_sha = "HEAD"

        # HPC 用 SSH（服务器有部署密钥），local 用 HTTPS（用户机器未必配 SSH key）
        # 时间紧迫下的最优解；未来应由配置项统一控制 repo URL 协议
        if is_local_mode:
            repo_url = f"https://github.com/{namespace}/{repo_name}.git"
        else:
            repo_url = f"git@github.com:{namespace}/{repo_name}.git"

        # branch=None: 解析仓库默认分支（带 TTL 内存缓存，fanout 里只打一次 ls-remote）
        if branch is None:
            branch = await self._resolve_default_branch_cached(repo_url)
            if branch is None:
                return False, "Failed to determine default branch", None

        cache_path = self._get_repo_cache_path(namespace, repo_name, branch)
        cache_key = f"{namespace}/{repo_name}/{branch}"

        # Phase 1 + 2: 在 repo_lock 内串行化 clone / fetch / copy。
        # - fanout 的 N 个 job 共享这把锁 → N 次上游 fetch 被去重为最多 1 次
        # - copy 保留在锁内，避免"job A 在 copytree 时 job B 的 fetch 改写 cache"导致的撕裂读
        # - checkout 在锁外跑（target_dir 已是隔离副本，无需上游 I/O）
        if cache_key not in self.repo_locks:
            self.repo_locks[cache_key] = asyncio.Lock()

        # 只有 40 位完整 hex SHA 走"cache 已有则跳过 fetch"的快路径；
        # "HEAD" / "msg:..." / tag 名 / branch 名 / 短 SHA 都按需时效处理（TTL + fail-closed），
        # 否则本次修复的"绝不回落化石"语义会从非 40 位入口复发
        is_immutable_sha = bool(FULL_SHA_PATTERN.match(commit_sha))

        async with self.repo_locks[cache_key]:
            # Phase 1a: cache 不存在 → clone（刚 clone 完即视为最新）
            if not os.path.exists(cache_path):
                self._evict_lru_repos()

                start_time = time.time()
                logger.info(f"Cloning repo to cache: {repo_url} -> {cache_path}")

                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--branch", branch, "--single-branch", repo_url, cache_path,
                    stdout = asyncio.subprocess.DEVNULL,
                    stderr = asyncio.subprocess.PIPE,
                    env = _git_env,
                )
                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    clone_error = stderr.decode().strip()
                    logger.error(f"Clone failed ({repo_url}): {clone_error}")
                    if os.path.exists(cache_path):
                        shutil.rmtree(cache_path, ignore_errors=True)
                    return False, f"git clone failed: {clone_error}", None

                elapsed = time.time() - start_time
                logger.info(f"Repo cached: {cache_path} ({elapsed:.1f}s)")
                self._write_cache_fetch_ts(cache_path)

            # Phase 1b: cache 已存在 → 按需刷新
            else:
                if is_immutable_sha:
                    # 完整 SHA：不可变对象，cache 已有就永远可用；没有才 fetch
                    # （不走 TTL——我们需要的是对象存在性，不是时效性）
                    if not await self._cache_has_commit(cache_path, commit_sha):
                        fetch_ok = await self._fetch_in_cache(cache_path, repo_url)
                        if fetch_ok:
                            self._write_cache_fetch_ts(cache_path)
                        if not await self._cache_has_commit(cache_path, commit_sha):
                            logger.error(
                                f"Commit {commit_sha} not found in cache for {repo_url} "
                                f"(fetch_ok={fetch_ok})"
                            )
                            return False, (
                                f"commit {commit_sha} not found in repository "
                                f"(fetch_ok={fetch_ok})"
                            ), None
                else:
                    # "HEAD" / "msg:..." / tag / branch / 短 SHA 都可能指向移动目标：
                    # TTL 内视为足够新（fanout 聚合窗口），过了 TTL 就必须成功 fetch，
                    # 否则直接 FAIL——绝不回落到化石 cache
                    cache_age = time.time() - self._read_cache_fetch_ts(cache_path)
                    if cache_age > REPO_FRESHNESS_TTL_SECONDS:
                        fetch_ok = await self._fetch_in_cache(cache_path, repo_url)
                        if fetch_ok:
                            self._write_cache_fetch_ts(cache_path)
                        else:
                            logger.error(
                                f"git fetch failed for {repo_url}; "
                                f"refusing to serve stale cache for commit_sha={commit_sha!r}"
                            )
                            return False, (
                                f"git fetch failed for {repo_url}: "
                                f"cannot guarantee freshness for {commit_sha!r}"
                            ), None

            # 更新 cache 访问时间（LRU 按 atime 淘汰，活跃 cache 需持续刷 atime）
            try:
                os.utime(cache_path, None)
            except OSError:
                pass

            # Phase 2: 锁内复制 cache 到工作目录。
            # copytree 放线程池避免阻塞 event loop；锁保证其它协程不会在我们 copy 时
            # 通过 fetch 改写 cache 的 refs / pack 文件
            start_time = time.time()
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(shutil.copytree, cache_path, target_dir, symlinks=True),
                )
            except Exception as error:
                # 清掉可能的半成品 target_dir，否则入口 `if os.path.exists(target_dir)` 短路条件
                # 会在下次调度时把残留副本误判为"已就绪"，跑在不完整代码上
                shutil.rmtree(target_dir, ignore_errors=True)
                logger.error(f"Failed to copy repo cache: {error}")
                return False, f"copy cache failed: {error}", None

        # Phase 3: 解析 commit_sha 语义并 checkout（无需 fetch——cache 上一步已刷新，
        # target_dir 是隔离副本，origin/<branch> 与 cache 保持一致）
        # - "HEAD": checkout origin/<branch> 最新提交
        # - "msg:<regex>": 搜索最近的 commit message 匹配正则的提交
        # - 其他: 视为 40 位 SHA 或 tag 等 git ref
        assert branch is not None
        if commit_sha == "HEAD":
            effective_sha = f"origin/{branch}"
        elif commit_sha.startswith("msg:"):
            pattern_str = commit_sha[4:].strip()
            try:
                pattern = re.compile(pattern_str)
            except re.error as error:
                shutil.rmtree(target_dir, ignore_errors=True)
                return False, f"Invalid commit message regex '{pattern_str}': {error}", None

            max_commit_search = 200
            proc = await asyncio.create_subprocess_exec(
                "git", "log", f"origin/{branch}", "--format=%H %s", "-n", str(max_commit_search),
                cwd = target_dir,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.DEVNULL,
                env = _git_env,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                shutil.rmtree(target_dir, ignore_errors=True)
                return False, "Failed to read git log for commit message search", None

            effective_sha = None
            for line in stdout.decode().strip().splitlines():
                sha, _, message = line.partition(" ")
                if pattern.search(message):
                    effective_sha = sha
                    break

            if effective_sha is None:
                shutil.rmtree(target_dir, ignore_errors=True)
                return False, f"No commit found matching pattern '{pattern_str}' in recent {max_commit_search} commits on {branch}", None

            logger.info(f"Resolved msg:{pattern_str} -> {effective_sha[:12]}")
        else:
            effective_sha = commit_sha
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-f", effective_sha,
            cwd = target_dir,
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.PIPE,
            env = _git_env,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to checkout {commit_sha}: {error_msg}")
            shutil.rmtree(target_dir, ignore_errors=True)
            return False, f"git checkout failed: {error_msg}", None

        # 解析真实 SHA（将 HEAD / origin/branch 等符号引用固化为 40 位哈希）
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd = target_dir,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.DEVNULL,
            env = _git_env,
        )
        stdout, _ = await proc.communicate()
        resolved_sha = stdout.decode().strip() if proc.returncode == 0 else commit_sha

        # Phase 4: 设置 ACL（local 模式下跳过，Docker 容器内不需要 host ACL）
        if not is_local_mode:
            default_runner = magnus_config["cluster"]["default_runner"]
            try:
                subprocess.run(
                    [
                        "setfacl", "-R",
                        "-m", f"u:{runner}:rwx",
                        "-d", "-m", f"u:{default_runner}:rwx",
                        "-d", "-m", f"u:{runner}:rwx",
                        job_working_dir,
                    ],
                    check = True,
                    capture_output = True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as error:
                logger.warning(f"setfacl failed: {error}")

        elapsed = time.time() - start_time
        logger.info(f"Repo ready: {target_dir} ({elapsed:.1f}s)")
        return True, resolved_sha, branch
