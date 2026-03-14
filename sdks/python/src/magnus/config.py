# sdks/python/src/magnus/config.py
import json
import logging
from typing import Any, Dict, Optional
from pathlib import Path

# DEFAULT_ADDRESS = "https://magnus.pkuplasma.com"
DEFAULT_ADDRESS = "http://127.0.0.1:8017"
DEFAULT_TOKEN = "local"
ENV_MAGNUS_TOKEN = "MAGNUS_TOKEN"
ENV_MAGNUS_ADDRESS = "MAGNUS_ADDRESS"
CONFIG_DIR = Path.home() / ".magnus"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_VERSION = 2
RESERVED_SITE_NAME = "default"

logger = logging.getLogger("magnus")


def _looks_like_ip(host: str) -> bool:
    """粗判 host 部分是否为 IP 地址（含端口）。"""
    bare = host.split(":")[0]
    return all(ch in "0123456789." for ch in bare) and bare.count(".") >= 1


def normalize_address(address: str) -> str:
    if not address:
        raise ValueError("Address cannot be empty.")

    address = address.strip().rstrip("/")

    # 拒绝非 HTTP 协议（tcp://, ftp://, ws:// 等）
    if "://" in address and not address.startswith(("http://", "https://")):
        raise ValueError(f"Unsupported protocol: '{address}'. Use http:// or https://")

    if not address.startswith(("http://", "https://")):
        # IP 地址优先 http（内网），域名优先 https
        scheme = "http" if _looks_like_ip(address) else "https"
        address = f"{scheme}://{address}"

    return address


def _empty_config() -> Dict[str, Any]:
    return {"version": CONFIG_VERSION, "current": None, "sites": {}}


def _load_config() -> Dict[str, Any]:
    try:
        if CONFIG_FILE.is_file():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if (
                isinstance(data, dict)
                and data.get("version") == CONFIG_VERSION
                and isinstance(data.get("sites"), dict)
            ):
                return data
            logger.warning(
                f"~/.magnus/config.json was reset (format mismatch). "
                f"Run `magnus login` to reconfigure."
            )
            CONFIG_FILE.unlink(missing_ok=True)
    except Exception:
        logger.warning(
            f"~/.magnus/config.json was reset (corrupted). "
            f"Run `magnus login` to reconfigure."
        )
        try:
            CONFIG_FILE.unlink(missing_ok=True)
        except Exception:
            pass
    return _empty_config()


def _save_config(config: Dict[str, Any]) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return CONFIG_FILE


def _get_current_site() -> Dict[str, str]:
    config = _load_config()
    current = config.get("current")
    if current and current in config.get("sites", {}):
        return config["sites"][current]
    return {}


def save_site(
    name: str,
    address: str,
    token: str,
    set_current: bool = True,
) -> Path:
    config = _load_config()
    config["sites"][name] = {"address": address, "token": token}
    if set_current:
        config["current"] = name
    return _save_config(config)


def remove_site(name: str) -> str:
    config = _load_config()
    config["sites"].pop(name, None)
    if config.get("current") == name:
        remaining = sorted(config["sites"].keys())
        config["current"] = remaining[0] if remaining else None
    _save_config(config)
    return config["current"] or RESERVED_SITE_NAME


def set_current_site(name: Optional[str]) -> Path:
    config = _load_config()
    config["current"] = name
    return _save_config(config)
