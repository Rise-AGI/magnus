# Magnus SDK & CLI 完整指南

本文档提供 Magnus Python SDK 和命令行工具 (CLI) 的完整使用说明。

> **设计原则**: CLI 的 API 与 Python SDK（及未来各语言 SDK）保持一致。相同的操作在不同接口中具有相同的语义和参数结构。

---

## 目录

- [Python SDK](#python-sdk)
  - [安装](#安装)
  - [环境配置](#环境配置)
  - [蓝图操作](#蓝图操作)
  - [服务调用](#服务调用)
  - [任务管理](#任务管理)
  - [异步 API](#异步-api)
  - [API 参考](#api-参考)
- [命令行工具 (CLI)](#命令行工具-cli)
  - [全局选项](#全局选项)
  - [magnus submit](#magnus-submit)
  - [magnus run](#magnus-run)
  - [magnus call](#magnus-call)
  - [magnus jobs](#magnus-jobs)
  - [magnus status](#magnus-status)
  - [magnus kill](#magnus-kill)
  - [magnus connect](#magnus-connect)
  - [magnus disconnect](#magnus-disconnect)
- [附录](#附录)
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

SDK 需要两个环境变量：

| 变量 | 说明 | 示例 |
|------|------|------|
| `MAGNUS_TOKEN` | JWT 认证令牌，从 Web 界面获取 | `eyJhbGciOiJIUzI1NiIs...` |
| `MAGNUS_ADDRESS` | Magnus 后端地址 | `http://162.105.151.196:8017` |

```bash
export MAGNUS_TOKEN="your-jwt-token"
export MAGNUS_ADDRESS="http://your-server:8017"
```

也可以在代码中配置：

```python
import magnus

magnus.configure(
    token="your-jwt-token",
    address="http://your-server:8017",
)
```

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

print(f"任务已提交: {job_id}")
```

**参数说明**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数，键值对形式

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
- `timeout` (int, 可选): 超时时间，单位秒，默认 3600
- `poll_interval` (int, 可选): 轮询间隔，单位秒，默认 5

**返回值**：
- `dict`: 包含任务状态和结果的字典

**异常**：
- `TimeoutError`: 任务超时未完成
- `RuntimeError`: 任务执行失败

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
- `ServiceUnavailableError`: 服务不可用或启动失败

### 任务管理

#### list_jobs - 列出任务

```python
import magnus

# 列出最近 10 个任务
jobs = magnus.list_jobs()

# 指定数量和搜索条件
jobs = magnus.list_jobs(
    limit=50,
    search="quadre",  # 按名称搜索
)

for job in jobs:
    print(f"{job['id']} | {job['task_name']} | {job['status']}")
```

**参数说明**：
- `limit` (int, 可选): 返回数量，默认 10
- `search` (str, 可选): 按任务名称搜索

**返回值**：
- `list[dict]`: 任务列表

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
| `submit_blueprint(id, args)` | 提交蓝图任务，立即返回 | Job ID |
| `run_blueprint(id, args, timeout)` | 提交并等待完成 | 任务结果 |
| `call_service(id, payload, timeout)` | 调用弹性服务 | 服务响应 |
| `list_jobs(limit, search)` | 列出任务 | 任务列表 |
| `get_job(job_id)` | 获取任务详情 | 任务信息 |
| `terminate_job(job_id)` | 终止任务 | None |
| `configure(token, address)` | 配置 SDK | None |

所有函数均有 `_async` 异步版本。

---

## 命令行工具 (CLI)

Magnus CLI 基于 Typer 构建，提供完整的命令行操作支持。

### 全局选项

```bash
# 查看帮助
magnus --help

# 查看版本
magnus --version

# 指定配置 (覆盖环境变量)
magnus --token "xxx" --address "http://..." submit my-blueprint
```

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
✓ Job submitted: abc123
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
- `--timeout, -t`: 超时时间 (秒)，默认 3600
- `--poll-interval`: 轮询间隔 (秒)，默认 5

**输出**：
```
✓ Job submitted: abc123
⠋ Waiting for completion... [Running]
✓ Job completed successfully
Result: {...}
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

**输出**：
```json
{
  "result": "...",
  "status": "success"
}
```

### magnus jobs

列出任务。

```bash
# 基本用法
magnus jobs [OPTIONS]

# 示例
magnus jobs                  # 列出最近 10 个任务
magnus jobs -l 20            # 列出 20 个
magnus jobs -n "quadre"      # 按名称搜索
magnus jobs -l 50 -n "train" # 组合使用
```

**选项**：
- `-l, --limit`: 显示数量，默认 10
- `-n, --name`: 按任务名称搜索

**输出**：
```
┌──────────┬─────────────────────┬──────────┬─────────────────────┐
│ ID       │ Task Name           │ Status   │ Created At          │
├──────────┼─────────────────────┼──────────┼─────────────────────┤
│ abc123   │ quadre-sim-exp1     │ Running  │ 2024-01-15 10:30:00 │
│ def456   │ training-resnet     │ Success  │ 2024-01-15 09:15:00 │
│ ghi789   │ data-preprocessing  │ Failed   │ 2024-01-15 08:00:00 │
└──────────┴─────────────────────┴──────────┴─────────────────────┘
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
magnus status -3             # 查看第三新的任务
```

**负数索引说明**：
- `-1`: 最新提交的任务
- `-2`: 第二新的任务
- `-n`: 第 n 新的任务

**输出**：
```
Job Details
───────────────────────────────────────
ID:           abc123
Task Name:    quadre-simulation-exp1
Status:       Running
Priority:     A2
Created:      2024-01-15 10:30:00
Started:      2024-01-15 10:30:05
SLURM Job:    12345678

Repository:   Rise-AGI/quadre
Branch:       main
Commit:       a1b2c3d

Resources:
  GPU Type:   A100
  GPU Count:  4
  CPU Count:  32
  Memory:     128G
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
magnus kill -1 --force       # 同上
```

**选项**：
- `-f, --force`: 跳过确认提示，直接终止

**输出**：
```
⚠ About to terminate job: abc123 (quadre-simulation)
? Are you sure? [y/N]: y
✓ Job terminated
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

**输出**：
```
[Magnus] Connected.
```

多任务时：
```
[Magnus] Connected to latest (12345). Other active: 12344, 12343
```

**典型工作流**：
```bash
# 1. 通过 Web 界面或 CLI 提交 debug 蓝图
magnus submit debug

# 2. 连接到 debug 环境
magnus connect

# 3. 在 debug 环境中工作...

# 4. 退出
magnus disconnect
```

### magnus disconnect

断开当前的 Magnus Debug 会话。

```bash
magnus disconnect
```

**行为说明**：
- 仅在 Magnus 会话内有效（`SLURM_JOB_ID` 已设置）
- 发送 `SIGHUP` 信号到父进程，终止 srun 会话
- 如果不在会话中，提示并退出

**输出**：
```
[Magnus] Disconnected.
```

不在会话中时：
```
[Magnus] Not in a Magnus session.
```

---

## 附录

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

**Q: 如何获取 JWT Token？**

A: 在 Web 界面登录后，可以在浏览器开发者工具的 Application > Local Storage 中找到 `magnus_token`。

**Q: 环境变量和代码配置哪个优先？**

A: 代码中通过 `magnus.configure()` 设置的配置优先级更高，会覆盖环境变量。

**Q: 异步 API 和同步 API 有什么区别？**

A: 异步 API 适用于需要并发执行多个操作的场景，可以显著提升效率。同步 API 更简单直观，适合简单脚本。

**Q: 负数索引支持哪些命令？**

A: `magnus status` 和 `magnus kill` 支持负数索引。`-1` 表示最新任务，`-2` 表示第二新，以此类推。

**Q: 如何调试 SDK 请求？**

A: 可以设置环境变量 `MAGNUS_DEBUG=1` 启用调试模式，会打印所有 HTTP 请求和响应。

**Q: call_service 超时后服务还在运行吗？**

A: 是的。`timeout` 只控制客户端等待时间，服务端的请求会继续执行直到完成或达到服务端超时限制。

**Q: connect/disconnect 为什么没有 Python SDK 版本？**

A: `connect` 和 `disconnect` 是终端会话管理命令，通过 `srun` 建立交互式 shell 连接。这类操作本质上是 shell 级别的，无法用 Python 函数调用来替代，因此仅作为 CLI 命令提供。

**Q: 可以同时连接多个 debug 会话吗？**

A: 可以。在不同的终端窗口中分别运行 `magnus connect <job_id>` 即可连接到不同的任务。

