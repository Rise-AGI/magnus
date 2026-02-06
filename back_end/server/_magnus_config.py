# back_end/server/_magnus_config.py
import sys
from typing import Type
from library import *


__all__ = [
    "magnus_config",
]


def _check_key(config: dict, key: str, expected_type: Type) -> None:
    """
    检查配置字典中的键是否存在且类型正确。
    快速失败：启动时立即发现配置问题，而非运行时模糊错误。

    Args:
        config: 配置字典
        key: 要检查的键名
        expected_type: 期望的值类型

    Raises:
        KeyError: 键不存在
        TypeError: 值类型不匹配
    """
    if key not in config:
        raise KeyError(f"❌ 配置缺少必需的键: '{key}'")

    value = config[key]
    if not isinstance(value, expected_type):
        raise TypeError(
            f"❌ 配置键 '{key}' 类型错误: 期望 {expected_type.__name__}, 实际 {type(value).__name__}"
        )


def _validate_magnus_config(config: Dict[str, Any]) -> None:
    """
    验证 magnus_config 的完整性和类型正确性。
    在服务器启动时调用，快速失败。
    """
    # 顶层键
    _check_key(config, "server", dict)
    _check_key(config, "cluster", dict)

    # server 配置
    server = config["server"]
    _check_key(server, "front_end_port", int)
    _check_key(server, "back_end_port", int)
    _check_key(server, "root", str)
    _check_key(server, "resource_cache", dict)
    _check_key(server["resource_cache"], "container_cache_size", str)
    _check_key(server["resource_cache"], "repo_cache_size", str)

    # explorer 配置
    _check_key(server, "explorer", dict)
    explorer = server["explorer"]
    _check_key(explorer, "api_key", str)
    _check_key(explorer, "base_url", str)
    _check_key(explorer, "model_name", str)
    _check_key(explorer, "visual_model_name", str)
    _check_key(explorer, "small_fast_model_name", str)

    # cluster 配置
    cluster = config["cluster"]
    _check_key(cluster, "name", str)
    _check_key(cluster, "gpus", list)
    _check_key(cluster, "default_runner", str)
    _check_key(cluster, "default_container_image", str)
    _check_key(cluster, "default_system_entry_command", str)


def _load_magnus_config() -> Dict[str, Any]:

    magnus_project_root = Path(__file__).resolve().parent.parent.parent
    magnus_config_path = magnus_project_root / "configs" / "magnus_config.yaml"

    if not magnus_config_path.exists():
        raise FileNotFoundError(f"❌ 配置文件未找到: {magnus_config_path}")

    try:
        data = load_from_yaml(str(magnus_config_path))
        if "--deliver" not in sys.argv:
            data["server"]["front_end_port"] += 2
            data["server"]["back_end_port"] += 2
            data["server"]["root"] += "-develop"

        # 快速失败：启动时验证配置完整性
        _validate_magnus_config(data)

        return data
    except Exception as error:
        raise RuntimeError(f"❌ 解析 YAML 失败: {error}\n调用栈：\n{traceback.format_exc()}")


magnus_config = _load_magnus_config()