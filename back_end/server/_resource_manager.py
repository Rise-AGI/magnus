# back_end/server/_resource_manager.py
import os
import re
import time
import shutil
import asyncio
import logging
import subprocess
from typing import Dict, List, Optional, Tuple
from pywheels.file_tools import guarantee_file_exist
from ._magnus_config import magnus_config, is_local_mode


__all__ = ["resource_manager"]


logger = logging.getLogger(__name__)


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
        docker_image = re.sub(r'^[a-z]+://', '', image)

        if not force:
            # 检查本地是否已有
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", docker_image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return True, None

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
                    "apptainer", "pull", pull_dest, image,
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

        # branch=None: 解析仓库默认分支
        if branch is None:
            branch = await self._resolve_default_branch(repo_url)
            if branch is None:
                return False, "Failed to determine default branch", None

        cache_path = self._get_repo_cache_path(namespace, repo_name, branch)
        cache_key = f"{namespace}/{repo_name}/{branch}"

        # Phase 1: 确保 cache 存在（带锁）
        if cache_key not in self.repo_locks:
            self.repo_locks[cache_key] = asyncio.Lock()

        async with self.repo_locks[cache_key]:
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

            # 更新 cache 访问时间
            try:
                os.utime(cache_path, None)
            except OSError:
                pass

        # Phase 2: 复制 cache 到工作目录（放到线程池避免阻塞 event loop）
        start_time = time.time()
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, shutil.copytree, cache_path, target_dir)
        except Exception as e:
            logger.error(f"Failed to copy repo cache: {e}")
            return False, f"copy cache failed: {e}", None

        # Phase 3: fetch + checkout 到指定 commit
        proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin",
            cwd = target_dir,
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.PIPE,
            env = _git_env,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"git fetch failed (may be ok): {stderr.decode().strip()}")

        # 解析 commit_sha 语义：
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
