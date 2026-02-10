# back_end/server/routers/github.py
from fastapi import APIRouter, HTTPException, Depends

from .. import models
from .._github_client import github_client
from .auth import get_current_user


router = APIRouter()


@router.get("/github/{ns}/{repo}/branches")
async def get_branches(
    ns: str,
    repo: str,
    _: models.User = Depends(get_current_user),
):
    branches = await github_client.fetch_branches(ns, repo)
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
    branch: str,
    _: models.User = Depends(get_current_user),
):
    return await github_client.fetch_commits(ns, repo, branch)