> **Language / 语言**: [English](local-mode.md) · **简体中文**

# Magnus 本地模式

## 概述

Magnus 支持一种本地执行模式，在用户机器的 Docker 容器中运行作业，而非在 SLURM 管理的 HPC 集群上运行。本地模式提供**完整的 Magnus 技术栈**——backend、frontend 和 SDK——与 HPC 部署保持一致，仅以 Docker 替代 SLURM+Apptainer 作为执行层。

```
User/Agent → magnus CLI / SDK / Web UI → HTTP API → Local Magnus Server → Docker containers
```

## 快速上手

前置条件：必须安装 Docker、git、uv 和 Node.js。

```bash
pip install magnus-sdk
git clone https://github.com/rise-agi/magnus.git ~/.magnus/repository
magnus local start    # checks deps, installs backend/frontend deps, starts everything
magnus run hello-world -- --message "It works!"
magnus local stop     # stops backend + frontend, restores previous SDK site
```

在 `magnus local start` 之后：
- Backend：`http://127.0.0.1:8017`
- Frontend：`http://localhost:3011`（自动登录，无需 Feishu）

## 架构

### 二元 backend 选择

`execution.backend` 只能是 `"slurm"`（HPC）或 `"local"`（Docker），不允许交叉配置。当 `backend: local` 时：

- `auth.provider` 必须为 `"local"`（免登录）
- `container_runtime` 被强制为 `"docker"`
- 不需要 SLURM 依赖
- 集群资源属性（gpu_count、memory_demand、cpu_count、ephemeral_storage）会被接受但**不强制执行**

### 配置隔离

`magnus local start` 会在 `~/.magnus/local_config.yaml` 生成一份**独立**的配置。`configs/magnus_config.yaml` 下的生产配置绝不会被修改。backend 和 frontend 通过以下方式接收本地配置路径：
- Backend：`--config ~/.magnus/local_config.yaml`
- Frontend：`MAGNUS_CONFIG_PATH=~/.magnus/local_config.yaml`

| 字段 | HPC 模式 | 本地模式 |
|-------|----------|------------|
| `execution.backend` | `slurm` | `local` |
| `execution.container_runtime` | `apptainer` | `docker` |
| `auth.provider` | `feishu` | `local` |
| `server.root` | 站点特定 | `~/.magnus/data` |
| `server.back_end_port` | 8017 | 8017 |
| `server.front_end_port` | 3011 | 3011 |
| `cluster.*` | 完整集群规格 | 最小默认值 |

### 端口

本地模式使用固定端口：**8017**（backend）和 **3011**（frontend）。这些与生产端口相同——通过传入 `--deliver` 标志来跳过 +2 的 dev 偏移。

## Frontend（Web UI）

frontend 在本地模式下运行时存在以下差异：

- **无需登录**：`auth.provider: local` 会触发自动认证。`AuthProvider` 通过 `NEXT_PUBLIC_AUTH_PROVIDER` 检测本地模式，并调用 `POST /api/auth/local/login` 自动获取 JWT。
- **配置注入**：`MAGNUS_CONFIG_PATH` 和 `MAGNUS_DELIVER=TRUE` 作为环境变量传给 `npm run dev`。
- **API 代理**：Next.js 的 catch-all 路由（`/api/[...path]`）将请求代理到 backend，与 HPC 模式一致。

## 目录布局

```
~/.magnus/
├── config.json              # SDK site config (which server to talk to)
├── local_config.yaml        # Generated server config for local mode
├── local_server.pid         # Backend PID
├── local_frontend.pid       # Frontend PID
├── local_previous_site      # Previous SDK site name (restored on stop)
├── data/                    # Magnus root (database, workspace, caches)
│   ├── database/            # SQLite database
│   └── workspace/           # Job working directories
│       └── jobs/
│           └── {job_id}/
│               ├── repository/              # Git repo checkout
│               ├── slurm/output.txt         # Job logs (streamed incrementally)
│               ├── .magnus_user_script.sh   # User's entry command
│               ├── .magnus_success          # Success marker (written by host)
│               ├── .magnus_result           # Job result (optional)
│               └── .magnus_action           # Post-job action (optional)
└── repository/              # Magnus backend code (if not editable-installed)
```

## 作业执行流程

### HPC 模式（SLURM + Apptainer）
```
PENDING → wrapper.py → sbatch → SLURM queue → apptainer exec → exit 0 → wrapper writes .magnus_success
```

### 本地模式（Docker）
```
PENDING → docker pull → docker run -d → heartbeat polls docker inspect → host writes .magnus_success
```

### Docker Run 命令

