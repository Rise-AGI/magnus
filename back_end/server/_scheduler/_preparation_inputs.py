"""Detached inputs shared by repository and environment preparation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreparationInputs:
    container_image: str | None
    dockerfile: str | None
    runner: str
    namespace: str
    repo_name: str
    branch: str | None
    commit_sha: str | None
    user_id: str | None
