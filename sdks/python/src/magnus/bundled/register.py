# sdks/python/src/magnus/bundled/register.py
import time
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from ..client import strip_imports

logger = logging.getLogger(__name__)

BUNDLED_DIR = Path(__file__).resolve().parent
BLUEPRINTS_DIR = BUNDLED_DIR / "blueprints"
SKILLS_DIR = BUNDLED_DIR / "skills"


def _discover_blueprints() -> List[Tuple[str, Path]]:
    results = []
    if not BLUEPRINTS_DIR.exists():
        return results
    for bp_path in sorted(BLUEPRINTS_DIR.glob("*.py")):
        if bp_path.name.startswith("_") or bp_path.name == "__init__.py":
            continue
        blueprint_id = bp_path.stem
        results.append((blueprint_id, bp_path))
    return results


def register_bundled_blueprints(
    address: str,
    token: str,
    timeout: float = 10.0,
    max_retries: int = 5,
    retry_delay: float = 2.0,
) -> List[Tuple[str, str]]:
    import httpx

    blueprints = _discover_blueprints()
    if not blueprints:
        return []

    headers = {"Authorization": f"Bearer {token}"}
    registered: List[Tuple[str, str]] = []

    for blueprint_id, bp_path in blueprints:
        # Reuse the AST-based stripper from client.py — handles multi-line / parenthesized imports
        code = strip_imports(bp_path.read_text(encoding="utf-8"))

        title = blueprint_id.replace("-", " ").title()
        payload = {
            "id": blueprint_id,
            "title": title,
            "description": f"Bundled blueprint: {blueprint_id}",
            "code": code,
        }

        for attempt in range(max_retries):
            try:
                resp = httpx.post(
                    f"{address}/api/blueprints",
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                if resp.status_code in (200, 201):
                    registered.append((blueprint_id, title))
                    logger.info(f"Registered blueprint: {blueprint_id}")
                    break
                elif resp.status_code == 409:
                    registered.append((blueprint_id, title))
                    break
                else:
                    logger.warning(f"Blueprint {blueprint_id} registration returned {resp.status_code}: {resp.text}")
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.warning(f"Failed to register blueprint {blueprint_id} after {max_retries} retries")

    return registered


def _discover_skills() -> List[Tuple[str, Path]]:
    results = []
    if not SKILLS_DIR.exists():
        return results
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        results.append((skill_dir.name, skill_dir))
    return results


_RESOURCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _collect_skill_files(source: Path) -> Tuple[List[Dict[str, str]], List[Path]]:
    """Returns (text_files, binary_paths). Binary image files are collected separately."""
    text_files: List[Dict[str, str]] = []
    binary_paths: List[Path] = []
    for p in sorted(source.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in _RESOURCE_EXTENSIONS:
            binary_paths.append(p)
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        text_files.append({"path": str(p.relative_to(source)), "content": content})
    return text_files, binary_paths


def register_bundled_skills(
    address: str,
    token: str,
    timeout: float = 10.0,
    max_retries: int = 5,
    retry_delay: float = 2.0,
) -> List[Tuple[str, str]]:
    import httpx

    skills = _discover_skills()
    if not skills:
        return []

    headers = {"Authorization": f"Bearer {token}"}
    registered: List[Tuple[str, str]] = []

    for skill_id, skill_dir in skills:
        text_files, binary_paths = _collect_skill_files(skill_dir)
        if not text_files:
            continue

        title = skill_id.replace("-", " ").title()
        payload = {
            "id": skill_id,
            "title": title,
            "description": f"Bundled skill: {skill_id}",
            "files": text_files,
        }

        for attempt in range(max_retries):
            try:
                resp = httpx.post(
                    f"{address}/api/skills",
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                if resp.status_code in (200, 201):
                    registered.append((skill_id, title))
                    logger.info(f"Registered skill: {skill_id}")
                    break
                elif resp.status_code == 409:
                    registered.append((skill_id, title))
                    break
                else:
                    logger.warning(f"Skill {skill_id} registration returned {resp.status_code}: {resp.text}")
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.warning(f"Failed to register skill {skill_id} after {max_retries} retries")

        # Upload binary resources after text save succeeds
        for bp in binary_paths:
            rel = str(bp.relative_to(skill_dir))
            try:
                with open(bp, "rb") as f:
                    resp = httpx.post(
                        f"{address}/api/skills/{skill_id}/resources",
                        files={"file": (bp.name, f)},
                        headers=headers,
                        timeout=timeout,
                    )
                if resp.status_code in (200, 201):
                    logger.info(f"Uploaded resource {rel} for skill {skill_id}")
                else:
                    logger.warning(f"Resource {rel} upload returned {resp.status_code}")
            except (httpx.ConnectError, httpx.TimeoutException):
                logger.warning(f"Failed to upload resource {rel} for skill {skill_id}")

    return registered
