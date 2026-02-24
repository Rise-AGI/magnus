# back_end/server/_magnus_config.py
import sys
import logging
from typing import Set, Type
from library import *


__all__ = [
    "magnus_config",
    "admin_open_ids",
]


logger = logging.getLogger(__name__)


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


def _warn_extra_keys(config: dict, expected_keys: Set[str], path: str)-> None:
    for key in sorted(set(config.keys()) - expected_keys):
        logger.warning(f"⚠️ 配置中存在未识别的键: '{path}.{key}'，可能是拼写错误或已废弃")


def _validate_magnus_config(config: Dict[str, Any])-> None:
    """
    验证 magnus_config 的完整性和类型正确性。
    在服务器启动时调用，快速失败。
    未被声明的键会触发 warning（捕捉拼写错误和废弃残留）。
    """
    # 顶层键
    _check_key(config, "server", dict)
    _check_key(config, "execution", dict)
    _check_key(config, "cluster", dict)
    _warn_extra_keys(config, {"client", "server", "execution", "cluster"}, "config")

    # server 配置
    server = config["server"]
    _check_key(server, "address", str)
    _check_key(server, "front_end_port", int)
    _check_key(server, "back_end_port", int)
    _check_key(server, "root", str)
    _check_key(server, "auth", dict)
    _check_key(server, "github_client", dict)
    _check_key(server, "scheduler", dict)
    _check_key(server, "service_proxy", dict)
    _check_key(server, "file_custody", dict)
    _check_key(server, "explorer", dict)
    _warn_extra_keys(server, {
        "address", "front_end_port", "back_end_port", "root",
        "auth", "github_client", "scheduler", "service_proxy", "file_custody", "explorer",
    }, "server")

    # auth 配置
    auth = server["auth"]
    _check_key(auth, "provider", str)
    if auth["provider"] != "feishu":
        raise NotImplementedError(f"❌ auth.provider '{auth['provider']}' 尚未实现，当前仅支持 'feishu'")
    _check_key(auth, "jwt_signer", dict)
    _check_key(auth, "feishu_client", dict)
    _warn_extra_keys(auth, {"provider", "jwt_signer", "feishu_client"}, "server.auth")

    jwt_signer = auth["jwt_signer"]
    _check_key(jwt_signer, "secret_key", str)
    _check_key(jwt_signer, "algorithm", str)
    _check_key(jwt_signer, "expire_minutes", int)
    _warn_extra_keys(jwt_signer, {"secret_key", "algorithm", "expire_minutes"}, "server.auth.jwt_signer")

    feishu_client = auth["feishu_client"]
    _check_key(feishu_client, "app_id", str)
    _check_key(feishu_client, "app_secret", str)
    _check_key(feishu_client, "admins", list)
    _check_key(feishu_client, "refresh_interval", int)
    _warn_extra_keys(feishu_client, {"app_id", "app_secret", "admins", "refresh_interval"}, "server.auth.feishu_client")

    # github_client 配置
    _check_key(server["github_client"], "token", str)
    _warn_extra_keys(server["github_client"], {"token"}, "server.github_client")

    # scheduler 配置
    scheduler = server["scheduler"]
    _check_key(scheduler, "heartbeat_interval", int)
    _check_key(scheduler, "snapshot_interval", int)
    _warn_extra_keys(scheduler, {"heartbeat_interval", "snapshot_interval"}, "server.scheduler")

    # service_proxy 配置
    service_proxy = server["service_proxy"]
    _check_key(service_proxy, "max_concurrency", int)
    _warn_extra_keys(service_proxy, {"max_concurrency"}, "server.service_proxy")

    # explorer 配置
    explorer = server["explorer"]
    _check_key(explorer, "api_key", str)
    _check_key(explorer, "base_url", str)
    _check_key(explorer, "model_name", str)
    _check_key(explorer, "visual_model_name", str)
    _check_key(explorer, "small_fast_model_name", str)
    _warn_extra_keys(explorer, {
        "api_key", "base_url", "model_name", "visual_model_name", "small_fast_model_name",
    }, "server.explorer")

    # file_custody 配置
    file_custody = server["file_custody"]
    _check_key(file_custody, "max_size", str)
    _check_key(file_custody, "max_file_size", str, nullable=True)
    _check_key(file_custody, "max_processes", int)
    _check_key(file_custody, "default_ttl_minutes", int)
    _check_key(file_custody, "max_ttl_minutes", int)
    _warn_extra_keys(file_custody, {
        "max_size", "max_file_size", "max_processes", "default_ttl_minutes", "max_ttl_minutes",
    }, "server.file_custody")

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
    _warn_extra_keys(execution, {
        "backend", "container_runtime", "spy_gpu_interval", "allow_root", "resource_cache",
    }, "execution")

    resource_cache = execution["resource_cache"]
    _check_key(resource_cache, "container_cache_size", str)
    _check_key(resource_cache, "repo_cache_size", str)
    _warn_extra_keys(resource_cache, {"container_cache_size", "repo_cache_size"}, "execution.resource_cache")

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
    _warn_extra_keys(cluster, {
        "name", "gpus", "max_cpu_count", "max_memory_demand",
        "default_cpu_count", "default_memory_demand", "default_runner",
        "default_container_image", "default_ephemeral_storage", "default_system_entry_command",
    }, "cluster")


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
admin_open_ids: Set[str] = set(magnus_config["server"]["auth"]["feishu_client"]["admins"])