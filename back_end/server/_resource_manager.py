# back_end/server/_resource_manager.py
import os
import re
import time
import shutil
import asyncio
import logging
import functools
import subprocess
from typing import Dict, List, Optional, Tuple
from pywheels.file_tools import guarantee_file_exist
from ._magnus_config import magnus_config, is_local_mode


__all__ = ["resource_manager"]


logger = logging.getLogger(__name__)


_registry_mirror: Optional[str] = magnus_config["cluster"]["registry_mirror"]


_DOCKER_HUB_HOSTS = {"registry-1.docker.io", "index.docker.io", "docker.io"}


def _rewrite_image_for_mirror(image: str)-> str:
    if _registry_mirror is None:
        return image

    for scheme in ("docker://", "oras://"):
        if image.startswith(scheme):
            rest = image[len(scheme):]
            break
    else:
        return image

    parts = rest.split("/")
    host = parts[0] if len(parts) > 1 and ("." in parts[0] or ":" in parts[0]) else None

    if host is None:
        # docker://alpine, docker://pytorch/pytorch:tag — implicit Docker Hub
        name_part = rest.split(":")[0].split("@")[0]
        if "/" not in name_part:
            rest = "library/" + rest
        return f"{scheme}{_registry_mirror}/{rest}"

    if host in _DOCKER_HUB_HOSTS:
        # oras://registry-1.docker.io/user/repo:tag — explicit Docker Hub
        rest_without_host = "/".join(parts[1:])
        return f"{scheme}{_registry_mirror}/{rest_without_host}"

    return image


magnus_root = magnus_config['server']['root']
magnus_container_cache_path = f"{magnus_root}/container_cache"
magnus_repo_cache_path = f"{magnus_root}/repo_cache"
magnus_apptainer_cache_path = f"{magnus_root}/apptainer_cache"
guarantee_file_exist(magnus_container_cache_path, is_directory=True)
guarantee_file_exist(magnus_repo_cache_path, is_directory=True)
guarantee_file_exist(magnus_apptainer_cache_path, is_directory=True)


def _parse_size_string(size_str: str)-> int:
    """解析大小字符串，如 '200G', '1024M'，返回字节数"""
    size_str = size_str.strip().upper()
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)


