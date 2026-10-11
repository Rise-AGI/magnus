# back_end/server/_resource_manager/_repos.py
"""仓库 clone/fetch/checkout：默认分支解析、cache 时效性、ensure_repo 主流程。"""
import os
import re
import time
import shutil
import asyncio
import signal
import sys
from typing import TYPE_CHECKING, Optional, Tuple

from library import is_disk_full_stderr, disk_full_message

from .._magnus_config import is_local_mode
from .._job_dir_access import grant_job_dir_runner_access
from . import logger, magnus_repo_cache_path
from ._config import (
    _git_env,
    DEFAULT_BRANCH_CACHE_TTL_SECONDS,
    REPO_FRESHNESS_TTL_SECONDS,
    GIT_FETCH_MAX_RETRIES,
    GIT_FETCH_TIMEOUT_SECONDS,
    GIT_CLONE_MAX_RETRIES,
    GIT_LOCAL_QUERY_TIMEOUT_SECONDS,
    CACHE_FETCH_TIMESTAMP_FILENAME,
    FULL_SHA_PATTERN,
)
from ._helpers import _remove_tree, _run_blocking_io


if TYPE_CHECKING:
    from ._typing import _ResourceManagerProtocol
    _ReposMixinBase = _ResourceManagerProtocol
else:
    _ReposMixinBase = object


async def _remove_cache_dir(
    cache_path: str,
) -> None:
    """在线程里删掉 cache 目录；期间任务即便被取消，也等删完再把取消抛出去。

    调用方持有这个目录的 repo_lock。取消若直接打断等待，async with 会立刻放锁，而删除
    线程还在跑，下一个拿到锁的 job 就会在正被删的目录里 clone。线程停不下来，所以一直
    等到它结束。"""
    await _run_blocking_io(
        _remove_tree,
        cache_path,
    )


async def _kill_git_process(
    process: asyncio.subprocess.Process,
) -> None:
    """Stop Git and its transport helpers while communicate keeps pipes drained."""
    if os.name == "posix":
        # SSH/HTTPS helpers can retain Git's pipes or continue writing packs.
        # Every invocation has its own session, so the group is ours to stop.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        # The Windows launcher owns a KILL_ON_JOB_CLOSE Job Object. Killing
        # it closes the only handle, terminating Git and its transport helpers.
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def _run_git(
    *arguments: str,
    cwd: Optional[str] = None,
    timeout: Optional[float] = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> Tuple[int, bytes, bytes]:
    """Run Git with cancellation-safe spawn, pipe draining and process reaping.

    The caller retains its repository lock until this function has stopped all
    child I/O, including when cancellation arrives during process creation or
    arrives repeatedly during cleanup. Clone intentionally has no time limit;
    cancelling its owning preparation still interrupts network I/O immediately.
    """
    command = ["git"]
    if os.name == "nt":
        command = [
            sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "_git_windows.py"),
        ]
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *command,
        *arguments,
        cwd = cwd,
        stdin = asyncio.subprocess.DEVNULL,
        stdout = asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
        stderr = asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
        env = _git_env,
        start_new_session = os.name == "posix",
    ))
    communication = None
    try:
        process = await asyncio.shield(spawning)
        communication = asyncio.create_task(process.communicate())
        if timeout is None:
            stdout, stderr = await asyncio.shield(communication)
        else:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communication),
                timeout = timeout,
            )
        assert process.returncode is not None
        return process.returncode, stdout or b"", stderr or b""
    except BaseException:
        async def stop_and_reap() -> None:
            try:
                process = await spawning
            except Exception:
                return  # Spawn failed before acquiring a process to reap.
            reader = communication or asyncio.create_task(process.communicate())
            try:
                await _kill_git_process(process)
            finally:
                await asyncio.gather(
                    reader,
                    return_exceptions = True,
                )

        cleanup = asyncio.create_task(stop_and_reap())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        raise


