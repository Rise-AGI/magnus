# back_end/server/routers/github.py
import os
import shutil
import asyncio
import tempfile
import logging
from typing import List, Dict, Any
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

_GIT_TIMEOUT = 15


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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="git ls-remote timed out")

    if proc.returncode != 0:
        error = stderr.decode().strip()
        logger.warning(f"git ls-remote --heads failed for {ns}/{repo}: {error}")
        raise HTTPException(status_code=502, detail=f"git ls-remote failed: {error}")

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
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail="git clone timed out")

        if proc.returncode != 0:
            error = stderr.decode().strip()
            logger.warning(f"git clone --bare failed for {ns}/{repo}#{branch}: {error}")
            raise HTTPException(status_code=502, detail=f"git clone failed: {error}")

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
    branches = await _git_ls_remote_branches(ns, repo)
    if not branches:
        raise HTTPException(
            status_code=404,
            detail="Repo not found or empty",
        )
    return branches


@router.get("/github/{ns}/{repo}/commits")
async def get_commits(
    ns: str,
    repo: str,
    branch: str = Query(...),
    _: models.User = Depends(get_current_user),
):
    return await _git_fetch_commits(ns, repo, branch, per_page=10)
