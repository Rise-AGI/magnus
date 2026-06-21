# back_end/server/_magnus_config.py
import sys
import logging
from typing import Set, Type
from library import *
from ._size_utils import _parse_size_string, effective_cpu_count_per_cpu


__all__ = [
    "magnus_config",
    "admin_open_ids",
    "is_local_mode",
    "is_local_auth",
    "is_admin_user",
    "apply_cluster_defaults",
    "normalize_per_cpu_resources",
    "validate_cluster_limits",
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


def _check_one_secret_source(config: dict, inline_key: str, file_key: str, path: str)-> None:
    """密钥必须恰好以"内联值"或"文件路径"之一提供（互斥）。给出文件路径时要求文件已
    存在 —— fail-fast，免得留到首个 socket 重建时才炸。"""
    has_inline = config.get(inline_key) is not None
    has_file = config.get(file_key) is not None
    if has_inline == has_file:
        raise ValueError(
            f"❌ {path} 要求 '{inline_key}'（内联值）与 '{file_key}'"
            "（chmod 600 密钥文件路径）恰好提供一个"
        )
    if has_file:
        _check_key(config, file_key, str)
        if not Path(config[file_key]).is_file():
            raise ValueError(f"❌ {path}.{file_key} 指向的密钥文件不存在: {config[file_key]}")
    else:
        _check_key(config, inline_key, str)


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
    _warn_extra_keys(config, {"client", "server", "execution", "cluster", "transport"}, "config")

    # execution 配置 (先验证，决定后续验证路径)
    execution = config["execution"]
    _check_key(execution, "backend", str)
    backend = execution["backend"]
    if backend not in ("slurm", "local"):
        raise ValueError(f"❌ execution.backend 必须是 'slurm' 或 'local'，当前值: '{backend}'")

    is_local = backend == "local"

    # transport：magnus 执行 SLURM CLI（及后续跨界文件搬运）的位置。
    # local = 本机 subprocess（magnus 与 SLURM controller 同机，自有/独占集群现状）；
    # ssh = 经已建立的 SSH ControlMaster socket 驱动远程站点（共享集群租户场景：在唯一可达的
    # 控制机上骑 socket 跑命令）。default local 保持现状，现有站点无需配置即字节级等价。
    config.setdefault("transport", {"mode": "local"})
    transport = config["transport"]
    _check_key(transport, "mode", str)
    transport_mode = transport["mode"]
    if transport_mode not in ("local", "ssh"):
        raise ValueError(f"❌ transport.mode 必须是 'local' 或 'ssh'，当前值: '{transport_mode}'")
    if transport_mode == "ssh":
        _check_key(transport, "ssh", dict)
        ssh = transport["ssh"]
        _check_key(ssh, "control_path", str)
        _check_key(ssh, "host", str)
        _check_key(ssh, "user", str)
        # remote_root：远程站点上 magnus 搬运 job 工作区 / wrapper / 产物的根目录
        # （典型落远端共享盘，如 Lustre）。transport=ssh 意味着 SLURM 在异机、与 magnus 无
        # 共享盘，job 工作区必须落远端，故 remote_root 必填 —— 启动即 fail-fast，
        # 不留到首个 job 提交时才炸。
        ssh.setdefault("remote_root", None)
        _check_key(ssh, "remote_root", str, nullable=True)
        if ssh["remote_root"] is None:
            raise ValueError(
                "❌ transport.mode='ssh' 要求 transport.ssh.remote_root"
                "（远程站点 job 工作区根目录），当前为 None"
            )
        # resource_staging：SIF 镜像 / git 仓库怎么落到远端站点。
        # - relay：控制机（已在镜像反代白名单内）本地拉取后经 transport 推到远端，
        #   远端站点不需要能直连镜像反代。
        # - remote：远端站点自己拉（需先把站点出网 IP 加进镜像反代白名单）—— 接线
        #   待白名单落地，当前 gate 住。
        ssh.setdefault("resource_staging", "relay")
        _check_key(ssh, "resource_staging", str)
        if ssh["resource_staging"] not in ("relay", "remote"):
            raise ValueError(
                "❌ transport.ssh.resource_staging 必须是 'relay' 或 'remote'，"
                f"当前值: '{ssh['resource_staging']}'"
            )
        if ssh["resource_staging"] == "remote":
            raise ValueError(
                "❌ transport.ssh.resource_staging='remote'（远端站点自拉镜像/仓库）"
                "尚未接线，需先把站点出网 IP 加入镜像反代白名单；当前请用 'relay'"
                "（控制机拉取后推送到远端）"
            )
        # auto_connect（可选）：配了就让 transport 在每次跨界操作前确保 ControlMaster
        # socket 活着、失效（ControlPersist 过期 / 控制机重启）时用账号持有人自己的登录
        # 密码 + TOTP 种子（标准 RFC 6238）无人值守重建 —— 把人肉一次的 2FA 登录自动化。
        # 缺省 None：保持现状（socket 需人肉建、失效即快失败）。通用能力，任何"密码 +
        # TOTP"两段 keyboard-interactive 认证的 SSH 主机皆可用。
        ssh.setdefault("auto_connect", None)
        if ssh["auto_connect"] is not None:
            _check_key(ssh, "auto_connect", dict)
            auto_connect = ssh["auto_connect"]
            # 密钥（登录密码、TOTP 种子）各自内联值或 chmod 600 文件路径二选一。
            _check_one_secret_source(
                auto_connect, "password", "password_file", "transport.ssh.auto_connect",
            )
            _check_one_secret_source(
                auto_connect, "totp_secret", "totp_secret_file", "transport.ssh.auto_connect",
            )
            # control_persist：重建时 master 的存活时长；prompt 正则：站点提示词措辞与
            # 默认不符时覆盖（None 即用内置默认，见 _ssh_auto_connect）。
            auto_connect.setdefault("control_persist", "8h")
            auto_connect.setdefault("password_prompt", None)
            auto_connect.setdefault("totp_prompt", None)
            _check_key(auto_connect, "control_persist", str)
            _check_key(auto_connect, "password_prompt", str, nullable=True)
            _check_key(auto_connect, "totp_prompt", str, nullable=True)
            _warn_extra_keys(
                auto_connect,
                {
                    "password", "password_file",
                    "totp_secret", "totp_secret_file",
                    "control_persist", "password_prompt", "totp_prompt",
                },
                "transport.ssh.auto_connect",
            )
        _warn_extra_keys(
            ssh,
            {"control_path", "host", "user", "remote_root", "resource_staging", "auto_connect"},
            "transport.ssh",
        )
    _warn_extra_keys(transport, {"mode", "ssh"}, "transport")

    # server 配置
    server = config["server"]
    _check_key(server, "address", str)
    _check_key(server, "front_end_port", int)
    _check_key(server, "back_end_port", int)
    _check_key(server, "root", str)
    # ephemeral_root 可选：缺省回落到 root（向后兼容，单盘站点无需配置）。
    # 显式配置可把高频随机写的 ephemeral overlay + apptainer tmp/cache 落到更快的盘，
    # 与持久数据（database / file_custody / container_cache，仍在 root）解耦。
    server.setdefault("ephemeral_root", server["root"])
    _check_key(server, "ephemeral_root", str)
    _check_key(server, "cors_origins", list)
    _check_key(server, "database", dict)
    _check_key(server, "auth", dict)
    _check_key(server, "scheduler", dict)
    _check_key(server, "service_proxy", dict)
    _check_key(server, "file_custody", dict)

    expected_server_keys = {
        "address", "front_end_port", "back_end_port", "root", "ephemeral_root",
        "database", "auth", "scheduler", "service_proxy", "file_custody",
        "cors_origins",
    }
    if not is_local:
        _check_key(server, "explorer", dict)
        expected_server_keys |= {"explorer"}
    else:
        # local 模式下这些是可选的
        expected_server_keys |= {"explorer"}
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
        if auth_provider not in ("feishu", "local"):
            raise NotImplementedError(f"❌ auth.provider '{auth_provider}' 尚未实现，HPC 模式支持 'feishu' 或 'local'")
        _check_key(auth, "jwt_signer", dict)
        if auth_provider == "feishu":
            _check_key(auth, "feishu_client", dict)
            _warn_extra_keys(auth, {"provider", "jwt_signer", "feishu_client"}, "server.auth")

            feishu_client = auth["feishu_client"]
            _check_key(feishu_client, "app_id", str)
            _check_key(feishu_client, "app_secret", str)
            _check_key(feishu_client, "admins", list)
            _check_key(feishu_client, "refresh_interval", int)
            _warn_extra_keys(feishu_client, {"app_id", "app_secret", "admins", "refresh_interval"}, "server.auth.feishu_client")
        else:
            _warn_extra_keys(auth, {"provider", "jwt_signer"}, "server.auth")

    jwt_signer = auth["jwt_signer"]
    _check_key(jwt_signer, "secret_key", str)
    _check_key(jwt_signer, "algorithm", str)
    _check_key(jwt_signer, "expire_minutes", int)
    _warn_extra_keys(jwt_signer, {"secret_key", "algorithm", "expire_minutes"}, "server.auth.jwt_signer")

    # scheduler 配置
    scheduler_cfg = server["scheduler"]
    _check_key(scheduler_cfg, "heartbeat_interval", int)
    _check_key(scheduler_cfg, "snapshot_interval", int)
    # cluster 页面只读 SLURM 视图（squeue + scontrol）的 TTL 缓存秒数。0 = 不缓存、
    # 每次现查（默认，保持现状）。骑 ssh transport 的远端站点把它设成几秒可显著降低
    # 高频 poll / 多查看者下的远端查询开销，代价是 cluster 页面至多陈旧这么多秒。
    scheduler_cfg.setdefault("cluster_stats_cache_ttl", 0)
    _check_key(scheduler_cfg, "cluster_stats_cache_ttl", int)
    _warn_extra_keys(scheduler_cfg, {"heartbeat_interval", "snapshot_interval", "cluster_stats_cache_ttl"}, "server.scheduler")

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
        if execution["container_runtime"] not in ("apptainer", "singularity"):
            raise NotImplementedError(f"❌ execution.container_runtime '{execution['container_runtime']}' 尚未实现，HPC 模式支持 'apptainer' 或 'singularity'")
        _check_key(execution, "allow_root", bool)
        _check_key(execution, "resource_cache", dict)
        # slurm 子配置：SLURM 提交方言。全部带保持现状的 default —— partition/qos/
        # account 为 None 即不下发对应 flag；mem_mode='explicit' 即沿用 --mem；
        # module_loads 为空即不注入 module load 前置。独占集群站点无需
        # 配置即字节级等价；共享集群租户场景显式覆盖（如 mem_mode='per_cpu' 折核数、
        # module_loads=['singularity/3.11.3']）。
        execution.setdefault("slurm", {})
        slurm_cfg = execution["slurm"]
        slurm_cfg.setdefault("partition", None)
        slurm_cfg.setdefault("qos", None)
        slurm_cfg.setdefault("account", None)
        slurm_cfg.setdefault("mem_mode", "explicit")
        slurm_cfg.setdefault("mem_per_cpu_mb", 4000)
        slurm_cfg.setdefault("module_loads", [])
        _check_key(slurm_cfg, "partition", str, nullable=True)
        _check_key(slurm_cfg, "qos", str, nullable=True)
        _check_key(slurm_cfg, "account", str, nullable=True)
        _check_key(slurm_cfg, "mem_mode", str)
        if slurm_cfg["mem_mode"] not in ("explicit", "per_cpu"):
            raise ValueError(f"❌ execution.slurm.mem_mode 必须是 'explicit' 或 'per_cpu'，当前值: '{slurm_cfg['mem_mode']}'")
        _check_key(slurm_cfg, "mem_per_cpu_mb", int)
        # per_cpu 模式按 ceil(内存MB / mem_per_cpu_mb) 折核数，<=0 会除零/负核：fail-fast。
        if slurm_cfg["mem_mode"] == "per_cpu" and slurm_cfg["mem_per_cpu_mb"] <= 0:
            raise ValueError(
                f"❌ execution.slurm.mem_mode='per_cpu' 要求 mem_per_cpu_mb > 0，"
                f"当前值: {slurm_cfg['mem_per_cpu_mb']}"
            )
        _check_key(slurm_cfg, "module_loads", list)
        _warn_extra_keys(slurm_cfg, {"partition", "qos", "account", "mem_mode", "mem_per_cpu_mb", "module_loads"}, "execution.slurm")
        expected_exec_keys = {"backend", "container_runtime", "allow_root", "resource_cache", "slurm"}

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
        cluster.setdefault("max_time_limit", None)
        cluster.setdefault("default_cpu_count", 4)
        cluster.setdefault("default_memory_demand", "4G")
        cluster.setdefault("default_time_limit", None)
        cluster.setdefault("default_runner", getpass.getuser())
        cluster.setdefault("default_container_image", "docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
        cluster.setdefault("default_ephemeral_storage", "10G")
        cluster.setdefault("default_system_entry_command", "")
        cluster.setdefault("registry_mirror", None)
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
        # time_limit 上限/默认（分钟）可选,缺省 None = 不限/不下发 --time(保持现状)。
        cluster.setdefault("max_time_limit", None)
        cluster.setdefault("default_time_limit", None)
        _check_key(cluster, "max_time_limit", int, nullable=True)
        _check_key(cluster, "default_time_limit", int, nullable=True)
        cluster.setdefault("registry_mirror", None)
        _check_key(cluster, "registry_mirror", str, nullable=True)
        _warn_extra_keys(cluster, {
            "name", "gpus", "max_cpu_count", "max_memory_demand", "max_time_limit",
            "default_cpu_count", "default_memory_demand", "default_time_limit", "default_runner",
            "default_container_image", "default_ephemeral_storage", "default_system_entry_command",
            "registry_mirror", "scheduling",
        }, "cluster")

    # cluster.scheduling：调度策略模式。authoritative = magnus 独占集群、自己算全
    # 集群 free + EASY backfill + 抢占（独占集群现状）；tenant = magnus 是共享
    # 集群的租户、只按 QOS 配额 eager 提交、把排队/backfill 交给外部 SLURM 自身
    # fairshare 调度（共享集群租户）。default authoritative 保持现状。local 与 HPC 两模式通用。
    cluster = config["cluster"]
    cluster.setdefault("scheduling", {"mode": "authoritative"})
    scheduling = cluster["scheduling"]
    _check_key(scheduling, "mode", str)
    if scheduling["mode"] not in ("authoritative", "tenant"):
        raise ValueError(f"❌ cluster.scheduling.mode 必须是 'authoritative' 或 'tenant'，当前值: '{scheduling['mode']}'")
    # tenant 模式下 cluster 视图与提交都 scope 到本租户获授的分区（见 routers/cluster.py
    # 与 _scheduler/_decisions.py）。SLURM 后端下 partition 是 tenant 语义的前提，缺它会
    # 静默退化成"把整个共享集群当我们的"——明确 fail-fast，逼站点配 execution.slurm.partition。
    # local 后端无 SLURM、tenant 只影响调度分流、不涉分区，故不强制。
    if scheduling["mode"] == "tenant" and config["execution"]["backend"] != "local":
        if not config.get("execution", {}).get("slurm", {}).get("partition"):
            raise ValueError(
                "❌ cluster.scheduling.mode='tenant' 要求 execution.slurm.partition —— "
                "租户视图与提交需 scope 到分区，否则会把整个共享集群误显示成本租户"
            )
    # 远端 transport（ssh）只能配 tenant：驱动的是异机共享集群，magnus 是租户、不掌握
    # 全集群，authoritative 的"全集群算 free + backfill + 抢占"语义不成立。功能层面外，这
    # 也消掉了 authoritative 决策（_make_decisions 的 _compute_cluster_resources / 抢占）
    # 在事件循环上跑的远端查询 —— 它不像 _sync_reality / _submit_jobs / _record_snapshot
    # 那样被 to_thread 卸到线程池，ssh 下会把阻塞的 socket 查询/重建留在 loop 上。fail-fast
    # 杜绝这个组合。
    if config["transport"]["mode"] == "ssh" and scheduling["mode"] == "authoritative":
        raise ValueError(
            "❌ transport.mode='ssh' 不能与 cluster.scheduling.mode='authoritative' 同用 —— "
            "远端共享集群下 magnus 是租户，请用 scheduling.mode='tenant'"
        )
    _warn_extra_keys(scheduling, {"mode"}, "cluster.scheduling")


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
            # ephemeral_root 显式配置时同样隔离 develop/prod；缺省时它会在
            # 校验阶段回落到（已加后缀的）root，无需在此处理。
            if "ephemeral_root" in data["server"]:
                data["server"]["ephemeral_root"] += "-develop"
            # 远端站点（transport=ssh）的 remote_root 同样要隔离 dev/prod —— 它在共享盘
            # （如 Lustre）上，dev/prod 两实例若共用同一 remote_root，job 工作区与
            # container_cache 会在同一路径互相踩。prod（--deliver）不进此分支，保持原值。
            _transport = data.get("transport") or {}
            if _transport.get("mode") == "ssh":
                _ssh = _transport.get("ssh") or {}
                if _ssh.get("remote_root"):
                    _ssh["remote_root"] += "-develop"

        # 快速失败：启动时验证配置完整性
        _prepare_and_validate_magnus_config(data)

        return data
    except Exception as error:
        raise RuntimeError(f"❌ 解析 YAML 失败: {error}\n调用栈：\n{traceback.format_exc()}")


magnus_config = _load_magnus_config()
is_local_mode = magnus_config["execution"]["backend"] == "local"
is_local_auth = magnus_config["server"]["auth"]["provider"] == "local"

admin_open_ids: Set[str]
if is_local_auth:
    admin_open_ids = set()
else:
    admin_open_ids = set(magnus_config["server"]["auth"]["feishu_client"]["admins"])


def is_admin_user(user) -> bool:
    """本地认证模式下所有用户都是 admin；飞书模式下检查 feishu_open_id。"""
    if is_local_auth:
        return True
    return user.feishu_open_id in admin_open_ids


_CLUSTER_DEFAULT_FIELDS = [
    ("cpu_count", "default_cpu_count"),
    ("memory_demand", "default_memory_demand"),
    ("time_limit", "default_time_limit"),
    ("ephemeral_storage", "default_ephemeral_storage"),
    ("runner", "default_runner"),
    ("container_image", "default_container_image"),
    ("system_entry_command", "default_system_entry_command"),
]


def apply_cluster_defaults(data: Dict[str, Any])-> Dict[str, Any]:
    """填充 Job/Service 提交字典中未指定的资源字段为集群默认值。In-place + return self。

    只补提交字典里已经带的字段,不凭空注入新键:Service 提交（ServiceCreate）不含
    time_limit（常驻服务给墙钟会被超时误杀）,其 data 随后整体 splat 进 Service(**data),
    若注入了 Service 无此列的键会构造失败。
    """
    cluster = magnus_config["cluster"]
    for field, default_key in _CLUSTER_DEFAULT_FIELDS:
        if field not in data:
            continue
        if data[field] is None or (field == "cpu_count" and data[field] == 0):
            data[field] = cluster[default_key]
    return data


def normalize_per_cpu_resources(data: Dict[str, Any])-> Dict[str, Any]:
    """per_cpu 内存模式下，把 cpu_count / memory_demand 归一化为真实分配值。In-place + return self。

    共享集群（execution.slurm.mem_mode='per_cpu'）禁用 --mem，SLURM 按 DefMemPerCPU 给每核
    固定 mem_per_cpu_mb 内存，job 实际内存 = 有效核数 × mem_per_cpu_mb。用户提交的
    memory_demand 只在内存需求超过 cpu_count 隐含内存时上调核数（见 _size_utils
    effective_cpu_count_per_cpu）。若不归一化，DB / UI / SDK 会原样存用户填的 memory_demand
    （多为默认值，如 1600M），与真实分配（动辄数十～数百 GB）差几十倍，严重误导。这里把两者
    对齐到真实有效值，使存储与展示诚实；提交时 submit_job_simple 对归一化结果再折算是幂等的。

    explicit 模式（自有站点现状）下 --mem 即真实分配、local 模式无 SLURM 概念，均直接返回
    —— 这两类部署字节级不变。在 apply_cluster_defaults 之后、validate_cluster_limits 之前
    调用，使上限校验落在归一化后的有效核数 / 内存上。
    """
    slurm_cfg = magnus_config["execution"].get("slurm")
    if slurm_cfg is None or slurm_cfg.get("mem_mode") != "per_cpu":
        return data

    mem_per_cpu_mb = slurm_cfg["mem_per_cpu_mb"]
    effective_cpu_count = effective_cpu_count_per_cpu(
        cpu_count = data.get("cpu_count"),
        memory_demand = data.get("memory_demand"),
        mem_per_cpu_mb = mem_per_cpu_mb,
    )
    if effective_cpu_count <= 0:
        return data
    data["cpu_count"] = effective_cpu_count
    data["memory_demand"] = f"{effective_cpu_count * mem_per_cpu_mb}M"
    return data


def validate_cluster_limits(data: Dict[str, Any])-> None:
    """校验 Job/Service 提交字典中 cpu_count / memory_demand / gpu_type 与本站集群匹配。
    不匹配抛 ValueError，由 endpoint 层转为 HTTP 400。

    GPU 类型校验是 fast-fail：SLURM 在 gres 不严格的站点会照样跑 a100 任务到 rtx5090
    卡上（用户透过 SDK 提交时尤甚，UI form 走 SearchableSelect 看不到非法选项），
    所以服务端必须把住，错误信息里列出本站实际支持哪些类型让用户改。
    """
    cluster = magnus_config["cluster"]

    max_cpu = cluster["max_cpu_count"]
    if data["cpu_count"] > max_cpu:
        raise ValueError(f"cpu_count={data['cpu_count']} exceeds cluster limit ({max_cpu})")

    max_mem_str = cluster["max_memory_demand"]
    requested_mem = _parse_size_string(data["memory_demand"])
    max_mem = _parse_size_string(max_mem_str)
    if requested_mem > max_mem:
        raise ValueError(f"memory_demand={data['memory_demand']} exceeds cluster limit ({max_mem_str})")

    # time_limit 仅在站点配了 max_time_limit 且本次提交带了 time_limit 时校验;两者任一为
    # None 都不限(保持现状)。
    max_time = cluster["max_time_limit"]
    requested_time = data.get("time_limit")
    if max_time is not None and requested_time is not None and requested_time > max_time:
        raise ValueError(f"time_limit={requested_time} exceeds cluster limit ({max_time} min)")

    raw_gpu_type = data.get("gpu_type") or "cpu"
    gpu_type = raw_gpu_type.strip().lower()
    allowed_gpu_types = {"cpu"} | {g["value"].lower() for g in cluster["gpus"]}
    if gpu_type not in allowed_gpu_types:
        raise ValueError(
            f"gpu_type={raw_gpu_type!r} not available on this station; "
            f"available: {sorted(allowed_gpu_types)}"
        )
