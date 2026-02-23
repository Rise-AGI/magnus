# back_end/server/_magnus_config.py
import sys
from typing import Type
from library import *


__all__ = [
    "magnus_config",
]


def _check_key(config: dict, key: str, expected_type: Type, nullable: bool = False)-> None:
    if key not in config:
        raise KeyError(f"❌ 配置缺少必需的键: '{key}'")

    value = config[key]
    if nullable and value is None:
        return
    if not isinstance(value, expected_type):
        expected = f"{expected_type.__name__} 或 None" if nullable else expected_type.__name__
        raise TypeError(
            f"❌ 配置键 '{key}' 类型错误: 期望 {expected}, 实际 {type(value).__name__}"
        )


def _validate_magnus_config(config: Dict[str, Any])-> None:
    """
    验证 magnus_config 的完整性和类型正确性。
    在服务器启动时调用，快速失败。
    """
    # 顶层键
    _check_key(config, "server", dict)
    _check_key(config, "execution", dict)
    _check_key(config, "cluster", dict)

    # server 配置
    server = config["server"]
    _check_key(server, "address", str)
    _check_key(server, "front_end_port", int)
    _check_key(server, "back_end_port", int)
    _check_key(server, "root", str)

    # auth 配置
    _check_key(server, "auth", dict)
    auth = server["auth"]
    _check_key(auth, "provider", str)
    if auth["provider"] != "feishu":
        raise NotImplementedError(f"❌ auth.provider '{auth['provider']}' 尚未实现，当前仅支持 'feishu'")

    _check_key(auth, "jwt_signer", dict)
    jwt_signer = auth["jwt_signer"]
    _check_key(jwt_signer, "secret_key", str)
    _check_key(jwt_signer, "algorithm", str)
    _check_key(jwt_signer, "expire_minutes", int)

    _check_key(auth, "feishu_client", dict)
    feishu_client = auth["feishu_client"]
    _check_key(feishu_client, "app_id", str)
    _check_key(feishu_client, "app_secret", str)

    # github_client 配置
    _check_key(server, "github_client", dict)
    _check_key(server["github_client"], "token", str)

    # scheduler 配置
    _check_key(server, "scheduler", dict)
    scheduler = server["scheduler"]
    _check_key(scheduler, "heartbeat_interval", int)
    _check_key(scheduler, "snapshot_interval", int)

    # explorer 配置
    _check_key(server, "explorer", dict)
    explorer = server["explorer"]
    _check_key(explorer, "api_key", str)
    _check_key(explorer, "base_url", str)
    _check_key(explorer, "model_name", str)
    _check_key(explorer, "visual_model_name", str)
    _check_key(explorer, "small_fast_model_name", str)

    # file_custody 配置
    _check_key(server, "file_custody", dict)
    file_custody = server["file_custody"]
    _check_key(file_custody, "max_size", str)
    _check_key(file_custody, "max_file_size", str, nullable=True)
    _check_key(file_custody, "max_processes", int)
    _check_key(file_custody, "default_ttl_minutes", int)
    _check_key(file_custody, "max_ttl_minutes", int)

    # execution 配置
    execution = config["execution"]
    _check_key(execution, "backend", str)
    if execution["backend"] != "slurm":
        raise NotImplementedError(f"❌ execution.backend '{execution['backend']}' 尚未实现，当前仅支持 'slurm'")
    _check_key(execution, "container_runtime", str)
    if execution["container_runtime"] != "apptainer":
        raise NotImplementedError(f"❌ execution.container_runtime '{execution['container_runtime']}' 尚未实现，当前仅支持 'apptainer'")
    _check_key(execution, "spy_gpu_interval", int)
    _check_key(execution, "allow_root", bool)
    _check_key(execution, "resource_cache", dict)
    _check_key(execution["resource_cache"], "container_cache_size", str)
    _check_key(execution["resource_cache"], "repo_cache_size", str)

    # cluster 配置
    cluster = config["cluster"]
    _check_key(cluster, "name", str)
    _check_key(cluster, "gpus", list)
    _check_key(cluster, "max_cpu_count", int)
    _check_key(cluster, "max_memory_demand", str)
    _check_key(cluster, "default_cpu_count", int)
    _check_key(cluster, "default_memory_demand", str)
    _check_key(cluster, "default_runner", str)
    _check_key(cluster, "default_container_image", str)
    _check_key(cluster, "default_ephemeral_storage", str)
    _check_key(cluster, "default_system_entry_command", str)


def _load_magnus_config()-> Dict[str, Any]:

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