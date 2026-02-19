# Magnus Job Runtime

> 2026-02-19, by Claude (with zycai)

本文档描述 Magnus job 从提交到容器内执行的完整运行时链路，以及宿主机与容器之间的文件系统协议和环境变量协议。

## 执行链路总览

```
用户提交 (POST /api/jobs/submit)
  │
  ▼
PREPARING ─── 异步资源准备（并行）
  │             ├── ensure_image: docker:// → .sif (LRU cache, 80G)
  │             └── ensure_repo:  git clone → copy → checkout → setfacl
  ▼
PENDING ───── 队头挂号调度
  │             ├── 按优先级排序: A1(4) > A2(3) > B1(2) > B2(1), 同级 FIFO
  │             ├── A 类可抢占 RUNNING 的 B 类 (B2 优先, LIFO)
  │             └── SLURM 队列中只允许一个 QUEUED job
  ▼
QUEUED ────── SLURM sbatch 已提交
  │             └── sbatch 脚本: python3 {workspace}/jobs/{id}/wrapper.py
  ▼
RUNNING ───── wrapper.py 开始执行
  │             ├── Phase 1: GPU spy 守护线程 (nvidia-smi 轮询)
  │             ├── Phase 2: 写 .magnus_user_script.sh
  │             ├── Phase 3: shell 引导层 → apptainer exec → 用户脚本
  │             └── Phase 4: epilogue, 写 .magnus_success
  ▼
SUCCESS / FAILED
```

## wrapper.py: 三层结构

`_scheduler.py` 的 `_build_wrapper_content()` 生成 `wrapper.py`，它是 SLURM 实际执行的入口。wrapper 内含三个嵌套层次：

```
wrapper.py (Python, SLURM 直接运行)
  ├── GPU spy thread        ← Python threading
  ├── .magnus_user_script.sh 写入
  └── shell_cmd (Bash)      ← subprocess.call(shell=True)
        ├── 环境变量注入 (APPTAINERENV_*)
        ├── system_entry_command 执行
        ├── overlay 创建
        └── apptainer exec   ← 容器入口
              └── .magnus_user_script.sh  ← 用户代码
```

### Phase 1: GPU Spy

守护线程，每 `spy_gpu_interval`（默认 5）秒调一次 `nvidia-smi --query-gpu=index,utilization.gpu,utilization.memory`，原子写入 `gpu_status.json`（写 .tmp → `os.replace`）。scheduler 心跳时读取该文件采集指标。

### Phase 2: 用户脚本

将 `job.entry_command` 写入 `.magnus_user_script.sh`，前面加 `set -e` 和 `export HOME=$MAGNUS_HOME`。

### Phase 3: Shell 引导层

这是最复杂的部分，按顺序执行：

1. **注入容器环境变量** — 通过 `APPTAINERENV_` 前缀（apptainer 会自动去前缀注入容器）
2. **执行 `system_entry_command`** — 宿主机侧的 per-job 可配置 shell 脚本
3. **设置 apptainer 运行时目录** — `APPTAINER_TMPDIR`, `APPTAINER_CACHEDIR`
4. **追加 bind mount** — workspace 挂到 `${MAGNUS_HOME}/workspace`
5. **代理穿透** — bridge 模式下将 `127.0.0.1`/`localhost` 替换为 `$MAGNUS_HOST_GATEWAY`
6. **创建 overlay** — `apptainer overlay create --size {MB}`（可被 `MAGNUS_NO_OVERLAY=1` 跳过）
7. **执行 apptainer** — host 模式直接 exec，bridge 模式走 `rootlesskit`

### Phase 4: Epilogue

apptainer 返回 0 时写 `.magnus_success` 标记。finally 块中清理 overlay 文件。

## 文件系统协议

### 宿主机侧

所有路径基于 `{magnus_root}/workspace/jobs/{job_id}/`（下文简称 `{work}/`）：

