# back_end/server/_magnus_config.py
import sys
import logging
from typing import Set, Type
from library import *


__all__ = [
    "magnus_config",
    "admin_open_ids",
    "is_local_mode",
    "is_admin_user",
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


def _prepare_and_validate_magnus_config(config: Dict[str, Any])-> None:
    """
    验证 magnus_config 的完整性和类型正确性。
    在服务器启动时调用，快速失败。
    未被声明的键会触发 warning（捕捉拼写错误和废弃残留）。

    Binary choice: backend 只能是 "slurm" (HPC) 或 "local" (Docker)，不允许交叉配置。
    """
    # 顶层键
    _check_key(config, "server", dict)
    _check_key(config, "execution", dict)
    _warn_extra_keys(config, {"client", "server", "execution", "cluster"}, "config")

    # execution 配置 (先验证，决定后续验证路径)
    execution = config["execution"]
    _check_key(execution, "backend", str)
    backend = execution["backend"]
    if backend not in ("slurm", "local"):
        raise ValueError(f"❌ execution.backend 必须是 'slurm' 或 'local'，当前值: '{backend}'")

    is_local = backend == "local"

    # server 配置
    server = config["server"]
    _check_key(server, "address", str)
    _check_key(server, "front_end_port", int)
    _check_key(server, "back_end_port", int)
    _check_key(server, "root", str)
    _check_key(server, "cors_origins", list)
    _check_key(server, "database", dict)
    _check_key(server, "auth", dict)
    _check_key(server, "scheduler", dict)
    _check_key(server, "service_proxy", dict)
    _check_key(server, "file_custody", dict)

    expected_server_keys = {
        "address", "front_end_port", "back_end_port", "root",
        "database", "auth", "scheduler", "service_proxy", "file_custody",
        "cors_origins",
    }
    if not is_local:
        _check_key(server, "github_client", dict)
        _check_key(server, "explorer", dict)
        expected_server_keys |= {"github_client", "explorer"}
    else:
        # local 模式下这些是可选的
        expected_server_keys |= {"github_client", "explorer"}
    _warn_extra_keys(server, expected_server_keys, "server")

    # database 配置
    database = server["database"]
    _check_key(database, "pool_size", int)
    _check_key(database, "max_overflow", int)
    _check_key(database, "pool_timeout", int)
    _check_key(database, "pool_recycle", int)
    _warn_extra_keys(database, {"pool_size", "max_overflow", "pool_timeout", "pool_recycle"}, "server.database")

    # auth 配置
    auth = server["auth"]
    _check_key(auth, "provider", str)
    auth_provider = auth["provider"]

    if is_local:
        if auth_provider != "local":
            raise ValueError(f"❌ execution.backend='local' 要求 auth.provider='local'，当前值: '{auth_provider}'")
        _check_key(auth, "jwt_signer", dict)
        _warn_extra_keys(auth, {"provider", "jwt_signer"}, "server.auth")
    else:
        if auth_provider != "feishu":
            raise NotImplementedError(f"❌ auth.provider '{auth_provider}' 尚未实现，HPC 模式仅支持 'feishu'")
        _check_key(auth, "jwt_signer", dict)
        _check_key(auth, "feishu_client", dict)
        _warn_extra_keys(auth, {"provider", "jwt_signer", "feishu_client"}, "server.auth")

        feishu_client = auth["feishu_client"]
        _check_key(feishu_client, "app_id", str)
        _check_key(feishu_client, "app_secret", str)
        _check_key(feishu_client, "admins", list)
        _check_key(feishu_client, "refresh_interval", int)
        _warn_extra_keys(feishu_client, {"app_id", "app_secret", "admins", "refresh_interval"}, "server.auth.feishu_client")

    jwt_signer = auth["jwt_signer"]
    _check_key(jwt_signer, "secret_key", str)
    _check_key(jwt_signer, "algorithm", str)
    _check_key(jwt_signer, "expire_minutes", int)
    _warn_extra_keys(jwt_signer, {"secret_key", "algorithm", "expire_minutes"}, "server.auth.jwt_signer")

    # github_client 配置 (HPC 必须, local 可选)
    if not is_local:
        _check_key(server["github_client"], "token", str)
        _warn_extra_keys(server["github_client"], {"token"}, "server.github_client")

    # scheduler 配置
    scheduler_cfg = server["scheduler"]
    _check_key(scheduler_cfg, "heartbeat_interval", int)
    _check_key(scheduler_cfg, "snapshot_interval", int)
    _warn_extra_keys(scheduler_cfg, {"heartbeat_interval", "snapshot_interval"}, "server.scheduler")

    # service_proxy 配置
    service_proxy = server["service_proxy"]
    _check_key(service_proxy, "max_concurrency", int)
    _warn_extra_keys(service_proxy, {"max_concurrency"}, "server.service_proxy")

    # explorer 配置 (HPC 必须, local 可选；只要提供了就验证完整性)
    if not is_local:
        _check_key(server, "explorer", dict)
    if "explorer" in server:
        explorer = server["explorer"]
        _check_key(explorer, "api_key", str)
        _check_key(explorer, "base_url", str)
        _check_key(explorer, "model_name", str)
        _check_key(explorer, "visual_model_name", str)
        _check_key(explorer, "small_fast_model_name", str)
        _check_key(explorer, "stt_model_name", str)
        _warn_extra_keys(explorer, {
            "api_key", "base_url", "model_name", "visual_model_name", "small_fast_model_name", "stt_model_name",
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

    # execution 配置 (续)
    if is_local:
        # local 模式：container_runtime 固定为 docker，allow_root / resource_cache 可选
        expected_exec_keys = {"backend", "container_runtime", "allow_root", "resource_cache"}
        # 提供默认值
        execution.setdefault("container_runtime", "docker")
        execution.setdefault("allow_root", True)
        execution.setdefault("resource_cache", {"container_cache_size": "80G", "repo_cache_size": "20G"})
        if execution["container_runtime"] != "docker":
            raise ValueError(f"❌ execution.backend='local' 要求 container_runtime='docker'，当前值: '{execution['container_runtime']}'")
    else:
        _check_key(execution, "container_runtime", str)
        if execution["container_runtime"] != "apptainer":
            raise NotImplementedError(f"❌ execution.container_runtime '{execution['container_runtime']}' 尚未实现，HPC 模式仅支持 'apptainer'")
        _check_key(execution, "allow_root", bool)
        _check_key(execution, "resource_cache", dict)
        expected_exec_keys = {"backend", "container_runtime", "allow_root", "resource_cache"}

    _warn_extra_keys(execution, expected_exec_keys, "execution")

    resource_cache = execution["resource_cache"]
    _check_key(resource_cache, "container_cache_size", str)
    _check_key(resource_cache, "repo_cache_size", str)
    _warn_extra_keys(resource_cache, {"container_cache_size", "repo_cache_size"}, "execution.resource_cache")

    # cluster 配置
    if is_local:
        # local 模式下 cluster 可选，提供合理默认值
        import getpass
        config.setdefault("cluster", {})
        cluster = config["cluster"]
        cluster.setdefault("name", "Local")
        cluster.setdefault("gpus", [])
        cluster.setdefault("max_cpu_count", 128)
        cluster.setdefault("max_memory_demand", "256G")
        cluster.setdefault("default_cpu_count", 4)
        cluster.setdefault("default_memory_demand", "4G")
        cluster.setdefault("default_runner", getpass.getuser())
        cluster.setdefault("default_container_image", "docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
        cluster.setdefault("default_ephemeral_storage", "10G")
        cluster.setdefault("default_system_entry_command", "")
    else:
        _check_key(config, "cluster", dict)
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

    # 支持 --config 参数指定配置文件路径（local 模式使用）
    magnus_config_path = None
    for i, arg in enumerate(sys.argv):
        if arg == "--config" and i + 1 < len(sys.argv):
            magnus_config_path = Path(sys.argv[i + 1])
            break

    if magnus_config_path is None:
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
        _prepare_and_validate_magnus_config(data)

        return data
    except Exception as error:
        raise RuntimeError(f"❌ 解析 YAML 失败: {error}\n调用栈：\n{traceback.format_exc()}")


magnus_config = _load_magnus_config()
is_local_mode = magnus_config["execution"]["backend"] == "local"

admin_open_ids: Set[str]
if is_local_mode:
    admin_open_ids = set()
else:
    admin_open_ids = set(magnus_config["server"]["auth"]["feishu_client"]["admins"])


def is_admin_user(user) -> bool:
    """本地模式下所有用户都是 admin；HPC 模式下检查 feishu_open_id。"""
    if is_local_mode:
        return True
    return user.feishu_open_id in admin_open_ids