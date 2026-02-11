# Magnus SDK & CLI 完整指南

本文档提供 Magnus Python SDK 和命令行工具 (CLI) 的完整使用说明。

> **设计原则**: CLI 的 API 与 Python SDK（及未来各语言 SDK）保持一致。相同的操作在不同接口中具有相同的语义和参数结构。

---

## 目录

- [Python SDK](#python-sdk)
  - [安装](#安装)
  - [环境配置](#环境配置)
  - [配置优先级](#配置优先级)
  - [容器内环境变量](#容器内环境变量)
  - [蓝图操作](#蓝图操作)
  - [服务调用](#服务调用)
  - [任务管理](#任务管理)
  - [集群与资源](#集群与资源)
  - [文件传输](#文件传输)
  - [文件代管](#文件代管)
  - [异步 API](#异步-api)
  - [API 参考](#api-参考)
- [命令行工具 (CLI)](#命令行工具-cli)
  - [全局选项](#全局选项)
  - [magnus config](#magnus-config)
  - [magnus login](#magnus-login)
  - [magnus submit](#magnus-submit)
  - [magnus run](#magnus-run)
  - [magnus call](#magnus-call)
  - [magnus jobs](#magnus-jobs)
  - [magnus status](#magnus-status)
  - [magnus logs](#magnus-logs)
  - [magnus kill](#magnus-kill)
  - [magnus cluster](#magnus-cluster)
  - [magnus blueprints](#magnus-blueprints)
  - [magnus services](#magnus-services)
  - [magnus send](#magnus-send)
  - [magnus receive](#magnus-receive)
  - [magnus custody](#magnus-custody)
  - [magnus connect](#magnus-connect)
  - [magnus disconnect](#magnus-disconnect)
- [附录](#附录)
  - [输出格式](#输出格式)
  - [错误代码](#错误代码)
  - [常见问题](#常见问题)

---

## Python SDK

### 安装

```bash
# 使用 pip
pip install magnus-sdk

# 使用 uv
uv add magnus-sdk

# 从源码安装
cd sdks/python && pip install -e .
```

### 环境配置

SDK 通过以下两个配置项连接 Magnus 后端：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `address` | Magnus 后端地址 | `http://162.105.151.196:8017` |
| `token` | 信任令牌 (Trust Token)，从 Web 界面用户菜单获取 | `sk-aBcDeFgHiJkLmNoPqRsTuVwXyZaB` |

推荐使用 `magnus login` 交互式配置（保存到 `~/.magnus/config.json`，即时生效）：

```bash
magnus login
```

也可以手动设置环境变量（环境变量优先级高于配置文件）：

```bash
export MAGNUS_TOKEN="sk-your-trust-token"
export MAGNUS_ADDRESS="http://your-server:8017"
```

也可以在代码中配置：

```python
import magnus

magnus.configure(
    token="sk-your-trust-token",
    address="http://your-server:8017",
)
```

### 配置优先级

SDK 按以下顺序解析配置，**先找到的生效**：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 (最高) | `magnus.configure()` 显式传参 | 代码中硬编码，仅影响当前进程 |
| 2 | 环境变量 `MAGNUS_TOKEN` / `MAGNUS_ADDRESS` | 适合 CI/CD、容器内注入、`.bashrc` 老用户 |
| 3 | `~/.magnus/config.json` | `magnus login` 写入，跨 shell 即时生效 |
| 4 (最低) | 默认值 | address 默认 `http://127.0.0.1:8017`，token 默认为空 |

**设计意图**：

- **新用户**：`magnus login` 一次配置，所有终端立即可用，无需 `source` 或重启。
- **老用户**：如果已在 `.bashrc` 中 `export MAGNUS_TOKEN=...`，环境变量优先级更高，完全无感。
- **Trust Forwarding**：Magnus 在容器内执行任务时会注入 `MAGNUS_TOKEN` 环境变量，天然覆盖配置文件，蓝图调蓝图（Job-in-Job）场景零阻碍。
- **`magnus config`** 命令会显示每个配置项的实际来源（env / file / default），方便排查。

### 容器内环境变量

Magnus 任务在 Apptainer 容器内执行时，以下环境变量自动注入：

| 变量 | 说明 | 示例值 |
|------|------|--------|
| `MAGNUS_HOME` | 容器内 Magnus 根目录，同时设为 `HOME`（`echo ~` 输出此值） | `/magnus` |
| `MAGNUS_TOKEN` | 当前用户的 Trust Token，用于容器内调用 Magnus API | `sk-...` |
| `MAGNUS_ADDRESS` | Magnus 后端地址 | `http://162.105.151.196:3011` |
| `MAGNUS_JOB_ID` | 当前任务 ID | `abc123` |
| `MAGNUS_RESULT` | 任务结果文件路径，写入此文件的内容会作为任务结果返回 | `$MAGNUS_HOME/workspace/.magnus_result` |

工作区位于 `$MAGNUS_HOME/workspace/`，代码仓库在 `$MAGNUS_HOME/workspace/repository/`。

`MAGNUS_HOME` 在 `system_entry_command` 之前预设，用户可在 `system_entry_command` 中覆盖。`HOME` 在 `system_entry_command` 之后设为 `$MAGNUS_HOME`。

### 蓝图操作

#### submit_blueprint - 提交蓝图任务

提交蓝图任务后立即返回 Job ID，不等待任务完成。

```python
import magnus

# 基本用法
job_id = magnus.submit_blueprint("quadre-simulation")

# 传递参数
job_id = magnus.submit_blueprint(
    "quadre-simulation",
    args={
        "Te": "2.0",
        "B": "1.5",
        "data_dir": "/home/user/outputs/exp1",
    },
)

# 完整参数
job_id = magnus.submit_blueprint(
    "quadre-simulation",
    args={"Te": "2.0"},
    use_preference=True,   # 合并用户缓存的偏好参数
    save_preference=True,  # 成功后保存参数为新偏好
)

print(f"任务已提交: {job_id}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数，键值对形式
- `use_preference` (bool, 可选): 是否合并已缓存的偏好参数，默认 True
- `save_preference` (bool, 可选): 成功后是否保存参数为新偏好，默认 True

**返回值**：
- `str`: 提交成功的 Job ID

#### run_blueprint - 提交并等待完成

提交任务并轮询等待完成，返回任务结果。

```python
import magnus

# 基本用法
result = magnus.run_blueprint("my-blueprint", args={"param": "value"})

# 设置超时时间 (秒)
result = magnus.run_blueprint(
    "long-running-task",
    args={"input": "data"},
    timeout=3600,  # 1 小时超时
    poll_interval=10,  # 每 10 秒检查一次状态
)

print(f"任务结果: {result}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数
- `use_preference` (bool, 可选): 是否合并已缓存的偏好参数，默认 True
- `save_preference` (bool, 可选): 成功后是否保存参数为新偏好，默认 True
- `timeout` (int, 可选): 超时时间，单位秒，默认无限等待
- `poll_interval` (int, 可选): 轮询间隔，单位秒，默认 2

**返回值**：
- `str`: 任务结果

**异常**：
- `TimeoutError`: 任务超时未完成
- `ExecutionError`: 任务执行失败

#### list_blueprints - 列出蓝图

```python
import magnus

# 列出蓝图
blueprints = magnus.list_blueprints(limit=20)

for bp in blueprints["items"]:
    print(f"{bp['id']} | {bp['title']}")
```

**参数说明**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按标题或 ID 搜索

**返回值**：
- `dict`: `{"total": int, "items": list}`

### 服务调用

#### call_service - 调用弹性服务

```python
import magnus

# 基本用法
response = magnus.call_service("llm-inference", payload={"prompt": "Hello!"})

# 完整参数
response = magnus.call_service(
    "image-generation",
    payload={
        "prompt": "A beautiful sunset over mountains",
        "width": 1024,
        "height": 768,
        "seed": 42,
    },
    timeout=120,  # 请求超时 (秒)
)

print(response)
```

**参数说明**：
- `service_id` (str): 服务 ID
- `payload` (dict): 请求负载，传递给服务的数据
- `timeout` (int, 可选): 请求超时时间，默认 60 秒

**返回值**：
- `dict`: 服务返回的响应数据

**异常**：
- `TimeoutError`: 请求超时
- `MagnusError`: 服务不可用或启动失败

#### list_services - 列出服务

```python
import magnus

# 列出所有服务
services = magnus.list_services(limit=20)

# 仅列出活跃服务
services = magnus.list_services(active_only=True)

for svc in services["items"]:
    print(f"{svc['id']} | {svc['name']} | Active: {svc['is_active']}")
```

**参数说明**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按名称或 ID 搜索
- `active_only` (bool, 可选): 仅返回活跃服务，默认 False

**返回值**：
- `dict`: `{"total": int, "items": list}`

### 任务管理

#### list_jobs - 列出任务

```python
import magnus

# 列出最近 10 个任务
result = magnus.list_jobs()

# 指定数量和搜索条件
result = magnus.list_jobs(
    limit=50,
    search="quadre",  # 按名称搜索
)

for job in result["items"]:
    print(f"{job['id']} | {job['task_name']} | {job['status']}")
```

**参数说明**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按任务名称或 ID 搜索

**返回值**：
- `dict`: `{"total": int, "items": list}`

#### get_job - 获取任务详情

```python
import magnus

job = magnus.get_job("abc123")

print(f"任务名称: {job['task_name']}")
print(f"状态: {job['status']}")
print(f"创建时间: {job['created_at']}")
print(f"SLURM Job ID: {job['slurm_job_id']}")
```

**参数说明**：
- `job_id` (str): Job ID

**返回值**：
- `dict`: 任务详细信息

#### get_job_logs - 获取任务日志

```python
import magnus

# 获取最新一页日志
result = magnus.get_job_logs("abc123")

print(f"Page {result['page'] + 1}/{result['total_pages']}")
print(result["logs"])

# 获取指定页
result = magnus.get_job_logs("abc123", page=0)  # 第一页
```

**参数说明**：
- `job_id` (str): Job ID
- `page` (int, 可选): 页码，-1 表示最新页，默认 -1

**返回值**：
- `dict`: `{"logs": str, "page": int, "total_pages": int}`

#### terminate_job - 终止任务

```python
import magnus

# 终止指定任务
magnus.terminate_job("abc123")

# 终止前检查状态
job = magnus.get_job("abc123")
if job['status'] in ['Pending', 'Running']:
    magnus.terminate_job("abc123")
    print("任务已终止")
```

**参数说明**：
- `job_id` (str): 要终止的 Job ID

### 集群与资源

#### get_cluster_stats - 获取集群状态

```python
import magnus

stats = magnus.get_cluster_stats()

resources = stats["resources"]
print(f"集群: {resources['node']}")
print(f"GPU 型号: {resources['gpu_model']}")
print(f"总数: {resources['total']}")
print(f"空闲: {resources['free']}")
print(f"已用: {resources['used']}")

print(f"运行中任务: {stats['total_running']}")
print(f"排队任务: {stats['total_pending']}")
```

**返回值**：
- `dict`: 包含 `resources`, `running_jobs`, `pending_jobs` 等字段

### 文件传输

Magnus 通过 [croc](https://github.com/schollz/croc) 实现文件传输。蓝图中使用 `FileSecret` 类型声明文件参数，SDK 自动处理发送；蓝图代码中使用 `download_file` 接收。

#### FileSecret 参数 - 自动发送

当蓝图参数类型为 `FileSecret` 时，SDK 的 `submit_blueprint` / `run_blueprint` 会自动检测：如果传入的值是本地文件路径（而非 `magnus-secret:` 格式），SDK 会启动 `croc send`，将路径替换为 croc secret。

```python
import magnus

# 传文件路径，SDK 自动启动 croc send
job_id = magnus.submit_blueprint(
    "my-blueprint",
    args={"input_data": "/home/user/data.csv"},
)
```

#### download_file - 接收文件

在蓝图执行环境中接收文件。

```python
from magnus import download_file

# 下载到指定路径（croc 下载后自动重命名）
download_file(file_secret, "/workspace/data/input.csv")

# 支持相对路径
download_file(file_secret, "data/input.csv")
```

**参数说明**：
- `file_secret` (str): croc secret，可以带或不带 `magnus-secret:` 前缀
- `target_path` (str): 下载后的目标路径（支持相对路径）
- `timeout` (float, 可选): 超时时间（秒），默认无限等待
- `overwrite` (bool, 可选): 是否覆盖已存在的文件，默认 True

**返回值**：
- `Path`: target_path 的 Path 对象

**异常**：
- `MagnusError`: croc 未安装、下载超时、传输失败等

#### download_file_async - 异步接收文件

`download_file` 的异步版本，参数和行为完全一致。

```python
from magnus import download_file_async

path = await download_file_async(file_secret, "/workspace/data/input.csv")
```

### 文件代管

#### custody_file - 代管文件

将本地文件/文件夹上传到 Magnus 后端代管，后端通过 croc 重新托管，返回新的 file_secret。任何人都可以用这个 secret 下载文件，直到过期。

```python
import magnus

# 基本用法
secret = magnus.custody_file("/path/to/results.csv")

# 指定过期时间
secret = magnus.custody_file(
    "/path/to/output_dir",
    expire_minutes=120,  # 2 小时后过期
)

# 在蓝图中使用：将结果文件代管后写入 MAGNUS_RESULT
secret = magnus.custody_file("/workspace/output.tar.gz")
```

**参数说明**：
- `path` (str): 本地文件或文件夹路径
- `expire_minutes` (int, 可选): 过期时间（分钟），默认 60
- `timeout` (float, 可选): HTTP 请求超时时间（秒），默认 300

**返回值**：
- `str`: 新的 file_secret（`magnus-secret:xxxx` 格式），可供 `download_file()` 或 `croc` 使用

**异常**：
- `MagnusError`: croc 未安装、文件不存在、传输失败等

#### custody_file_async - 异步代管文件

`custody_file` 的异步版本，参数和行为完全一致。

```python
from magnus import custody_file_async

secret = await custody_file_async("/path/to/results.csv", expire_minutes=120)
```

#### get_blueprint_schema - 获取蓝图参数 Schema

```python
import magnus

schema = magnus.get_blueprint_schema("my-blueprint")
for param in schema:
    print(f"{param['key']}: {param['type']}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID

**返回值**：
- `list`: 参数 Schema 列表，每项包含 `key`, `type`, `label`, `description` 等字段

### 异步 API

所有同步 API 均有对应的异步版本，函数名添加 `_async` 后缀：

```python
import magnus
import asyncio

async def main():
    # 异步提交蓝图
    job_id = await magnus.submit_blueprint_async(
        "my-blueprint",
        args={"param": "value"},
    )

    # 异步执行并等待
    result = await magnus.run_blueprint_async(
        "my-blueprint",
        args={"param": "value"},
        timeout=300,
    )

    # 异步调用服务
    response = await magnus.call_service_async(
        "my-service",
        payload={"x": 1, "y": 2},
    )

    # 异步任务管理
    jobs = await magnus.list_jobs_async(limit=20)
    job = await magnus.get_job_async("abc123")
    await magnus.terminate_job_async("abc123")

asyncio.run(main())
```

**并发执行多个任务**：

```python
import magnus
import asyncio

async def run_experiments():
    # 并发提交多个任务
    tasks = [
        magnus.submit_blueprint_async("experiment", args={"seed": i})
        for i in range(10)
    ]
    job_ids = await asyncio.gather(*tasks)
    print(f"提交了 {len(job_ids)} 个任务")

asyncio.run(run_experiments())
```

### API 参考

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `submit_blueprint(id, args, ...)` | 提交蓝图任务，立即返回 | Job ID |
| `run_blueprint(id, args, timeout, ...)` | 提交并等待完成 | 任务结果 |
| `list_blueprints(limit, search)` | 列出蓝图 | `{total, items}` |
| `get_blueprint_schema(id)` | 获取蓝图参数 Schema | 参数列表 |
| `call_service(id, payload, timeout)` | 调用弹性服务 | 服务响应 |
| `list_services(limit, search, active_only)` | 列出服务 | `{total, items}` |
| `list_jobs(limit, search)` | 列出任务 | `{total, items}` |
| `get_job(job_id)` | 获取任务详情 | 任务信息 |
| `get_job_logs(job_id, page)` | 获取任务日志 | `{logs, page, total_pages}` |
| `terminate_job(job_id)` | 终止任务 | 状态信息 |
| `get_cluster_stats()` | 获取集群状态 | 集群信息 |
| `download_file(secret, target_path)` | 通过 croc 接收文件 | Path |
| `custody_file(path, expire_minutes)` | 代管文件到后端，返回新 secret | file_secret |
| `configure(token, address)` | 配置 SDK | None |

所有函数均有 `_async` 异步版本（`get_cluster_stats`, `get_job_logs`, `list_blueprints`, `list_services`, `get_blueprint_schema` 除外）。

---

## 命令行工具 (CLI)

Magnus CLI 基于 Typer 构建，提供完整的命令行操作支持。

### 全局选项

```bash
# 查看帮助
magnus --help    # 或 magnus -h

# 查看版本
magnus --version  # 或 magnus -v
```

### magnus config

查看当前 SDK 配置（服务器地址和令牌）。

```bash
magnus config
```

**输出**：
```
  MAGNUS_ADDRESS  http://162.105.151.196:8017
  MAGNUS_TOKEN    sk-a****************ZaB
```

Token 会自动脱敏处理，仅显示首尾各 4 个字符。

### magnus login

交互式配置 `MAGNUS_ADDRESS` 和 `MAGNUS_TOKEN`，验证连通性后保存到 `~/.magnus/config.json`。

```bash
magnus login
```

**交互流程**：
```
[Magnus] Magnus Address (current: http://127.0.0.1:8017): http://162.105.151.134:8017
[Magnus] Magnus Token (current: sk-a****ZaB): sk-xxxxxxxxxx

[Magnus] Connection verified.
[Magnus] Saved to /home/user/.magnus/config.json
```

**行为说明**：
- 显示当前值（token 脱敏），直接回车保留当前值
- 输入后自动验证连通性（调用 `GET /api/auth/my-token`）
- 验证失败会警告但不阻止保存（服务器可能未启动）
- 保存到 `~/.magnus/config.json`，即时生效，无需重启终端
- 配置优先级：环境变量 > 配置文件 > 默认值

### magnus submit

提交蓝图任务，立即返回 Job ID (Fire & Forget)。

```bash
# 基本用法
magnus submit <blueprint-id> [OPTIONS] [-- ARGS...]

# 示例
magnus submit quadre-simulation
magnus submit quadre-simulation --Te 2.0 --B 1.5
magnus submit my-blueprint -- --param value --flag
```

**参数说明**：
- `<blueprint-id>`: 蓝图 ID (必填)
- `-- ARGS...`: 传递给蓝图的参数，使用 `--key value` 格式

**输出**：
```
[Magnus] Submitting blueprint quadre-simulation...
[Magnus] Job submitted. ID: abc123 (use -1 to reference)
```

### magnus run

提交蓝图任务并等待完成 (Submit & Wait)。

```bash
# 基本用法
magnus run <blueprint-id> [OPTIONS] [-- ARGS...]

# 示例
magnus run my-blueprint --timeout 300 -- --param value
magnus run long-task --timeout 3600 --poll-interval 30
```

**选项**：
- `--timeout, -t`: 超时时间 (秒)，默认无限等待
- `--poll-interval`: 轮询间隔 (秒)，默认 2

**输出**：
```
[Magnus] Running blueprint my-blueprint...
[Magnus] Waiting for job completion...
[Magnus] Job finished.
═══════════════════ MAGNUS RESULT ═══════════════════
{...}
═════════════════════════════════════════════════════
```

### magnus call

调用弹性服务。

```bash
# 基本用法
magnus call <service-id> [OPTIONS] [ARGS...]

# 直接传参
magnus call llm-inference --prompt "Hello!" --max_tokens 100

# 从 JSON 文件读取 payload
magnus call my-service @payload.json

# 从 stdin 读取
echo '{"x": 1, "y": 2}' | magnus call my-service -
cat input.json | magnus call my-service -

# 设置超时
magnus call slow-service --timeout 120 -- --param value
```

**参数格式**：
- `--key value`: 直接传参，组装为 JSON payload
- `@file.json`: 从 JSON 文件读取 payload
- `-`: 从 stdin 读取 JSON payload

**选项**：
- `--timeout, -t`: 请求超时时间 (秒)，默认 60

### magnus jobs

列出任务。

```bash
# 基本用法
magnus jobs [OPTIONS]

# 示例
magnus jobs                  # 列出最近 10 个任务
magnus jobs -l 20            # 列出 20 个
magnus jobs -n "quadre"      # 按名称搜索
magnus jobs --format yaml    # YAML 格式输出
magnus jobs | head -20       # 管道时自动切换为 YAML
```

**选项**：
- `-l, --limit`: 显示数量，默认 10
- `-n, --name, -s, --search`: 按任务名称搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

**输出**：
```
        Jobs (10/42)
┏━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━┳━━━━━━━━━━━━┓
┃ Idx ┃ Job ID    ┃ Task            ┃ Status   ┃ GPU ┃ Created    ┃
┡━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━╇━━━━━━━━━━━━┩
│ -1  │ abc123    │ quadre-sim-exp1 │ Running  │ 4   │ 01-15 10:30│
│ -2  │ def456    │ training-resnet │ Success  │ 8   │ 01-15 09:15│
└─────┴───────────┴─────────────────┴──────────┴─────┴────────────┘
[Magnus] Use: magnus status -1, magnus kill -2 -f, ...
```

### magnus status

查看任务详情。支持负数索引快速引用最近的任务。

```bash
# 基本用法
magnus status <job-id | index>

# 示例
magnus status abc123         # 按 Job ID 查看
magnus status -1             # 查看最新任务
magnus status -2             # 查看第二新的任务
```

**负数索引说明**：
- `-1`: 最新提交的任务
- `-2`: 第二新的任务
- `-n`: 第 n 新的任务

**输出**：
```
═══════════════════ Job: abc123 ═══════════════════
  Task:    quadre-simulation-exp1
  Status:  Running
  GPU:     4
  Type:    A2
  Created: 01-15 10:30
  Started: 01-15 10:30
═══════════════════════════════════════════════════
```

### magnus logs

查看任务日志。

```bash
# 基本用法
magnus logs <job-id | index> [OPTIONS]

# 示例
magnus logs -1               # 最新任务的日志
magnus logs -1 --page 0      # 第一页
magnus logs abc123           # 指定任务
```

**选项**：
- `-p, --page`: 页码，-1 表示最新页，默认 -1

**输出**：
```
════════════ Job Logs: abc123 (Page 3/3) ════════════
[2024-01-15 10:30:05] Starting simulation...
[2024-01-15 10:30:10] Loading data...
[2024-01-15 10:31:00] Processing complete.
═════════════════════════════════════════════════════
```

### magnus kill

终止任务。

```bash
# 基本用法
magnus kill <job-id | index> [OPTIONS]

# 示例
magnus kill abc123           # 终止指定任务 (需确认)
magnus kill -1               # 终止最新任务
magnus kill -1 -f            # 跳过确认直接终止
```

**选项**：
- `-f, --force`: 跳过确认提示，直接终止

### magnus cluster

查看集群资源状态。

```bash
# 基本用法
magnus cluster [OPTIONS]

# 示例
magnus cluster               # 表格形式
magnus cluster --format yaml # YAML 输出
```

**选项**：
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

**输出**：
```
═══════════════════════ GPU-Cluster ═══════════════════════
  GPU Model: A100
  Total:     32
  Free:      12
  Used:      20

  Running Jobs: 5
  Pending Jobs: 3
═══════════════════════════════════════════════════════════
```

### magnus blueprints

列出可用蓝图。

```bash
# 基本用法
magnus blueprints [OPTIONS]

# 示例
magnus blueprints            # 列出蓝图
magnus blueprints -l 20      # 列出 20 个
magnus blueprints -s "sim"   # 搜索
magnus blueprints -f yaml    # YAML 输出
```

**选项**：
- `-l, --limit`: 显示数量，默认 10
- `-s, --search`: 按标题或 ID 搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

**输出**：
```
      Blueprints (10/25)
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ ID                  ┃ Title              ┃ Creator    ┃ Updated    ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ quadre-simulation   │ Quadre Simulation  │ zhangsan   │ 01-15 10:30│
│ magnus-debug        │ Magnus Debug       │ lisi       │ 01-14 15:00│
└─────────────────────┴────────────────────┴────────────┴────────────┘
```

### magnus services

列出托管服务。

```bash
# 基本用法
magnus services [OPTIONS]

# 示例
magnus services              # 列出所有服务
magnus services -a           # 仅活跃服务
magnus services -s "llm"     # 搜索
magnus services -f yaml      # YAML 输出
```

**选项**：
- `-l, --limit`: 显示数量，默认 10
- `-s, --search`: 按名称或 ID 搜索
- `-a, --active`: 仅显示活跃服务
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

**输出**：
```
       Services (5/12)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━┳━━━━━━━━━━━━┓
┃ ID                 ┃ Name                 ┃ Active ┃ GPU ┃ Updated    ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━╇━━━━━━━━━━━━┩
│ llm-inference      │ LLM Inference        │ ✓      │ 2   │ 01-15 10:30│
│ image-gen          │ Image Generation     │ -      │ 1   │ 01-14 15:00│
└────────────────────┴──────────────────────┴────────┴─────┴────────────┘
```

### magnus send

通过 croc 发送文件或文件夹。封装 `croc send`，自动检查 croc 是否安装。

```bash
# 基本用法
magnus send <path>

# 示例
magnus send data.csv
magnus send ./my_folder
```

croc 会生成一个 secret code 并显示在终端，接收方使用该 code 接收文件。

### magnus receive

通过 croc 接收文件或文件夹。支持通过 `-o` 指定目标路径（重命名/移动）。

```bash
# 基本用法（文件落到当前目录，保留原始文件名）
magnus receive <secret>

# 指定目标路径（接收并重命名）
magnus receive <secret> -o <target_path>

# 示例
magnus receive 1234-apple-banana-cherry
magnus receive 1234-apple-banana-cherry -o my_data.csv
magnus receive 1234-apple-banana-cherry --output ./downloads/result.tar.gz
```

**选项**：

| 选项 | 说明 |
|------|------|
| `-o`, `--output` | 目标路径（可重命名），不指定则落到当前目录 |

### magnus custody

将文件上传到 Magnus 后端代管。后端接收文件后重新通过 croc 托管，返回新的 secret。

```bash
# 基本用法
magnus custody <path> [OPTIONS]

# 示例
magnus custody results.csv
magnus custody ./output_dir --expire-minutes 120
```

**选项**：
- `-t, --expire-minutes`: 过期时间（分钟），默认 60

**输出**：
```
[Magnus] File custodied successfully. Expires in 60 min.
[Magnus] Secret: magnus-secret:1234-apple-banana-cherry
[Magnus] Download: magnus receive 1234-apple-banana-cherry
[Magnus] Or:       croc 1234-apple-banana-cherry
```

### magnus connect

连接到运行中的 Magnus Debug 会话。用于进入调试任务的交互式环境。

```bash
# 基本用法
magnus connect [JOB_ID]

# 示例
magnus connect           # 自动检测并连接到最新的 debug 任务
magnus connect 12345     # 连接到指定 SLURM Job ID
```

**行为说明**：
- 如果已在 Magnus 会话中（`SLURM_JOB_ID` 已设置），提示并退出
- 如果指定 `JOB_ID`，直接连接到该任务
- 如果未指定 `JOB_ID`，自动检测当前用户的 "Magnus Debug" 任务：
  - 无任务：提示先提交 debug 任务
  - 单个任务：直接连接
  - 多个任务：连接到最新的，并提示其他可用任务

### magnus disconnect

断开当前的 Magnus Debug 会话。

```bash
magnus disconnect
```

**行为说明**：
- 仅在 Magnus 会话内有效（`SLURM_JOB_ID` 已设置）
- 发送 `SIGHUP` 信号到父进程，终止 srun 会话

---

## 附录

### 输出格式

列表类命令 (`jobs`, `blueprints`, `services`, `cluster`) 支持三种输出格式：

| 格式 | 说明 | 适用场景 |
|------|------|----------|
| `table` | Rich 表格，带颜色 | 交互式终端 |
| `yaml` | YAML 格式 | 脚本处理、管道 |
| `json` | JSON 格式 | 程序解析 |

**自动格式选择**：
- 终端交互 (TTY): 默认 `table`
- 管道/重定向: 自动切换为 `yaml`

```bash
# 交互式 - 表格输出
magnus jobs

# 管道 - 自动 YAML
magnus jobs | grep Running

# 强制 JSON
magnus jobs --format json | jq '.items[0]'
```

### 错误代码

| 代码 | 说明 |
|------|------|
| `AUTH_REQUIRED` | 需要认证，请检查 MAGNUS_TOKEN |
| `TOKEN_EXPIRED` | Token 已过期，请重新获取 |
| `BLUEPRINT_NOT_FOUND` | 蓝图不存在 |
| `SERVICE_NOT_FOUND` | 服务不存在 |
| `JOB_NOT_FOUND` | 任务不存在 |
| `VALIDATION_ERROR` | 参数验证失败 |
| `SERVICE_UNAVAILABLE` | 服务不可用 |
| `TIMEOUT` | 请求超时 |

### 常见问题

**Q: 如何获取 MAGNUS_TOKEN？**

A: 在 Web 界面登录后，点击右上角用户头像，即可看到你的 Trust Token（`sk-` 开头）。注意：这不是 JWT 令牌——JWT 用于 Web 会话鉴权，Trust Token 用于 SDK/CLI 鉴权，两者是独立的。

**Q: 环境变量和代码配置哪个优先？**

A: 代码中通过 `magnus.configure()` 设置的配置优先级更高，会覆盖环境变量。

**Q: 异步 API 和同步 API 有什么区别？**

A: 异步 API 适用于需要并发执行多个操作的场景，可以显著提升效率。同步 API 更简单直观，适合简单脚本。

**Q: 负数索引支持哪些命令？**

A: `magnus status`, `magnus logs` 和 `magnus kill` 支持负数索引。`-1` 表示最新任务，`-2` 表示第二新，以此类推。

**Q: call_service 超时后服务还在运行吗？**

A: 是的。`timeout` 只控制客户端等待时间，服务端的请求会继续执行直到完成或达到服务端超时限制。

**Q: connect/disconnect 为什么没有 Python SDK 版本？**

A: `connect` 和 `disconnect` 是终端会话管理命令，通过 `srun` 建立交互式 shell 连接。这类操作本质上是 shell 级别的，无法用 Python 函数调用来替代，因此仅作为 CLI 命令提供。

**Q: 什么是偏好 (Preference)？**

A: 偏好是用户上次运行蓝图时使用的参数缓存。当 `use_preference=True` 时，SDK 会自动合并缓存的参数（显式传入的优先）。当 `save_preference=True` 时，成功运行后会保存当前参数供下次使用。注意：`FileSecret` 类型的参数不会被预填——magnus secret 是一次性凭证，过期后无法复用。

**Q: --format yaml 和管道自动切换有什么区别？**

A: 管道自动切换是智能检测：当输出不是终端时自动使用 YAML。`--format yaml` 是强制指定，即使在终端也输出 YAML。

**Q: FileSecret 文件传输需要什么依赖？**

A: 需要安装 [croc](https://github.com/schollz/croc)。推荐通过 conda 安装：`conda install -c conda-forge croc`。发送端和接收端都需要安装 croc。如果未安装，`magnus send/receive/custody` 会检测并提示安装方式。

**Q: magnus send/receive 和 croc 命令有什么区别？**

A: `magnus send` 封装 `croc send`，`magnus receive` 封装 `croc receive`，自动处理跨平台差异（Linux/macOS 通过环境变量传递 secret，Windows 通过命令行参数）。额外好处：统一入口、友好错误提示、`-o` 支持接收时重命名、未安装 croc 时提示安装方式。