| 路径 | 生命周期 | 写入方 | 读取方 | 说明 |
|------|----------|--------|--------|------|
| `{work}/repository/` | prepare → cleanup | resource_manager | 容器 (bind) | git checkout，容器内的工作目录 |
| `{work}/wrapper.py` | submit → cleanup | scheduler | SLURM | 生成的执行入口 |
| `{work}/gpu_status.json` | submit → job 结束 | GPU spy thread | scheduler 心跳 | 原子更新的 GPU 指标 |
| `{work}/slurm/output.txt` | submit → 永久 | SLURM | API (日志) | sbatch --output 指向此处 |
| `{work}/.magnus_user_script.sh` | wrapper 执行 → cleanup | wrapper.py | 容器 (bind) | 用户入口脚本 |
| `{work}/.magnus_success` | epilogue → sync_reality | wrapper.py | scheduler | 成功标记，存在即 SUCCESS |
| `{work}/.magnus_result` | 容器内用户写入 → API 读取 | 用户代码 | routers/jobs.py | 任务结果内容 |
| `{work}/.magnus_action` | 容器内用户写入 → API 读取 | 用户代码 | routers/jobs.py + SDK | 客户端动作指令 |
| `{work}/ephemeral_overlay.img` | Phase 3 → finally | wrapper shell | apptainer | 可写层，job 结束后删除 |
| `{work}/.magnus_tmp/` | Phase 3 → cleanup | apptainer | apptainer | APPTAINER_TMPDIR |
| `{work}/.magnus_cache/` | Phase 3 → cleanup | apptainer | apptainer | APPTAINER_CACHEDIR |

**cleanup** 指 `_clean_up_working_table()`，在 job 结束（SUCCESS/FAILED/TERMINATED/PAUSED）时调用。`slurm/output.txt` 不被清理。

### 容器内侧

```
${MAGNUS_HOME}/                              默认 /magnus
${MAGNUS_HOME}/workspace/                    bind mount ← {work}/
${MAGNUS_HOME}/workspace/repository/         git checkout, 也是 --pwd
${MAGNUS_HOME}/workspace/.magnus_user_script.sh
${MAGNUS_HOME}/workspace/.magnus_result      $MAGNUS_RESULT
${MAGNUS_HOME}/workspace/.magnus_action      $MAGNUS_ACTION
```

容器文件系统是只读 squashfs (SIF)。所有非 bind-mount 路径的写入落到 ephemeral overlay，受 `ephemeral_storage` 大小约束（默认 10G），job 结束后销毁。

## 环境变量协议

### 容器内注入的环境变量

通过 `APPTAINERENV_` 前缀机制注入，容器内去掉前缀后可直接读取：

| 变量 | 来源 | 说明 |
|------|------|------|
| `MAGNUS_TOKEN` | `job.user.token` | 当前用户的 trust token，SDK 自动识别 |
| `MAGNUS_ADDRESS` | `{server.address}:{server.front_end_port}` | Magnus 后端地址 |
| `MAGNUS_JOB_ID` | `job.id` | 当前 job ID |
| `MAGNUS_HOME` | `${MAGNUS_HOME:-/magnus}` | 容器内根目录，子 Magnus 可覆盖 |
| `MAGNUS_RESULT` | `${MAGNUS_HOME}/workspace/.magnus_result` | 结果文件路径 |
| `MAGNUS_ACTION` | `${MAGNUS_HOME}/workspace/.magnus_action` | 动作文件路径 |
| `HTTP_PROXY` 等 | 宿主机继承 | bridge 模式下自动替换 localhost → gateway |

### shell 引导层的环境变量旋钮

这些变量由 `system_entry_command` 设置，控制 wrapper shell 的行为：

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `MAGNUS_HOME` | `/magnus` | 容器内根路径，影响 bind mount 目标和所有内部路径 |
| `MAGNUS_NO_OVERLAY` | `0` | 设为 `1` 跳过 ephemeral overlay 创建 |
| `MAGNUS_CONTAIN_LEVEL` | `containall` | apptainer 隔离级别 (`containall` / `contain`) |
| `MAGNUS_FAKEROOT` | `0` | 设为 `1` 添加 `--fakeroot` |
| `MAGNUS_NET_MODE` | `host` | 设为 `bridge` 启用 rootlesskit 网络隔离 |
| `MAGNUS_PORT_MAP` | (无) | bridge 模式下 rootlesskit 的端口映射 |
| `MAGNUS_HOST_GATEWAY` | `10.0.2.2` | bridge 模式下代理地址替换目标 |
| `MAGNUS_HOST_LOOPBACK` | `0` | 设为 `1` 允许容器访问宿主机 loopback |
| `APPTAINER_BIND` | (无) | 额外 bind mount，wrapper 会追加 workspace 绑定 |