def _get_dir_size(path: str)-> int:
    """递归计算目录大小"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


CONTAINER_CACHE_SIZE = _parse_size_string(magnus_config['execution']['resource_cache']['container_cache_size'])
REPO_CACHE_SIZE = _parse_size_string(magnus_config['execution']['resource_cache']['repo_cache_size'])

# git subprocess 使用的干净环境：禁用交互式认证，剔除 IDE 注入的 credential helper
_git_env = os.environ.copy()
_git_env["GIT_TERMINAL_PROMPT"] = "0"
for _k in ("GIT_ASKPASS", "SSH_ASKPASS"):
    _git_env.pop(_k, None)


# Fanout 聚合窗口：同一个 cache 的 origin refs 在这个 TTL 内被认为足够新，
# 避免一次 blueprint fanout 里 N 个 worker 串行打 N 次上游 fetch
# （TTL 内所有 job 共享同一次 fetch 的结果，N 次上游请求 → 1 次）
REPO_FRESHNESS_TTL_SECONDS = 30

# 默认分支解析结果的内存缓存 TTL：fanout 里所有 worker 解析的是同一个 repo
# 的默认分支，没必要每次都打 ls-remote
DEFAULT_BRANCH_CACHE_TTL_SECONDS = 300

# git fetch 重试与超时（对齐 _resolve_default_branch 的 3 次重试 + 指数退避；
# fetch 比 ls-remote 重，超时放宽到 30s）
GIT_FETCH_MAX_RETRIES = 3
GIT_FETCH_TIMEOUT_SECONDS = 30

# cat-file 只读本地 object db，正常毫秒级；这里只是为了在 NFS / 盘满等异常
# I/O 场景下避免无限期阻塞整个 fanout（此 subprocess 是在 repo_lock 内调用）
GIT_CAT_FILE_TIMEOUT_SECONDS = 5

# cache 的上次 fetch 时间戳文件名，放在 <cache>/.git/ 里避免污染 working tree
CACHE_FETCH_TIMESTAMP_FILENAME = "magnus_fetch_ts"

# 只有 40 位完整 hex SHA 被视为不可变、可以走"cache 已有则跳过 fetch"的快路径。
# tag / branch 名 / 短 SHA 都可能指向移动目标或产生歧义，必须经 TTL + 显式 fetch 把关，
# 避免把陈旧的同名 ref 悄悄当成用户想要的那一版交付出去。
# 大小写都接受：git 默认小写，但人工复制 / API 产物中大写 SHA 合法且常见
FULL_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def _image_to_sif_filename(image: str)-> str:
    """docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime -> pytorch_pytorch_2.5.1-cuda12.4-cudnn9-runtime.sif"""
    name = re.sub(r'^[a-z]+://', '', image)
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return f"{name}.sif"


def _repo_to_cache_dirname(namespace: str, repo_name: str, branch: str)-> str:
    """namespace/repo_name/branch -> namespace_repo_name_branch"""
    name = f"{namespace}_{repo_name}_{branch}"
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name


class ResourceManager:
    """
    中心化管理镜像和仓库，由 magnus 系统用户执行。
    - 镜像：拉取到公共缓存，chmod 644，LRU 清理
    - 仓库：clone 到缓存，复制到工作目录，setfacl 授权，LRU 清理
    """

    def __init__(self):
        self.image_locks: Dict[str, asyncio.Lock] = {}
        self.repo_locks: Dict[str, asyncio.Lock] = {}
        # repo_url -> (default_branch, expires_at_epoch_seconds)
        self._default_branch_cache: Dict[str, Tuple[str, float]] = {}
        # repo_url -> asyncio.Lock, 用于串行化同 repo 的 ls-remote，
        # 让 fanout 里先到的解析结果被后到者复用
        self._default_branch_locks: Dict[str, asyncio.Lock] = {}

    def get_sif_path(self, image: str)-> str:
        return os.path.join(magnus_container_cache_path, _image_to_sif_filename(image))

    def _get_repo_cache_path(self, namespace: str, repo_name: str, branch: str)-> str:
        return os.path.join(magnus_repo_cache_path, _repo_to_cache_dirname(namespace, repo_name, branch))

    def _get_cached_images(self)-> List[Tuple[str, int, float]]:
        """获取缓存的镜像列表，返回 [(path, size_bytes, atime), ...]"""
        images = []
        for filename in os.listdir(magnus_container_cache_path):
            if not filename.endswith('.sif'):
                continue
            path = os.path.join(magnus_container_cache_path, filename)
            try:
                stat = os.stat(path)
                images.append((path, stat.st_size, stat.st_atime))
            except OSError:
                continue
        return images

    def _get_cached_repos(self)-> List[Tuple[str, int, float]]:
        """获取缓存的仓库列表，返回 [(path, size_bytes, atime), ...]"""
        repos = []
        for dirname in os.listdir(magnus_repo_cache_path):
            path = os.path.join(magnus_repo_cache_path, dirname)
            if not os.path.isdir(path):
                continue
            try:
                stat = os.stat(path)
                size = _get_dir_size(path)
                repos.append((path, size, stat.st_atime))
            except OSError:
                continue
        return repos

    def _evict_lru_images(self):
        """LRU 清理：按访问时间淘汰旧镜像。
        注：此方法在 self.image_locks[image] 内调用，不会与同一 URI 的拉取并发；
        不同 URI 的并发淘汰理论上存在 TOCTOU，但 ensure_image 入口处的 os.utime
        会刷新 atime，使正在使用的镜像不会被误淘汰，无需额外加锁。"""
        images = self._get_cached_images()
        if not images:
            return

        images.sort(key=lambda x: x[2])
        total_size = sum(img[1] for img in images)

        while images and total_size > CONTAINER_CACHE_SIZE:
            path, size, _ = images.pop(0)
            try:
                os.remove(path)
                logger.info(f"LRU evicted image: {path}")
                total_size -= size
            except OSError as e:
                logger.warning(f"Failed to evict image {path}: {e}")

    def _evict_lru_repos(self):
        """LRU 清理：按访问时间淘汰旧仓库"""
        repos = self._get_cached_repos()
        if not repos:
            return

        repos.sort(key=lambda x: x[2])
        total_size = sum(repo[1] for repo in repos)

        while repos and total_size > REPO_CACHE_SIZE:
            path, size, _ = repos.pop(0)
            try:
                shutil.rmtree(path)
                logger.info(f"LRU evicted repo: {path}")
                total_size -= size
            except OSError as e:
                logger.warning(f"Failed to evict repo {path}: {e}")

    async def ensure_image(self, image: str, force: bool = False)-> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        force=True 时跳过缓存检查，强制重新拉取。
        """
        if is_local_mode:
            return await self._ensure_image_docker(image, force)
        return await self._ensure_image_apptainer(image, force)

    async def _ensure_image_docker(self, image: str, force: bool = False) -> Tuple[bool, Optional[str]]:
        """Docker 模式：使用 docker pull，Docker 自身管理镜像缓存"""
        pull_image = _rewrite_image_for_mirror(image)
        docker_image = re.sub(r'^[a-z]+://', '', pull_image)

        if not force:
            # 检查本地是否已有（用重写后的名称，因为 Docker 按实际拉取名存储）
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", docker_image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return True, None

        if pull_image != image:
            logger.info(f"🐳 Pulling Docker image: {image} (via mirror: {docker_image})")
        else:
            logger.info(f"🐳 Pulling Docker image: {docker_image}")
        start_time = time.time()

        proc = await asyncio.create_subprocess_exec(
            "docker", "pull", docker_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await proc.communicate()
        except asyncio.CancelledError:
            proc.terminate()
            await proc.wait()
            raise

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"❌ Docker pull failed for {docker_image}: {error_msg}")
            return False, error_msg

        elapsed = time.time() - start_time
        logger.info(f"✅ Docker image ready: {docker_image} ({elapsed:.1f}s)")
        return True, None

    async def _ensure_image_apptainer(self, image: str, force: bool = False)-> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        force=True 时跳过缓存检查，强制重新拉取。
        所有拉取都写入 .tmp 再原子 rename，断电不会留下半成品 .sif。
        """
        sif_path = self.get_sif_path(image)

        if not force and os.path.exists(sif_path):
            try:
                os.utime(sif_path, None)
            except OSError:
                pass
            return True, None

        if image not in self.image_locks:
            self.image_locks[image] = asyncio.Lock()

        async with self.image_locks[image]:
            if not force and os.path.exists(sif_path):
                try:
                    os.utime(sif_path, None)
                except OSError:
                    pass
                return True, None

            self._evict_lru_images()

            # 始终写入临时文件，成功后原子 rename
            # 这样断电/OOM 只会留下 .tmp，重启时统一清理，正式 .sif 不会被污染
            pull_dest = sif_path + ".tmp"

            pull_image = _rewrite_image_for_mirror(image)
            if pull_image != image:
                logger.info(f"Pulling container image: {image} (via mirror: {pull_image})")
            else:
                logger.info(f"Pulling container image: {image}")
            start_time = time.time()

            # 非瞬态错误（镜像不存在、鉴权失败等）直接失败，不浪费时间重试
            non_transient_patterns = ["unauthorized", "not found", "manifest unknown", "denied", "invalid reference"]
            max_retries = 3
            base_retry_delay = 10

            for attempt in range(max_retries):
                env = os.environ.copy()
                env["APPTAINER_CACHEDIR"] = magnus_apptainer_cache_path
                env["GODEBUG"] = "http2client=0"
                proc = await asyncio.create_subprocess_exec(
                    "apptainer", "pull", pull_dest, pull_image,
                    stdout = asyncio.subprocess.PIPE,
                    stderr = asyncio.subprocess.PIPE,
                    env = env,
                )
                try:
                    _, stderr = await proc.communicate()
                except asyncio.CancelledError:
                    # 优雅关闭：终止子进程，避免孤儿 apptainer 进程
                    proc.terminate()
                    await proc.wait()
                    if os.path.exists(pull_dest):
                        try:
                            os.remove(pull_dest)
                        except OSError:
                            pass
                    raise

                if proc.returncode == 0:
                    break

                # 清理残留的不完整 .tmp
                if os.path.exists(pull_dest):
                    try:
                        os.remove(pull_dest)
                    except OSError:
                        pass

                error_msg = stderr.decode().strip()
                error_lower = error_msg.lower()

                if any(p in error_lower for p in non_transient_patterns):
                    logger.error(f"Failed to pull image {image} (non-transient): {error_msg}")
                    return False, error_msg

                if attempt < max_retries - 1:
                    retry_delay = base_retry_delay * (2 ** attempt)
                    logger.warning(f"Pull attempt {attempt + 1}/{max_retries} failed, retrying in {retry_delay}s: {error_msg}")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"Failed to pull image {image} after {max_retries} attempts: {error_msg}")
                    return False, error_msg

            # 先 chmod 再 rename：rename 保留权限，若在两者之间断电，
            # .sif 会以 umask 默认权限（可能是 0600）落盘，其他 runner 无法读取
            os.chmod(pull_dest, 0o644)
            os.rename(pull_dest, sif_path)

            elapsed = time.time() - start_time
            logger.info(f"Image ready: {sif_path} ({elapsed:.1f}s)")
            return True, None

    async def _resolve_default_branch(self, repo_url: str)-> Optional[str]:
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

    async def _resolve_default_branch_cached(self, repo_url: str)-> Optional[str]:
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

    def _cache_fetch_ts_path(self, cache_path: str)-> str:
        return os.path.join(cache_path, ".git", CACHE_FETCH_TIMESTAMP_FILENAME)

    def _read_cache_fetch_ts(self, cache_path: str)-> float:
        """读取 cache 上次成功 fetch/clone 的 epoch 秒；缺失或损坏返回 0.0（视为极陈旧）"""
        try:
            with open(self._cache_fetch_ts_path(cache_path)) as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return 0.0

    def _write_cache_fetch_ts(self, cache_path: str)-> None:
        try:
            with open(self._cache_fetch_ts_path(cache_path), "w") as f:
                f.write(str(time.time()))
        except OSError as e:
            logger.warning(f"Failed to write fetch timestamp for {cache_path}: {e}")

    async def _cache_has_commit(self, cache_path: str, sha: str)-> bool:
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

    async def _fetch_in_cache(self, cache_path: str, repo_url: str)-> bool:
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
    )-> Tuple[bool, Optional[str], Optional[str]]:
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
            except Exception as e:
                # 清掉可能的半成品 target_dir，否则入口 `if os.path.exists(target_dir)` 短路条件
                # 会在下次调度时把残留副本误判为"已就绪"，跑在不完整代码上
                shutil.rmtree(target_dir, ignore_errors=True)
                logger.error(f"Failed to copy repo cache: {e}")
                return False, f"copy cache failed: {e}", None

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
            except re.error as e:
                shutil.rmtree(target_dir, ignore_errors=True)
                return False, f"Invalid commit message regex '{pattern_str}': {e}", None

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
                subprocess.run([
                    "setfacl", "-R",
                    "-m", f"u:{runner}:rwx",
                    "-d", "-m", f"u:{default_runner}:rwx",
                    "-d", "-m", f"u:{runner}:rwx",
                    job_working_dir,
                ], check=True, capture_output=True)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.warning(f"setfacl failed: {e}")

        elapsed = time.time() - start_time
        logger.info(f"Repo ready: {target_dir} ({elapsed:.1f}s)")
        return True, resolved_sha, branch


resource_manager = ResourceManager()
