# sdks/python/src/magnus/bundled/register.py
import time
import logging
from pathlib import Path
from typing import List, Tuple

from ..client import strip_imports

logger = logging.getLogger(__name__)

BUNDLED_DIR = Path(__file__).resolve().parent
BLUEPRINTS_DIR = BUNDLED_DIR / "blueprints"


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
) -> int:
    import httpx

    blueprints = _discover_blueprints()
    if not blueprints:
        return 0

    headers = {"Authorization": f"Bearer {token}"}
    registered = 0

    for blueprint_id, bp_path in blueprints:
        # Reuse the AST-based stripper from client.py — handles multi-line / parenthesized imports
        code = strip_imports(bp_path.read_text(encoding="utf-8"))

        payload = {
            "id": blueprint_id,
            "title": blueprint_id.replace("-", " ").title(),
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
                    registered += 1
                    logger.info(f"Registered blueprint: {blueprint_id}")
                    break
                elif resp.status_code == 409:
                    registered += 1
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