`system_entry_command` 是 per-job 可配置的，不设则用 `cluster.default_system_entry_command`。它在宿主机侧、容器外执行。

## apptainer 执行参数

```bash
apptainer exec \
  --nv \                                  # GPU 驱动透传
  --${MAGNUS_CONTAIN_LEVEL} \             # containall (默认) 或 contain
  --no-mount tmp \                        # 禁止 /tmp 上的 64MB tmpfs，写入走 overlay
  [--overlay ephemeral_overlay.img] \     # 可写层 (MAGNUS_NO_OVERLAY!=1 时)
  [--fakeroot] \                          # MAGNUS_FAKEROOT=1 时
  --pwd ${MAGNUS_HOME}/workspace/repository \
  {sif_path} \
  bash ${MAGNUS_HOME}/workspace/.magnus_user_script.sh
```

bridge 模式下整个 apptainer 命令被 rootlesskit 包裹：
```bash
rootlesskit \
  --net=slirp4netns \
  --port-driver=builtin \
  --publish $MAGNUS_PORT_MAP \
  [--disable-host-loopback] \             # MAGNUS_HOST_LOOPBACK!=1 时
  apptainer exec ...
```

## SLURM 提交参数

```bash
sbatch --parsable \
  --job-name={task_name} \
  --output={work}/slurm/output.txt \
  --gres=gpu:{gpu_type}:{gpu_count} \
  --mem={memory_demand} \
  --cpus-per-task={cpu_count} \
  # 脚本内容: python3 {work}/wrapper.py
```

环境变量 `MAGNUS_RUNNER` 和 `MAGNUS_TOKEN` 通过 sbatch 的进程环境传递。

## scheduler 心跳与状态同步

心跳间隔 `scheduler.heartbeat_interval`（默认 2 秒），每次 tick:

1. **`_sync_reality`**: 遍历 QUEUED/RUNNING job，用 `squeue` 查真实状态
   - SLURM 报 RUNNING → DB 标 RUNNING
   - SLURM 报 COMPLETED + `.magnus_success` 存在 → SUCCESS，同时检查 `.magnus_result` 和 `.magnus_action`
   - SLURM 报 COMPLETED 但无 `.magnus_success` → FAILED
   - SLURM 报 FAILED/CANCELLED/TIMEOUT → FAILED
2. **`_make_decisions`**: 调度 PENDING/PAUSED job
3. **`_record_snapshot`**: 每 `snapshot_interval`（默认 300 秒）记录集群快照

## 资源准备

在 PREPARING 阶段并行执行：

**镜像拉取** (`_resource_manager.ensure_image`):
- docker URI → SIF 文件名映射（`docker://a/b:tag` → `a_b_tag.sif`）
- 缓存目录 `{magnus_root}/container_cache/`，LRU 淘汰，上限 `resource_cache.container_cache_size`
- per-image asyncio.Lock 防重复拉取
- 3 次重试 + 指数退避，非瞬态错误（unauthorized, not found）直接失败

**仓库克隆** (`_resource_manager.ensure_repo`):
- 缓存目录 `{magnus_root}/repo_cache/`，LRU 淘汰，上限 `resource_cache.repo_cache_size`
- 缓存 → copy 到 `{work}/repository/` → fetch + checkout 到指定 commit SHA
- `setfacl` 设置 runner 用户权限（容器内以 runner 身份执行时需要）

## 子 Magnus (嵌套容器)

子 Magnus 是在容器内运行完整 Magnus + SLURM 栈的场景。

### 已知的底层陷阱

