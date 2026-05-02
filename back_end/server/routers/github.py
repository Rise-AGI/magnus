# back_end/server/routers/github.py
import os
import re
import shutil
import asyncio
import tempfile
import logging
from typing import List, Dict, Any, Awaitable, Callable, TypeVar
from fastapi import APIRouter, HTTPException, Depends, Query

from .. import models
from .auth import get_current_user
from .._magnus_config import is_local_mode


router = APIRouter()
logger = logging.getLogger(__name__)

# git subprocess 使用的干净环境（与 _resource_manager.py 保持一致）
_git_env = os.environ.copy()
_git_env["GIT_TERMINAL_PROMPT"] = "0"
for _k in ("GIT_ASKPASS", "SSH_ASKPASS"):
    _git_env.pop(_k, None)

_GIT_LS_REMOTE_TIMEOUT = 15
_GIT_CLONE_TIMEOUT = 30

# in-flight 去重：同 key 的并发请求共享同一个 Future（同一次 git 进程的结果）。
# 失败也合并以避免雪崩；首个 Future settle 后立刻从 dict 移除（finally），
# 后续请求会重新 spawn —— 不是缓存，不影响数据新鲜度。
_T = TypeVar("_T")
_inflight: Dict[str, "asyncio.Future[Any]"] = {}


async def _dedup(key: str, factory: Callable[[], Awaitable[_T]]) -> _T:
    existing = _inflight.get(key)
    if existing is not None:
        return await existing
    loop = asyncio.get_running_loop()
    fut: "asyncio.Future[_T]" = loop.create_future()
    _inflight[key] = fut
    try:
        result = await factory()
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    else:
        if not fut.done():
            fut.set_result(result)
        return result
    finally:
        if _inflight.get(key) is fut:
            _inflight.pop(key, None)


# 上游代理（GHE / Cloudflare 等）经常瞬态抖动（429 / 522 / Connection refused）。
# git stderr 可能包含 "error: 429"、"The requested URL returned error: 522"
# 或网络层短语。下面把它们映射为精确的 HTTP 语义和 reason 常量，
# 让前端能给出针对性的 i18n 文案。
_HTTP_CODE_RE = re.compile(r"error:\s*(\d{3})", re.IGNORECASE)


def _classify_git_error(stderr: str) -> tuple[int, str]:
    low = stderr.lower()

    # 上游 HTTP 状态码（git 把 HTTP 错误透传成 "error: NNN"）
    m = _HTTP_CODE_RE.search(low)
    if m:
        upstream = int(m.group(1))
        if upstream == 429:
            return 429, "upstream_rate_limited"
        if upstream == 404:
            return 404, "repo_not_found"
        if upstream == 403:
            return 403, "permission_denied"
        if 500 <= upstream < 600:
            return 504, "upstream_unreachable"

    if "rate limit" in low:
        return 429, "upstream_rate_limited"
    if "timed out" in low or "timeout" in low:
        return 504, "upstream_timeout"
    if (
        "connection refused" in low
        or "could not resolve host" in low
        or "connection reset" in low
        or "network is unreachable" in low
        or "temporary failure in name resolution" in low
    ):
        return 504, "upstream_unreachable"

    if "not found" in low or "does not exist" in low or "no such" in low:
        return 404, "repo_not_found"
    if "permission denied" in low or "access denied" in low or "could not read" in low:
        return 403, "permission_denied"
    return 502, "git_error"


def _repo_url(ns: str, repo: str) -> str:
    if is_local_mode:
        return f"https://github.com/{ns}/{repo}.git"
    return f"git@github.com:{ns}/{repo}.git"


async def _git_ls_remote_branches(ns: str, repo: str) -> List[Dict[str, str]]:
    """git ls-remote --heads -> [{"name": "main", "commit_sha": "abc..."}, ...]"""
    url = _repo_url(ns, repo)
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-remote", "--heads", url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_git_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GIT_LS_REMOTE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="upstream_timeout")

    if proc.returncode != 0:
        error = stderr.decode().strip()
        logger.warning(f"git ls-remote --heads failed for {ns}/{repo}: {error}")
        code, reason = _classify_git_error(error)
        raise HTTPException(status_code=code, detail=reason)

    branches = []
    for line in stdout.decode().strip().splitlines():
        if not line:
            continue
        sha, ref = line.split(None, 1)
        name = ref.replace("refs/heads/", "")
        branches.append({"name": name, "commit_sha": sha})

    return branches


async def _git_fetch_commits(
    ns: str,
    repo: str,
    branch: str,
    per_page: int,
) -> List[Dict[str, Any]]:
    """
    git clone --bare --single-branch --depth N -> git log
    用临时浅 bare clone 获取 commit 历史，完成后清理。
    bare clone 只拉 git 对象（~KB 级），不拉工作树文件。
    """
    url = _repo_url(ns, repo)
    tmp_parent = tempfile.mkdtemp(prefix="magnus_git_commits_")
    tmp_repo = os.path.join(tmp_parent, "repo.git")

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--bare", "--single-branch", "--branch", branch,
            "--depth", str(per_page), url, tmp_repo,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=_git_env,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GIT_CLONE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail="upstream_timeout")

        if proc.returncode != 0:
            error = stderr.decode().strip()
            logger.warning(f"git clone --bare failed for {ns}/{repo}#{branch}: {error}")
            code, reason = _classify_git_error(error)
            raise HTTPException(status_code=code, detail=reason)

        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--format=%H%n%s%n%an%n%aI", "-n", str(per_page),
            cwd=tmp_repo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_git_env,
        )
        stdout, _ = await proc.communicate()

        commits = []
        lines = stdout.decode().strip().splitlines()
        # 每 4 行一组：sha, message, author, date
        for i in range(0, len(lines) - 3, 4):
            commits.append({
                "sha": lines[i],
                "message": lines[i + 1],
                "author": lines[i + 2],
                "date": lines[i + 3],
            })
        return commits

    finally:
        shutil.rmtree(tmp_parent, ignore_errors=True)


@router.get("/github/{ns}/{repo}/branches")
async def get_branches(
    ns: str,
    repo: str,
    _: models.User = Depends(get_current_user),
):
    branches = await _dedup(
        f"branches:{ns}/{repo}",
        lambda: _git_ls_remote_branches(ns, repo),
    )
    if not branches:
        raise HTTPException(
            status_code=404,
            detail="repo_not_found",
        )
    return branches


@router.get("/github/{ns}/{repo}/commits")
async def get_commits(
    ns: str,
    repo: str,
    branch: str = Query(...),
    _: models.User = Depends(get_current_user),
):
    return await _dedup(
        f"commits:{ns}/{repo}/{branch}",
        lambda: _git_fetch_commits(ns, repo, branch, per_page=10),
    )