```bash
docker run -d \
  --name magnus-job-{job_id} \
  --network host \              # Linux only; Windows/macOS use bridge mode
  -v {job_working_table}:/magnus/workspace \
  -e MAGNUS_TOKEN=... \
  -e MAGNUS_ADDRESS=... \       # Linux: http://127.0.0.1:8017, Windows/macOS: http://host.docker.internal:8017
  -e MAGNUS_JOB_ID=... \
  -e MAGNUS_HOME=/magnus \
  -w /magnus/workspace/repository \
  {image} \
  bash /magnus/workspace/.magnus_user_script.sh
```

与 HPC 模式的关键差异：
- **无 wrapper.py**：Docker run 是单次 subprocess 调用
- **宿主端成功标记**：宿主 Python 在检测到 exit code 0 后写入 `.magnus_success`（与 HPC 模式中由 wrapper.py 写入标记对称）
- **增量日志流**：每次心跳调用 `docker logs --since <timestamp>` 并追加到 `slurm/output.txt`
- **无 overlay 文件系统**：Docker 提供自己的可写层
- **无 GPU spy 线程**：跳过 GPU 监控
- **资源属性接受但不强制执行**：Memory/CPU 限制不应用到容器上
- **GPU 透传**：若 `gpu_count > 0`，则添加 `--gpus all`（需要 NVIDIA Container Toolkit）

### 三阶段同步

`_sync_reality_docker` 遵循与 `_sync_reality_slurm` 对称的三阶段模式：

1. **阶段 1** —— 收集活动作业 ID 和状态（短 DB 会话）
2. **阶段 2** —— 检查 Docker 容器状态 + 转储增量日志（无 DB 会话）
3. **阶段 3** —— 批量更新 DB 中的作业状态（短会话）

这样可避免在外部 Docker 调用期间持有 DB 会话。

## 跨平台支持

| 功能 | Linux | Windows | macOS |
|---------|-------|---------|-------|
| Docker 网络 | `--network host` | bridge + `host.docker.internal` | bridge + `host.docker.internal` |
| system_entry_command | bash 执行 | 跳过（无 bash） | bash 执行 |
| 进程管理 | `start_new_session` | `CREATE_NEW_PROCESS_GROUP` | `start_new_session` |
| npm 命令 | `npm` | `npm.cmd` | `npm` |

## system_entry_command 解释

Blueprint 可能指定一个 `system_entry_command`，用于设置 `APPTAINER_BIND` 以进行 bind mount。在本地模式下，scheduler（`_extract_bind_mounts_from_system_entry_command`）会：

1. 在 bash subprocess 中执行该命令
2. 读取最终得到的 `APPTAINER_BIND` 环境变量
3. 将每个条目翻译为 Docker 的 `-v` 标志

这是一个**有损的转换** —— 只提取 `APPTAINER_BIND` 变量。其他环境变量和副作用（例如 `module load`）都会被丢弃。

在 Windows 上，`system_entry_command` 完全被跳过（无可用的 bash）。

## 认证

本地模式完全禁用了认证。由于服务器绑定到 `127.0.0.1`，所有请求均被视为可信。服务器启动时：
1. 创建一个默认用户（以 OS 用户命名）
2. `get_current_user` 返回该用户，不检查任何 token
3. 所有用户均被视为 admin
4. 无需 Feishu OAuth

frontend 通过 `POST /api/auth/local/login` 自动初始化（返回用于 UI 展示的用户信息）。SDK 发送一个占位 token（`"local"`）以满足自身的非空校验，但 backend 会忽略它。在 `magnus local stop` 时，会恢复之前的 SDK site。

## 内置 Blueprint

`magnus local start` 会自动注册位于 `sdks/python/src/magnus/bundled/blueprints/` 中打包的 blueprint。这提供了开箱即用的体验。内置 blueprint 会在每次启动时被重新注册（幂等覆盖）。

要添加内置 blueprint，将 `.py` 文件放入 `bundled/blueprints/` 目录即可。文件名（去掉 `.py`）会成为 blueprint ID。

## Explorer

Explorer（AI 助手）由配置中 `server.explorer` 的**存在**启用，而非由 backend 模式决定。在配置中提供 API key 的本地模式用户可以完整使用 Explorer。

## 局限

- **无 GPU 监控**：未为 Docker 容器实现 nvidia-smi 轮询
- **无资源强制**：容器上不强制执行 Memory/CPU 限制
- **无服务代理**：service 系统在本地模式下未经测试
- **无抢占**：所有作业均不带基于优先级的抢占运行
- **无临时存储强制**：使用 Docker 的可写层而不限制大小