**SLURM `PartitionName=default` 是保留字**: SLURM 将 `default`（大小写不敏感）解释为分区默认模板，不是实际分区名。子 SLURM 集群使用 `PartitionName=batch`。

**容器内 bind mount 路径不能与母 Magnus 冲突**: 母 Magnus 已经 bind-mount 了 `/magnus`，子 apptainer 再 bind 同路径会冲突。解法：在子 Magnus 的 `system_entry_command` 中 `export MAGNUS_HOME=/submagnus`，所有内部路径自动跟随。

### 子 Magnus 的典型 system_entry_command

```bash
# 额外 bind mount
mounts=(
  "/dev/fuse:/dev/fuse"           # 子 apptainer 需要 fuse 设备
)
export APPTAINER_BIND=$(IFS=,; echo "${mounts[*]}")

# 路径隔离
export MAGNUS_HOME=/submagnus     # 不能叫 /magnus，母容器已占用

# 降级隔离
export MAGNUS_CONTAIN_LEVEL=contain  # containall 在嵌套场景下过于严格
export MAGNUS_NO_OVERLAY=1           # fuse-overlayfs 不支持嵌套

# 网络
export MAGNUS_HOST_LOOPBACK=1     # 允许访问宿主机代理

# 权限
# 配合 server.scheduler.allow_root=true
```

### 子 SLURM 引导

`scripts/setup_single_node_slurm.sh` 在容器内引导单节点 SLURM 集群：
- 集群名 `magnus-child`，分区名 `batch`
- 启动 munge → slurmctld → slurmd
- 通过 `sinfo` 验证集群就绪

### 嵌套容器的已知限制

Ephemeral overlay (fuse-overlayfs) 在嵌套容器中不工作 — FUSE-on-FUSE 的 mount propagation 跨 namespace 出问题。当前通过 `MAGNUS_NO_OVERLAY=1` 绕过。详见 `docs/apptainer_inside_apptainer_claude_report.md`。

## 配置参考

### `magnus_config.yaml` 中与 job runtime 相关的配置

```yaml
server:
  root: /home/magnus/magnus-data            # 所有路径的根
  scheduler:
    heartbeat_interval: 2                   # 心跳间隔 (秒)
    spy_gpu_interval: 5                     # GPU 采集间隔 (秒)
    snapshot_interval: 300                  # 集群快照间隔 (秒)
    allow_root: false                       # 是否允许 root runner
  resource_cache:
    container_cache_size: 80G               # SIF 缓存上限 (LRU)
    repo_cache_size: 20G                    # git repo 缓存上限 (LRU)

cluster:
  default_cpu_count: 4
  default_memory_demand: 1600M
  default_runner: zycai
  default_container_image: docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
  default_ephemeral_storage: 10G
  default_system_entry_command: |-
    mounts=(
      "/home:/home"
      "/opt/miniconda3:/opt/miniconda3"
    )
    export APPTAINER_BIND=$(IFS=,; echo "${mounts[*]}")
    export MAGNUS_HOME=/magnus
    unset -f nvidia-smi
    unset VIRTUAL_ENV SSL_CERT_FILE
    export UV_CACHE_DIR=/home/magnus/magnus-data-develop/uv_cache/$USER
```

### 源文件索引

| 文件 | 职责 |
|------|------|
| `back_end/server/_scheduler.py` | 调度器核心：心跳、状态同步、wrapper 生成、SLURM 提交 |
| `back_end/server/_slurm_manager.py` | SLURM CLI 封装 (sbatch/squeue/scancel/sinfo) |
| `back_end/server/_resource_manager.py` | 镜像拉取 + 仓库克隆，带 LRU 缓存 |
| `back_end/server/routers/jobs.py` | Job CRUD API，惰性读取 .magnus_result/.magnus_action |
| `back_end/server/models.py` | Job 模型 (SQLAlchemy) |
| `configs/magnus_config.yaml` | 配置源 |
| `docker/magnus-runtime/Dockerfile` | 子 Magnus 运行时镜像 |
| `scripts/setup_single_node_slurm.sh` | 容器内 SLURM 引导脚本 |
