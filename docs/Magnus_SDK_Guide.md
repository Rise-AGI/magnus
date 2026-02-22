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
  - [直接提交任务](#直接提交任务)
  - [文件传输](#文件传输)
  - [文件代管](#文件代管)
  - [异步 API](#异步-api)
  - [API 参考](#api-参考)
- [命令行工具 (CLI)](#命令行工具-cli)
  - [全局选项](#全局选项)
  - [命令结构](#命令结构)
  - [magnus config](#magnus-config)
  - [magnus login](#magnus-login)
  - [magnus blueprint](#magnus-blueprint)
    - [magnus blueprint list](#magnus-blueprint-list)
    - [magnus blueprint get](#magnus-blueprint-get)
    - [magnus blueprint schema](#magnus-blueprint-schema)
    - [magnus blueprint save](#magnus-blueprint-save)
    - [magnus blueprint delete](#magnus-blueprint-delete)
    - [magnus blueprint launch](#magnus-blueprint-launch)
    - [magnus blueprint run](#magnus-blueprint-run)
  - [magnus job](#magnus-job)
    - [magnus job list](#magnus-job-list)
    - [magnus job status](#magnus-job-status)
    - [magnus job logs](#magnus-job-logs)
    - [magnus job result](#magnus-job-result)
    - [magnus job action](#magnus-job-action)
    - [magnus job kill](#magnus-job-kill)
    - [magnus job submit](#magnus-job-submit)
    - [magnus job execute](#magnus-job-execute)
  - [magnus call](#magnus-call)
  - [magnus cluster](#magnus-cluster)
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
| `MAGNUS_HOME` | 容器内 Magnus 根目录（注意：`$HOME` 由 `--containall` 隔离，指向容器默认值，与 `MAGNUS_HOME` 不同） | `/magnus` |
| `MAGNUS_TOKEN` | 当前用户的 Trust Token，用于容器内调用 Magnus API | `sk-...` |
| `MAGNUS_ADDRESS` | Magnus 后端地址 | `http://162.105.151.196:3011` |
| `MAGNUS_JOB_ID` | 当前任务 ID | `abc123` |
| `MAGNUS_RESULT` | 任务结果文件路径，写入此文件的内容会作为任务结果返回 | `$MAGNUS_HOME/workspace/.magnus_result` |
| `MAGNUS_ACTION` | 任务动作文件路径，写入此文件的命令会在客户端自动执行 | `$MAGNUS_HOME/workspace/.magnus_action` |

工作区位于 `$MAGNUS_HOME/workspace/`，代码仓库在 `$MAGNUS_HOME/workspace/repository/`。

任务以 `--containall` 模式运行，宿主机的 `$HOME`、`/tmp` 和环境变量均不会泄露到容器内。容器内 `$HOME` 指向一个内存临时目录（tmpfs），与 `$MAGNUS_HOME` 无关。容器文件系统通过 Ephemeral Storage overlay 提供可写层（大小可在提交任务时配置），任务结束后自动销毁。

### 蓝图操作

#### launch_blueprint - 提交蓝图任务

提交蓝图任务后立即返回 Job ID，不等待任务完成。

```python
import magnus

# 基本用法
job_id = magnus.launch_blueprint("quadre-simulation")

# 传递参数
job_id = magnus.launch_blueprint(
    "quadre-simulation",
    args={
        "Te": "2.0",
        "B": "1.5",
        "data_dir": "/home/user/outputs/exp1",
    },
)

# 完整参数
job_id = magnus.launch_blueprint(
    "quadre-simulation",
    args={"Te": "2.0"},
    use_preference=False,  # 是否合并用户缓存的偏好参数（SDK 默认不合并）
    save_preference=True,  # 成功后保存参数为新偏好
)

print(f"任务已提交: {job_id}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数，键值对形式
- `use_preference` (bool, 可选): 是否合并已缓存的偏好参数，默认 False（Web UI 默认合并，SDK/CLI 默认不合并）
- `save_preference` (bool, 可选): 成功后是否保存参数为新偏好，默认 True
- `expire_minutes` (int, 可选): FileSecret 自动上传的过期时间（分钟），默认 60
- `max_downloads` (int, 可选): FileSecret 自动上传的最大下载次数，默认 1


#### run_blueprint - 提交并等待完成

提交任务并轮询等待完成，返回任务结果。如果任务写入了 `MAGNUS_ACTION`，默认会在客户端自动执行。

轮询期间具有网络鲁棒性：瞬时网络波动和服务器重启（5xx）会自动重试（指数退避，最多 30 次连续失败），不会丢失等待状态。

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

# 禁用自动执行 action
result = magnus.run_blueprint("my-blueprint", execute_action=False)

print(f"任务结果: {result}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数
- `use_preference` (bool, 可选): 是否合并已缓存的偏好参数，默认 False
- `save_preference` (bool, 可选): 成功后是否保存参数为新偏好，默认 True
- `expire_minutes` (int, 可选): FileSecret 自动上传的过期时间（分钟），默认 60
- `max_downloads` (int, 可选): FileSecret 自动上传的最大下载次数，默认 1
- `timeout` (int, 可选): 超时时间，单位秒，默认无限等待
- `poll_interval` (int, 可选): 轮询间隔，单位秒，默认 2
- `execute_action` (bool, 可选): 是否自动执行 MAGNUS_ACTION，默认 True

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

#### get_blueprint - 获取蓝图详情

获取蓝图的完整信息，包括代码。

```python
import magnus

bp = magnus.get_blueprint("quadre-simulation")

print(f"标题: {bp['title']}")
print(f"描述: {bp['description']}")
print(f"代码:\n{bp['code']}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID

**返回值**：
- `dict`: 蓝图详细信息，包含 `id`, `title`, `description`, `code`, `user_id`, `updated_at`, `user` 等字段

#### save_blueprint - 创建或更新蓝图

后端为 upsert 语义：同 ID 同 owner 更新，新 ID 创建。

```python
import magnus

# 创建新蓝图
bp = magnus.save_blueprint(
    blueprint_id="my-new-blueprint",
    title="My Blueprint",
    description="A test blueprint",
    code=open("blueprint.py").read(),
)
print(f"蓝图已保存: {bp['id']}")

# 更新已有蓝图
bp = magnus.save_blueprint(
    blueprint_id="my-new-blueprint",
    title="My Blueprint v2",
    description="Updated",
    code=open("blueprint_v2.py").read(),
)
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID
- `title` (str): 蓝图标题
- `description` (str): 蓝图描述
- `code` (str): 蓝图 Python 代码

**返回值**：
- `dict`: 保存后的蓝图信息

#### delete_blueprint - 删除蓝图

```python
import magnus

magnus.delete_blueprint("my-old-blueprint")
```

**参数说明**：
- `blueprint_id` (str): 要删除的蓝图 ID

**返回值**：
- `dict`: `{"message": "Blueprint deleted successfully"}`

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

### 直接提交任务

除了通过蓝图提交任务，SDK 也支持直接提交任务（无需预先创建蓝图）。蓝图代码中的 `submit_job()` 与此处的 SDK `submit_job()` 语义一致——蓝图只是在沙箱中调用同一个函数。

#### submit_job - 提交任务

```python
import magnus

job_id = magnus.submit_job(
    task_name="My Experiment",
    entry_command="python train.py --lr 0.001",
    repo_name="my-project",
)
print(f"Job submitted: {job_id}")
```

**参数说明**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_name` | str | 是 | - | 任务名称 |
| `entry_command` | str | 是 | - | 入口命令 |
| `repo_name` | str | 是 | - | 仓库名称 |
| `branch` | str \| None | 否 | None | 分支名（None = 服务端自动检测默认分支） |
| `commit_sha` | str \| None | 否 | None | Commit SHA（None = HEAD） |
| `gpu_type` | str | 否 | "cpu" | GPU 型号（"cpu" 表示不使用 GPU） |
| `gpu_count` | int | 否 | 0 | GPU 数量 |
| `namespace` | str | 否 | "Rise-AGI" | 命名空间 |
| `job_type` | str | 否 | "A2" | 优先级（A1/A2/B1/B2） |
| `description` | str \| None | 否 | None | 任务描述（Markdown） |
| `container_image` | str \| None | 否 | None | 容器镜像（None = 集群默认） |
| `cpu_count` | int \| None | 否 | None | CPU 数量（None = 集群默认） |
| `memory_demand` | str \| None | 否 | None | 内存需求（None = 集群默认） |
| `runner` | str \| None | 否 | None | 运行人（None = 集群默认） |

**返回值**：Job ID (str)

#### execute_job - 提交并等待完成

`submit_job` 的阻塞版本：提交任务后轮询等待完成，支持自动执行 action。

```python
import magnus

result = magnus.execute_job(
    task_name="Quick Test",
    entry_command="echo 'hello world'",
    repo_name="my-project",
    timeout=120,
)
print(f"Result: {result}")
```

**额外参数**（在 `submit_job` 基础上）：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout` | float \| None | None | 等待超时（秒），None = 无限等待 |
| `poll_interval` | float | 2.0 | 轮询间隔（秒） |
| `execute_action` | bool | True | 完成后是否自动执行 action |

**返回值**：任务结果 `Optional[str]`

#### 蓝图代码与 SDK 的关系

蓝图代码中的 `submit_job()` 与 SDK 的 `magnus.submit_job()` 参数一致。蓝图是"可真实运行的代码"——安装 SDK 后，蓝图代码可以直接在本地执行：

```python
# 这段代码既是蓝图，也能直接运行
from magnus import submit_job, JobType
from typing import Annotated

UserName = Annotated[str, {"label": "User Name"}]

def blueprint(user_name: UserName):
    submit_job(
        task_name=f"hello-{user_name}",
        entry_command=f"echo 'Hello, {user_name}!'",
        repo_name="my-project",
        job_type=JobType.A2,
    )

# 本地直接调用
blueprint("alice")
```

### 文件传输

蓝图中使用 `FileSecret` 类型声明文件参数，SDK 自动处理上传；蓝图代码中使用 `download_file` 接收。

#### FileSecret 参数 - 自动上传

当蓝图参数类型为 `FileSecret` 时，SDK 的 `launch_blueprint` / `run_blueprint` 会自动检测：如果传入的值是本地文件路径（而非 `magnus-secret:` 格式），SDK 会自动上传文件到服务器，将路径替换为 file secret。与其他类型一样，`FileSecret` 天然支持 `Optional` 和 `List` 包装。

```python
import magnus

# 单文件：传文件路径，SDK 自动上传到服务器
job_id = magnus.launch_blueprint(
    "my-blueprint",
    args={"input_data": "/home/user/data.csv"},
)

# 多文件 (List[FileSecret])：传路径列表，SDK 逐个上传
job_id = magnus.launch_blueprint(
    "batch-process",
    args={"files": ["/home/user/a.csv", "/home/user/b.csv"]},
)

# 已有 secret 和本地路径可以混用
job_id = magnus.launch_blueprint(
    "batch-process",
    args={"files": ["magnus-secret:abc-123", "/home/user/new.csv"]},
)
```

#### download_file - 接收文件

在蓝图执行环境中接收文件。

```python
from magnus import download_file

# 下载到指定路径
download_file(file_secret, "/workspace/data/input.csv")

# 支持相对路径
download_file(file_secret, "data/input.csv")
```

**参数说明**：
- `file_secret` (str): file secret，可以带或不带 `magnus-secret:` 前缀
- `target_path` (str): 下载后的目标路径（支持相对路径）
- `timeout` (float, 可选): 超时时间（秒），默认无限等待
- `overwrite` (bool, 可选): 是否覆盖已存在的文件，默认 True

**返回值**：
- `Path`: target_path 的 Path 对象

**异常**：
- `MagnusError`: 文件不存在或已过期、下载超时、传输失败等

#### download_file_async - 异步接收文件

`download_file` 的异步版本，参数和行为完全一致。

```python
from magnus import download_file_async

path = await download_file_async(file_secret, "/workspace/data/input.csv")
```

### 文件代管

#### custody_file - 代管文件

将本地文件/文件夹上传到 Magnus 后端代管，返回新的 file_secret。任何人都可以用这个 secret 下载文件，直到过期。

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

# 配合 MAGNUS_ACTION 实现一键传输：
# 蓝图代码中写入 action，客户端 run_blueprint 完成后自动执行
import os
secret = magnus.custody_file("/workspace/processed.pdf")
with open(os.environ["MAGNUS_ACTION"], "w") as f:
    f.write(f"magnus receive {secret} -o processed.pdf\n")
```

**参数说明**：
- `path` (str): 本地文件或文件夹路径
- `expire_minutes` (int, 可选): 过期时间（分钟），默认 60
- `max_downloads` (int, 可选): 最大下载次数，默认无限制
- `timeout` (float, 可选): HTTP 请求超时时间（秒），默认 300

**返回值**：
- `str`: 新的 file_secret（`magnus-secret:xxxx` 格式），可供 `download_file()` 使用

**异常**：
- `MagnusError`: 文件不存在、存储空间不足等

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
    job_id = await magnus.launch_blueprint_async(
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
        magnus.launch_blueprint_async("experiment", args={"seed": i})
        for i in range(10)
    ]
    job_ids = await asyncio.gather(*tasks)
    print(f"提交了 {len(job_ids)} 个任务")

asyncio.run(run_experiments())
```

### API 参考

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `submit_job(task_name, entry_command, repo_name, ...)` | 直接提交任务，立即返回 | Job ID |
| `execute_job(task_name, entry_command, repo_name, ...)` | 直接提交并等待完成 | 任务结果 |
| `launch_blueprint(id, args, ...)` | 提交蓝图任务，立即返回 | Job ID |
| `run_blueprint(id, args, timeout, ...)` | 提交并等待完成，自动执行 action | 任务结果 |
| `list_blueprints(limit, search)` | 列出蓝图 | `{total, items}` |
| `get_blueprint(id)` | 获取蓝图详情（含代码） | 蓝图信息 |
| `save_blueprint(id, title, description, code)` | 创建或更新蓝图 (upsert) | 蓝图信息 |
| `delete_blueprint(id)` | 删除蓝图 | 确认消息 |
| `get_blueprint_schema(id)` | 获取蓝图参数 Schema | 参数列表 |
| `call_service(id, payload, timeout)` | 调用弹性服务 | 服务响应 |
| `list_services(limit, search, active_only)` | 列出服务 | `{total, items}` |
| `list_jobs(limit, search)` | 列出任务 | `{total, items}` |
| `get_job(job_id)` | 获取任务详情 | 任务信息 |
| `get_job_result(job_id)` | 获取任务结果 | `Optional[str]` |
| `get_job_action(job_id)` | 获取任务动作 | `Optional[str]` |
| `get_job_logs(job_id, page)` | 获取任务日志 | `{logs, page, total_pages}` |
| `terminate_job(job_id)` | 终止任务 | 状态信息 |
| `get_cluster_stats()` | 获取集群状态 | 集群信息 |
| `download_file(secret, target_path)` | 接收文件 | Path |
| `custody_file(path, expire_minutes, max_downloads)` | 代管文件到后端，返回新 secret | file_secret |
| `configure(token, address)` | 配置 SDK | None |

所有函数均有 `_async` 异步版本（`get_cluster_stats`, `get_job_logs`, `list_blueprints`, `list_services`, `get_blueprint_schema` 除外）。

---

## 命令行工具 (CLI)

Magnus CLI 基于 Typer 构建，提供完整的命令行操作支持。

### 命令结构

CLI 有两层命令结构：

**顶层快捷命令** — 高频操作的简写，永久保留：

```bash
magnus launch <id>       # 提交蓝图 (Fire & Forget)
magnus run <id>          # 提交蓝图并等待完成
magnus jobs              # 列出任务
magnus status <ref>      # 查看任务详情
magnus logs <ref>        # 查看任务日志
magnus kill <ref>        # 终止任务
magnus blueprints        # 列出蓝图
magnus submit ...        # 直接提交任务
magnus execute ...       # 直接提交并等待完成
```

**宾动结构子命令** — 完整的 CRUD 操作，类似 `git remote add`：

```bash
magnus blueprint list              # = magnus blueprints
magnus blueprint get <id>          # 查看蓝图详情（含代码）
magnus blueprint schema <id>       # 查看参数 Schema
magnus blueprint save <id> ...     # 创建/更新蓝图
magnus blueprint delete <id>       # 删除蓝图
magnus blueprint launch <id>       # = magnus launch <id>
magnus blueprint run <id>          # = magnus run <id>

magnus job list                    # = magnus jobs
magnus job status <ref>            # = magnus status <ref>
magnus job logs <ref>              # = magnus logs <ref>
magnus job result <ref>            # 单独查看任务结果
magnus job action <ref>            # 单独查看任务动作
magnus job kill <ref>              # = magnus kill <ref>
magnus job submit ...              # = magnus submit ...
magnus job execute ...             # = magnus execute ...
```

顶层快捷命令与宾动子命令完全等价，选哪个纯粹看个人偏好。`blueprint get/save/delete`、`job result/action` 等 CRUD 操作只在宾动结构下提供。

不变的顶层命令：`config`, `login`, `logout`, `call`, `cluster`, `services`, `send`, `receive`, `custody`, `connect`, `disconnect`

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

### magnus blueprint

蓝图操作子命令组。

```bash
magnus blueprint --help        # 查看所有子命令
```

#### magnus blueprint list

列出可用蓝图。

```bash
magnus blueprint list                    # 列出蓝图
magnus blueprint list -l 20              # 列出 20 个
magnus blueprint list -s "sim"           # 搜索
magnus blueprint list -f yaml            # YAML 输出
```

**选项**：
- `-l, --limit`: 显示数量，默认 10
- `-s, --search`: 按标题或 ID 搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

#### magnus blueprint get

查看蓝图详情，包括完整代码。

```bash
magnus blueprint get <blueprint-id>
magnus blueprint get my-blueprint -f yaml
```

**选项**：
- `-f, --format`: 输出格式 (yaml/json)，默认人类可读

**输出**：
```
════════════════ Blueprint: my-blueprint ════════════════
  Title:       My Blueprint
  Description: A simulation blueprint
  Creator:     zhangsan
  Updated:     01-15 10:30

═══════════════════════ Code ═══════════════════════════
from magnus import submit_job
def blueprint(param: str):
    submit_job(...)
════════════════════════════════════════════════════════
```

#### magnus blueprint schema

查看蓝图参数 Schema。

```bash
magnus blueprint schema <blueprint-id>
magnus blueprint schema my-blueprint -f json
```

**选项**：
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

#### magnus blueprint save

创建或更新蓝图（upsert 语义）。

```bash
magnus blueprint save <id> --title "标题" --code-file blueprint.py
magnus blueprint save my-bp -t "My BP" -d "描述" -c ./src/bp.py
```

**参数**：
- `<id>`: 蓝图 ID (必填)

**选项**：
- `-t, --title`: 蓝图标题 (必填)
- `-d, --description, --desc`: 蓝图描述，默认空
- `-c, --code-file`: Python 源文件路径 (必填)

#### magnus blueprint delete

删除蓝图。

```bash
magnus blueprint delete <id>             # 交互确认
magnus blueprint delete my-blueprint -f  # 跳过确认
```

**选项**：
- `-f, --force`: 跳过确认

#### magnus blueprint launch

提交蓝图任务，立即返回 Job ID (Fire & Forget)。

```bash
# 基本用法
magnus blueprint launch <blueprint-id> [OPTIONS] [-- ARGS...]

# 示例
magnus blueprint launch quadre-simulation
magnus blueprint launch quadre-simulation --Te 2.0 --B 1.5
magnus blueprint launch my-blueprint -- --param value --flag
magnus blueprint launch my-blueprint --expire-minutes 120 -- --data /path/to/file

# 多文件参数 (List[FileSecret])：重复 flag 自动收集为列表
magnus blueprint launch batch-process -- --files a.csv --files b.csv
```

**参数说明**：
- `<blueprint-id>`: 蓝图 ID (必填)
- `-- ARGS...`: 传递给蓝图的参数，使用 `--key value` 格式

**选项**（防波堤 `--` 左侧）：
- `--expire-minutes`: FileSecret 自动上传的过期时间（分钟），默认 60
- `--max-downloads`: FileSecret 自动上传的最大下载次数，默认 1
- `--preference`: 是否合并用户缓存的偏好参数，默认 false
- `--timeout`: HTTP 请求超时时间（秒），默认 10

#### magnus blueprint run

提交蓝图任务并等待完成 (Submit & Wait)。任务完成后，如果蓝图写入了 `MAGNUS_ACTION`，CLI 会自动在客户端执行。

轮询期间具有网络鲁棒性：瞬时网络波动和服务器重启（5xx）会自动重试（指数退避，最多 30 次），不会丢失等待状态。

```bash
# 基本用法
magnus blueprint run <blueprint-id> [OPTIONS] [-- ARGS...]

# 示例
magnus blueprint run my-blueprint --timeout 300 -- --param value
magnus blueprint run long-task --timeout 3600 --poll-interval 30
magnus blueprint run my-blueprint --expire-minutes 120 -- --data /path/to/file

# 一键处理文件（蓝图内部写 MAGNUS_ACTION 实现自动下载）
magnus blueprint run scan-pdf-to-vector --file original.pdf --output processed.pdf

# 禁用自动执行 action
magnus blueprint run my-blueprint --execute-action false -- --param value
```

**选项**（防波堤 `--` 左侧）：
- `--timeout`: 超时时间 (秒)，默认无限等待
- `--poll-interval`: 轮询间隔 (秒)，默认 2
- `--execute-action`: 是否自动执行 MAGNUS_ACTION，默认 true
- `--expire-minutes`: FileSecret 自动上传的过期时间（分钟），默认 60
- `--max-downloads`: FileSecret 自动上传的最大下载次数，默认 1
- `--preference`: 是否合并用户缓存的偏好参数，默认 false

### magnus job

任务操作子命令组。

```bash
magnus job --help              # 查看所有子命令
```

#### magnus job list

列出任务。

```bash
magnus job list                          # 列出最近 10 个任务
magnus job list -l 20                    # 列出 20 个
magnus job list -n "quadre"              # 按名称搜索
magnus job list --format yaml            # YAML 格式输出
magnus job list | head -20               # 管道时自动切换为 YAML
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
[Magnus] Use: magnus job status -1, magnus job kill -2 -f, ...
```

#### magnus job status

查看任务详情。支持负数索引快速引用最近的任务。

```bash
magnus job status abc123         # 按 Job ID 查看
magnus job status -1             # 查看最新任务
magnus job status -2             # 查看第二新的任务
```

**负数索引说明**：
- `-1`: 最新提交的任务
- `-2`: 第二新的任务
- `-n`: 第 n 新的任务

#### magnus job logs

查看任务日志。

```bash
magnus job logs -1               # 最新任务的日志
magnus job logs -1 --page 0      # 第一页
magnus job logs abc123           # 指定任务
```

**选项**：
- `-p, --page`: 页码，-1 表示最新页，默认 -1

#### magnus job result

单独查看任务结果。

```bash
magnus job result -1             # 最新任务的结果
magnus job result abc123         # 指定任务
```

结果为 JSON 时自动格式化输出，否则原样显示。

#### magnus job action

单独查看任务动作脚本。

```bash
magnus job action -1             # 查看最新任务的 action
magnus job action -1 -e          # 查看并执行
magnus job action abc123         # 指定任务
```

**选项**：
- `-e, --execute`: 执行 action 脚本

#### magnus job kill

终止任务。

```bash
magnus job kill abc123           # 终止指定任务 (需确认)
magnus job kill -1               # 终止最新任务
magnus job kill -1 -f            # 跳过确认直接终止
```

**选项**：
- `-f, --force`: 跳过确认提示，直接终止

#### magnus job submit

直接提交任务 (Fire & Forget)，无需预先创建蓝图。

```bash
magnus job submit --task-name "Train" --repo-name my_repo --branch main \
  --commit-sha HEAD --entry-command "python train.py" --gpu-type A100 --gpu-count 4
```

#### magnus job execute

直接提交任务并等待完成。与 `magnus blueprint run` 类似，轮询期间具有网络鲁棒性。

```bash
magnus job execute --task-name "Quick Test" --repo-name my_repo --branch main \
  --commit-sha HEAD --entry-command "echo hello"
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

上传文件或文件夹到 Magnus 服务器，返回 file secret 供接收方下载。

```bash
# 基本用法
magnus send <path>

# 示例
magnus send data.csv
magnus send ./my_folder
magnus send data.csv --max-downloads 3
```

**选项**：
- `-t, --expire-minutes`: 过期时间（分钟），默认 60
- `-d, --max-downloads`: 最大下载次数，默认 1

上传完成后会显示 file secret，接收方使用该 secret 接收文件。

### magnus receive

从 Magnus 服务器下载文件或文件夹。支持通过 `-o` 指定目标路径（重命名/移动）。

```bash
# 基本用法（文件落到当前目录，保留原始文件名）
magnus receive <secret>

# 指定目标路径（接收并重命名）
magnus receive <secret> -o <target_path>

# 示例
magnus receive 7919-calm-boat-fire
magnus receive 7919-calm-boat-fire -o my_data.csv
magnus receive 7919-calm-boat-fire --output ./downloads/result.tar.gz
```

**选项**：

| 选项 | 说明 |
|------|------|
| `-o`, `--output` | 目标路径（可重命名），不指定则落到当前目录 |

### magnus custody

将文件上传到 Magnus 后端代管，返回新的 file secret。

```bash
# 基本用法
magnus custody <path> [OPTIONS]

# 示例
magnus custody results.csv
magnus custody ./output_dir --expire-minutes 120
```

**选项**：
- `-t, --expire-minutes`: 过期时间（分钟），默认 60
- `-d, --max-downloads`: 最大下载次数，默认无限制

**输出**：
```
[Magnus] File custodied successfully. Expires in 60 min.
[Magnus] Download: magnus receive magnus-secret:7919-calm-boat-fire
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

列表类命令 (`job list`, `blueprint list`, `services`, `cluster`) 支持三种输出格式：

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
magnus job list

# 管道 - 自动 YAML
magnus job list | grep Running

# 强制 JSON
magnus job list --format json | jq '.items[0]'
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

A: `magnus job status`, `magnus job logs` 和 `magnus job kill` 支持负数索引。`-1` 表示最新任务，`-2` 表示第二新，以此类推。新增的 `magnus job result` 和 `magnus job action` 也支持。

**Q: call_service 超时后服务还在运行吗？**

A: 是的。`timeout` 只控制客户端等待时间，服务端的请求会继续执行直到完成或达到服务端超时限制。

**Q: connect/disconnect 为什么没有 Python SDK 版本？**

A: `connect` 和 `disconnect` 是终端会话管理命令，通过 `srun` 建立交互式 shell 连接。这类操作本质上是 shell 级别的，无法用 Python 函数调用来替代，因此仅作为 CLI 命令提供。

**Q: 什么是偏好 (Preference)？**

A: 偏好是用户上次运行蓝图时使用的参数缓存。当 `use_preference=True` 时，会自动合并缓存的参数（显式传入的优先）。当 `save_preference=True` 时，成功运行后会保存当前参数供下次使用。SDK/CLI 默认 `use_preference=False`（避免不可见的外部状态），Web UI 默认合并。`FileSecret` 类型的参数也会被缓存，但 secret 有 TTL，过期后需重新上传。

**Q: --format yaml 和管道自动切换有什么区别？**

A: 管道自动切换是智能检测：当输出不是终端时自动使用 YAML。`--format yaml` 是强制指定，即使在终端也输出 YAML。

**Q: FileSecret 文件传输需要什么依赖？**

A: 无额外依赖。安装 Magnus SDK（`pip install magnus-sdk`）后即可使用 `magnus send/receive/custody` 以及 `download_file()`。文件通过 Magnus 服务器中转，只需配置好 `MAGNUS_ADDRESS` 和 `MAGNUS_TOKEN`。

**Q: 顶层快捷命令和宾动子命令有什么关系？**

A: 完全等价。`magnus run <id>` 和 `magnus blueprint run <id>` 执行的是同一段代码。高频操作用顶层快捷命令更简洁，CRUD 管理操作（`blueprint get/save/delete`、`job result/action`）只在宾动结构下提供。

**Q: `run` / `execute` 这类阻塞命令断网了怎么办？**

A: 轮询期间遇到瞬时网络波动（DNS 抖动、连接重置、服务器重启导致的 5xx 等）会自动指数退避重试（2s → 4s → 8s → ... → 最大 30s），最多容忍 30 次连续失败（约 13 分钟）。超过后报错并提示 `magnus job status <id>` 检查任务状态——任务不会因为客户端断线而终止。