class _ReposMixin(_ReposMixinBase):

    async def _resolve_default_branch(self, repo_url: str) -> Optional[str]:
        """
        通过 git ls-remote 解析仓库默认分支。
        带重试（网络瞬断）和超时（SSH 挂起）。
        """
        max_retries = 3
        timeout_seconds = 15

        for attempt in range(max_retries):
            try:
                returncode, stdout, stderr = await _run_git(
                    "ls-remote",
                    "--symref",
                    repo_url,
                    "HEAD",
                    timeout = timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(f"git ls-remote timed out ({timeout_seconds}s), attempt {attempt + 1}/{max_retries}: {repo_url}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                continue

            if returncode == 0:
                for line in stdout.decode().strip().splitlines():
                    if line.startswith("ref:"):
                        ref = line.split()[1]
                        return ref.replace("refs/heads/", "")
                return "main"

            error_msg = stderr.decode().strip()
            logger.warning(f"git ls-remote failed (rc={returncode}), attempt {attempt + 1}/{max_retries}: {repo_url} — {error_msg}")
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

    async def _run_cache_git_query(
        self,
        cache_path: str,
        *args: str,
    ) -> Optional[Tuple[bool, str, str]]:
        """在 cache 目录里跑一条本地只读 git 查询，返回 (是否成功, stdout, stderr)；超时返回 None。
        超时如何解读由调用方决定。"""
        try:
            returncode, stdout, stderr = await _run_git(
                *args,
                cwd = cache_path,
                timeout = GIT_LOCAL_QUERY_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return None
        return returncode == 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    async def _cache_has_commit(self, cache_path: str, sha: str) -> bool:
        """检查 cache 的 object db 中是否已有该 commit。
        对具体 SHA 场景：若 cache 已有，可彻底 skip fetch（SHA 不可变，永远不会过时）。
        超时视为"不确定"—>返回 False，走 fetch 路径兜底，绝不因 I/O 挂起阻塞整个 fanout"""
        result = await self._run_cache_git_query(cache_path, "cat-file", "-e", f"{sha}^{{commit}}")
        if result is None:
            logger.warning(
                f"git cat-file timed out ({GIT_LOCAL_QUERY_TIMEOUT_SECONDS}s) "
                f"on {cache_path} for {sha}; treating as miss"
            )
            return False
        succeeded, _, _ = result
        return succeeded

    async def _unusable_cache_reason(self, cache_path: str) -> Optional[str]:
        """cache 目录不能当作仓库 cache 用的原因；能用返回 None。须在 repo_lock 内调用。

        能用指三件事：它本身就是一个 git 仓库，HEAD 解析到一个 commit，且没有被中断的
        git 进程留下的 index.lock。前两件由 `git rev-parse --git-dir --verify HEAD^{commit}`
        一次回答，并要求输出的 git dir 恰为相对的 `.git`：目录里没有 .git 时 git 会沿父
        目录向上找仓库，找到了照样返回 0、只是给出那个仓库的绝对路径，这时在这里 fetch /
        checkout 就会作用到别的仓库上。`--verify` 防止解析不了的 HEAD 被 git 改当路径名。
        index.lock：cache 的 index 只有 clone 会写，且在 repo_lock 内写，持锁时它还在就
        一定是残留；它会随 copytree 进入每个 job 的副本，让那里的 checkout 失败。

        返回的原因带着 git 自己的报错：git 的运行环境出问题（比如全局 gitconfig 写坏）时
        健康的 cache 也通不过这项检查，日志里得分辨得出来。查询超时算无法判断、按能用
        处理，不因一次 I/O 抖动删掉好的 cache。"""
        if not os.path.isdir(cache_path):
            return "not a directory"
        result = await self._run_cache_git_query(
            cache_path, "rev-parse", "--git-dir", "--verify", "HEAD^{commit}",
        )
        if result is None:
            logger.warning(
                f"git rev-parse timed out ({GIT_LOCAL_QUERY_TIMEOUT_SECONDS}s) "
                f"on {cache_path}; assuming the cache is usable"
            )
            return None
        succeeded, stdout, stderr = result
        if not succeeded:
            return f"git rev-parse failed: {stderr.strip() or 'no error output'}"
        lines = stdout.splitlines()
        git_dir = lines[0].strip() if lines else ""
        if git_dir != ".git":
            return f"no .git of its own (git resolved {git_dir or 'nothing'})"
        if os.path.exists(os.path.join(cache_path, ".git", "index.lock")):
            return "stale .git/index.lock left by an interrupted git process"
        return None

    async def _fetch_in_cache(self, cache_path: str, repo_url: str) -> bool:
        """在 cache 里跑 git fetch origin，带 3 次重试和指数退避。
        重试语义与 _resolve_default_branch 对齐，便于日志对照分析。"""
        for attempt in range(GIT_FETCH_MAX_RETRIES):
            try:
                returncode, _, stderr = await _run_git(
                    "fetch",
                    "origin",
                    cwd = cache_path,
                    timeout = GIT_FETCH_TIMEOUT_SECONDS,
                    capture_stdout = False,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"git fetch timed out ({GIT_FETCH_TIMEOUT_SECONDS}s), "
                    f"attempt {attempt + 1}/{GIT_FETCH_MAX_RETRIES}: {repo_url}"
                )
                if attempt < GIT_FETCH_MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                continue
            if returncode == 0:
                return True

            error_msg = stderr.decode().strip()
            # 磁盘满不会在 backoff 期间自愈——和 image pull 同理，直接 fail-fast，
            # 不烧 retry/backoff 预算。
            if is_disk_full_stderr(error_msg):
                logger.error(
                    f"git fetch aborted (disk full): {repo_url} — "
                    f"{disk_full_message(magnus_repo_cache_path)}"
                )
                return False
            logger.warning(
                f"git fetch failed (rc={returncode}), "
                f"attempt {attempt + 1}/{GIT_FETCH_MAX_RETRIES}: {repo_url} — {error_msg}"
            )
            if attempt < GIT_FETCH_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

        return False

    async def _clone_into_cache(
        self,
        repo_url: str,
        branch: str,
        cache_path: str,
    ) -> Tuple[bool, Optional[str]]:
        """把仓库 clone 进 cache，带重试和指数退避。返回 (成功, 失败时的用户可见消息)。

        重试语义与 _fetch_in_cache 对齐。首次 clone 同样要穿越上游 / 代理，而它是
        传输量最大、最容易撞上瞬时故障（限流、连接重置）的那一次；不重试的话一次
        抖动就让整个 job 失败。与 fetch 不同的是这里不设超时，理由见
        GIT_CLONE_MAX_RETRIES 的注释。
        """
        clone_error = ""
        for attempt in range(GIT_CLONE_MAX_RETRIES):
            try:
                returncode, _, stderr = await _run_git(
                    "clone",
                    "--branch",
                    branch,
                    "--single-branch",
                    repo_url,
                    cache_path,
                    capture_stdout = False,
                )
            except asyncio.CancelledError:
                # _run_git has already stopped and reaped every writer. Keep
                # repo_lock until partial-clone removal has joined as well.
                if os.path.exists(cache_path):
                    await _remove_cache_dir(cache_path)
                raise

            if returncode == 0:
                return True, None

            clone_error = stderr.decode().strip()
            # 失败会留下半个 cache 目录，而 git clone 拒绝写入已存在的非空目录 ——
            # 不清掉的话后续重试（以及下一个 job）会连环失败在"目标已存在"上。
            if os.path.exists(cache_path):
                await _remove_cache_dir(cache_path)
                if os.path.exists(cache_path):
                    # 清不掉（权限等）时后续重试必然栽在"目标已存在"上，那条消息还会
                    # 把真正的根因从用户可见错误里挤掉 —— 带着真错误立刻停。
                    logger.error(
                        f"Clone cleanup left {cache_path} in place; "
                        f"not retrying ({repo_url}): {clone_error}"
                    )
                    return False, f"git clone failed: {clone_error}"
            # 磁盘满不会在 backoff 期间自愈 —— 和 fetch / image pull 同理，直接
            # fail-fast，不烧 retry/backoff 预算。
            if is_disk_full_stderr(clone_error):
                logger.error(f"Clone aborted (disk full) ({repo_url}): {clone_error}")
                return False, disk_full_message(magnus_repo_cache_path)
            logger.warning(
                f"Clone failed (rc={returncode}), "
                f"attempt {attempt + 1}/{GIT_CLONE_MAX_RETRIES}: {repo_url} — {clone_error}"
            )
            if attempt < GIT_CLONE_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

        logger.error(
            f"Clone failed after {GIT_CLONE_MAX_RETRIES} attempts ({repo_url}): {clone_error}"
        )
        return False, f"git clone failed: {clone_error}"

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
        # commit_sha=None 等同于 "HEAD"
        if commit_sha is None:
            commit_sha = "HEAD"

        # commit_sha 由用户提交、未经校验，最终会进 git checkout 的参数表。合法的修订号不以 `-`
        # 开头（git 不接受这样的分支 / tag 名，SHA 也不会），先拒掉，git 就不会把它当成选项解析。
        if commit_sha.startswith("-"):
            return False, f"invalid commit reference {commit_sha!r}", None

        # 进来时 job 的仓库副本已经在了，是上一轮留下的：准备被打断（比如后端在镜像还在拉时
        # 重启，PREPARING 的 job 被重新派发），或抢占后的收尾清理没删干净。它可能只拷了一半、
        # 被上一轮的 job 改过，解析出的 commit 也未必落了库 —— 删掉，按正常流程重建。
        if os.path.exists(target_dir):
            await _run_blocking_io(
                _remove_tree,
                target_dir,
            )
            if os.path.exists(target_dir):
                return False, f"leftover repository copy {target_dir} could not be removed", None

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

        # Phase 0–2: 在 repo_lock 内串行化 cache 自检 / clone / fetch / copy。
        # - 锁按 cache 目录取：一个目录只能对应一把锁（Phase 0 会删掉整个目录）
        # - fanout 的 N 个 job 共享这把锁 → N 次上游 fetch 被去重为最多 1 次
        # - copy 保留在锁内，避免"job A 在 copytree 时 job B 的 fetch 改写 cache"导致的撕裂读
        # - checkout 在锁外跑（target_dir 已是隔离副本，无需上游 I/O）
        if cache_path not in self.repo_locks:
            self.repo_locks[cache_path] = asyncio.Lock()

        # 只有 40 位完整 hex SHA 走"cache 已有则跳过 fetch"的快路径；
        # "HEAD" / "msg:..." / tag 名 / branch 名 / 短 SHA 都按需时效处理（TTL + fail-closed），
        # 否则本次修复的"绝不回落化石"语义会从非 40 位入口复发
        is_immutable_sha = bool(FULL_SHA_PATTERN.match(commit_sha))

        async with self.repo_locks[cache_path]:
            # Phase 0: 目录在、却不能用的 cache（clone 进程在清理前被杀、运维拷贝中断、
            # .git 损坏、残留 index.lock）按未命中处理，删掉后走下面的 clone。留着它的话，
            # 下面的 fetch 和每个 job 副本里的 checkout 会在它上面一再失败，这个仓库的每个
            # job 都跟着失败，直到有人手动删掉目录。
            if os.path.exists(cache_path):
                problem = await self._unusable_cache_reason(cache_path)
                if problem is not None:
                    await _remove_cache_dir(cache_path)
                    if os.path.exists(cache_path):
                        message = f"repo cache {cache_path} is unusable ({problem}) and could not be removed"
                        logger.error(message)
                        return False, message, None
                    logger.warning(f"Removed unusable repo cache {cache_path} ({problem}); cloning it again")

            # Phase 1a: cache 不存在 → clone（刚 clone 完即视为最新）
            if not os.path.exists(cache_path):
                await self._evict_lru_repos()

                start_time = time.time()
                logger.info(f"Cloning repo to cache: {repo_url} -> {cache_path}")

                clone_ok, clone_error = await self._clone_into_cache(
                    repo_url = repo_url,
                    branch = branch,
                    cache_path = cache_path,
                )
                if not clone_ok:
                    return False, clone_error, None

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
            try:
                await _run_blocking_io(
                    shutil.copytree,
                    cache_path,
                    target_dir,
                    symlinks = True,
                )
            except Exception as error:
                # 清掉半成品 target_dir，别让失败的 job 在工作区里留一份残缺副本
                await _run_blocking_io(
                    _remove_tree,
                    target_dir,
                )
                logger.error(f"Failed to copy repo cache: {error}")
                # copytree 把 per-file 错误聚合成 shutil.Error，磁盘满信息在 str(error) 里。
                # target_dir 刚被删掉，查它自身的剩余空间会失败返 0；查其父目录
                # （同一卷、仍存在）才能报出真实可用字节，且填满的正是目标卷。
                if is_disk_full_stderr(str(error)):
                    return False, disk_full_message(os.path.dirname(target_dir)), None
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
                await _run_blocking_io(
                    _remove_tree,
                    target_dir,
                )
                return False, f"Invalid commit message regex '{pattern_str}': {error}", None

            max_commit_search = 200
            returncode, stdout, _ = await _run_git(
                "log",
                f"origin/{branch}",
                "--format=%H %s",
                "-n",
                str(max_commit_search),
                cwd = target_dir,
                capture_stderr = False,
            )
            if returncode != 0:
                await _run_blocking_io(
                    _remove_tree,
                    target_dir,
                )
                return False, "Failed to read git log for commit message search", None

            effective_sha = None
            for line in stdout.decode().strip().splitlines():
                sha, _, message = line.partition(" ")
                if pattern.search(message):
                    effective_sha = sha
                    break

            if effective_sha is None:
                await _run_blocking_io(
                    _remove_tree,
                    target_dir,
                )
                return False, f"No commit found matching pattern '{pattern_str}' in recent {max_commit_search} commits on {branch}", None

            logger.info(f"Resolved msg:{pattern_str} -> {effective_sha[:12]}")
        else:
            effective_sha = commit_sha
        # 末尾的 `--` 让 git 只把 effective_sha 当修订号：解析不了就直接失败，不会退而把它当路径、
        # 从 index 恢复同名文件后照常返回成功，把副本留在 cache clone 时的旧提交上。
        # Git builtin/checkout.c 的 parse_branchname_arg case 3 明确接受 `<ref> [--]`；
        # 没有 pathspec 时仍进入 checkout_branch，不是文件恢复模式。
        returncode, _, stderr = await _run_git(
            "checkout",
            "-f",
            effective_sha,
            "--",
            cwd = target_dir,
            capture_stdout = False,
        )

        if returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to checkout {commit_sha}: {error_msg}")
            await _run_blocking_io(
                _remove_tree,
                target_dir,
            )
            return False, f"git checkout failed: {error_msg}", None

        # Pin the checked-out commit, never fall back to a moving symbolic ref.
        # Git repositories may use SHA-1 or SHA-256 object identifiers.
        returncode, stdout, stderr = await _run_git(
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            cwd = target_dir,
        )
        resolved_sha = stdout.decode("ascii", errors="replace").strip()
        if returncode != 0 or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", resolved_sha):
            logger.error(f"Failed to resolve checked-out commit: {stderr.decode(errors='replace').strip()}")
            await _run_blocking_io(
                _remove_tree,
                target_dir,
            )
            return False, "Failed to resolve the checked-out commit to a full SHA", None

        # Phase 4: 设置 ACL（local 模式下跳过，Docker 容器内不需要 host ACL）
        if not is_local_mode:
            await _run_blocking_io(
                grant_job_dir_runner_access,
                job_working_dir,
                runner,
            )

        elapsed = time.time() - start_time
        logger.info(f"Repo ready: {target_dir} ({elapsed:.1f}s)")
        return True, resolved_sha, branch
