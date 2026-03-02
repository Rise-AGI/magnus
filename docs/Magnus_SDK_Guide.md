# Magnus SDK & CLI 指南

CLI 的 API 与 Python SDK 保持一致。相同的操作在不同接口中具有相同的语义和参数结构。

---

## 目录

- [Python SDK](#python-sdk)
  - [安装](#安装)
  - [环境配置](#环境配置)
  - [配置优先级](#配置优先级)
  - [容器内环境变量](#容器内环境变量)
  - [任务 (Job)](#任务-job)
  - [蓝图 (Blueprint)](#蓝图-blueprint)
  - [服务调用](#服务调用)
  - [集群与资源](#集群与资源)
  - [文件传输](#文件传输)
  - [文件代管](#文件代管)
  - [技能 (Skill)](#技能-skill)
  - [镜像 (Image)](#镜像-image)
  - [异步 API](#异步-api)
  - [API 参考](#api-参考)
- [命令行工具 (CLI)](#命令行工具-cli)
  - [命令结构](#命令结构)
  - [全局选项](#全局选项)
  - [magnus config](#magnus-config)
  - [magnus login](#magnus-login)
  - [magnus job](#magnus-job)
    - [magnus job submit](#magnus-job-submit)
    - [magnus job execute](#magnus-job-execute)
    - [magnus job list](#magnus-job-list)
    - [magnus job status](#magnus-job-status)
    - [magnus job logs](#magnus-job-logs)
    - [magnus job result](#magnus-job-result)
    - [magnus job action](#magnus-job-action)
    - [magnus job kill](#magnus-job-kill)
  - [magnus blueprint](#magnus-blueprint)
    - [magnus blueprint list](#magnus-blueprint-list)
    - [magnus blueprint get](#magnus-blueprint-get)
    - [magnus blueprint schema](#magnus-blueprint-schema)
    - [magnus blueprint save](#magnus-blueprint-save)
    - [magnus blueprint delete](#magnus-blueprint-delete)
    - [magnus blueprint launch](#magnus-blueprint-launch)
    - [magnus blueprint run](#magnus-blueprint-run)
  - [magnus call](#magnus-call)
  - [magnus cluster](#magnus-cluster)
  - [magnus services](#magnus-services)
  - [magnus skill](#magnus-skill)
    - [magnus skill list](#magnus-skill-list)
    - [magnus skill get](#magnus-skill-get)
    - [magnus skill save](#magnus-skill-save)
    - [magnus skill delete](#magnus-skill-delete)
  - [magnus image](#magnus-image)
    - [magnus image list](#magnus-image-list)
    - [magnus image pull](#magnus-image-pull)
    - [magnus image refresh](#magnus-image-refresh)
    - [magnus image remove](#magnus-image-remove)
  - [magnus refresh](#magnus-refresh)
  - [magnus skills](#magnus-skills)
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
pip install magnus-sdk        # pip
uv add magnus-sdk             # uv
cd sdks/python && pip install -e .  # 源码
```

### 环境配置

SDK 通过两个配置项连接后端：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `address` | 后端地址 | `http://162.105.151.196:8017` |
| `token` | Trust Token，从 Web 界面用户菜单获取 | `sk-aBcDeFgHiJkLmNoPqRsTuVwXyZaB` |

交互式配置（保存到 `~/.magnus/config.json`）：

```bash
magnus login
```

环境变量（优先级高于配置文件）：

```bash
export MAGNUS_TOKEN="sk-your-trust-token"
export MAGNUS_ADDRESS="http://your-server:8017"
```

代码中配置（优先级最高）：

```python
import magnus

magnus.configure(
    token="sk-your-trust-token",
    address="http://your-server:8017",
)
```

### 配置优先级

按以下顺序解析，先找到的生效：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 (最高) | `magnus.configure()` 显式传参 | 仅影响当前进程 |
| 2 | 环境变量 `MAGNUS_TOKEN` / `MAGNUS_ADDRESS` | CI/CD、容器内注入、`.bashrc` |
| 3 | `~/.magnus/config.json` | `magnus login` 写入，跨 shell 生效 |
| 4 (最低) | 默认值 | address 默认 `http://127.0.0.1:8017`，token 默认为空 |

备注：

- `magnus login` 一次配置，所有终端立即可用，无需 `source` 或重启终端。
- 已在 `.bashrc` 中 `export MAGNUS_TOKEN=...` 的用户不受影响，环境变量优先级更高。
- Magnus 在容器内执行任务时注入 `MAGNUS_TOKEN` 环境变量，因此 Job-in-Job 场景下内层任务的 SDK 调用不需要额外配置。
- `magnus config` 命令显示每个配置项的实际来源（env / file / default）。

### 容器内环境变量

任务在 Apptainer 容器内执行时，以下环境变量自动注入：

| 变量 | 说明 | 示例值 |
|------|------|--------|
| `MAGNUS_HOME` | 容器内 Magnus 根目录（`$HOME` 由 `--containall` 隔离，指向容器默认值，与此不同） | `/magnus` |
| `MAGNUS_TOKEN` | 当前用户的 Trust Token | `sk-...` |
| `MAGNUS_ADDRESS` | 后端地址 | `http://162.105.151.196:3011` |
| `MAGNUS_JOB_ID` | 当前任务 ID | `abc123` |
| `MAGNUS_RESULT` | 结果文件路径，写入此文件的内容作为任务结果返回 | `$MAGNUS_HOME/workspace/.magnus_result` |
| `MAGNUS_ACTION` | 动作文件路径，写入此文件的命令会在客户端自动执行 | `$MAGNUS_HOME/workspace/.magnus_action` |

工作区位于 `$MAGNUS_HOME/workspace/`，代码仓库在 `$MAGNUS_HOME/workspace/repository/`。

任务以 `--containall` 模式运行，宿主机的 `$HOME`、`/tmp` 和环境变量不会泄露到容器内。容器内 `$HOME` 指向一个内存临时目录（tmpfs），与 `$MAGNUS_HOME` 无关。容器文件系统通过 Ephemeral Storage overlay 提供可写层（大小可在提交任务时配置），任务结束后销毁。

### 任务 (Job)

Job 是 Magnus 的基本执行单元。每个 Job 包含一条入口命令、一个代码仓库引用和资源配置，在集群容器中执行。

#### submit_job - 提交任务

提交后立即返回 Job ID，不等待完成。

```python
import magnus

job_id = magnus.submit_job(
    task_name="My Experiment",
    entry_command="python train.py --lr 0.001",
    repo_name="my-project",
)
print(job_id)
```

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_name` | str | 是 | - | 任务名称 |
| `entry_command` | str | 是 | - | 入口命令 |
| `repo_name` | str | 是 | - | 仓库名称 |
| `branch` | str \| None | 否 | None | 分支名（None = 服务端检测默认分支） |
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

`submit_job` 的阻塞版本：提交后轮询等待完成。轮询期间遇到瞬时网络错误或 5xx 会自动指数退避重试。

```python
import magnus

result = magnus.execute_job(
    task_name="Quick Test",
    entry_command="echo 'hello world'",
    repo_name="my-project",
    timeout=120,
)
print(result)
```

**额外参数**（在 `submit_job` 基础上）：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timeout` | float \| None | None | 等待超时（秒），None = 无限等待 |
| `poll_interval` | float | 2.0 | 轮询间隔（秒） |
| `execute_action` | bool | True | 完成后是否自动执行 action |

**返回值**：`Optional[str]`，任务写入 `MAGNUS_RESULT` 的内容

**异常**：
- `TimeoutError`: 超时
- `ExecutionError`: 任务状态为 Failed 或 Terminated

#### list_jobs - 列出任务

```python
import magnus

result = magnus.list_jobs(limit=50, search="quadre")

for job in result["items"]:
    print(f"{job['id']} | {job['task_name']} | {job['status']}")
```

**参数**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按任务名称或 ID 搜索

**返回值**：`{"total": int, "items": list}`

#### get_job - 获取任务详情

```python
import magnus

job = magnus.get_job("abc123")

print(f"任务: {job['task_name']}")
print(f"状态: {job['status']}")
print(f"SLURM Job ID: {job['slurm_job_id']}")
```

**参数**：
- `job_id` (str): Job ID

**返回值**：dict，包含 `id`, `task_name`, `status`, `entry_command`, `repo_name`, `gpu_type`, `gpu_count`, `slurm_job_id`, `created_at`, `start_time`, `result`, `action` 等字段

#### get_job_result - 获取任务结果

读取任务写入 `MAGNUS_RESULT` 文件的内容。任务未写入结果时返回 None。

```python
import magnus

result = magnus.get_job_result("abc123")
if result is not None:
    print(result)
```

**参数**：
- `job_id` (str): Job ID
- `timeout` (float, 可选): HTTP 超时（秒），默认 10

**返回值**：`Optional[str]`

#### get_job_action - 获取任务动作

读取任务写入 `MAGNUS_ACTION` 文件的内容。该内容通常是一段 shell 命令，由客户端执行。

```python
import magnus

action = magnus.get_job_action("abc123")
if action is not None:
    print(action)
```

**参数**：
- `job_id` (str): Job ID
- `timeout` (float, 可选): HTTP 超时（秒），默认 10

**返回值**：`Optional[str]`

#### get_job_logs - 获取任务日志

```python
import magnus

result = magnus.get_job_logs("abc123")
print(f"Page {result['page'] + 1}/{result['total_pages']}")
print(result["logs"])

# 获取第一页
result = magnus.get_job_logs("abc123", page=0)
```

**参数**：
- `job_id` (str): Job ID
- `page` (int, 可选): 页码，-1 表示最新页，默认 -1

**返回值**：`{"logs": str, "page": int, "total_pages": int}`

#### terminate_job - 终止任务

```python
import magnus

magnus.terminate_job("abc123")
```

**参数**：
- `job_id` (str): Job ID

> **Admin 特权**：管理员（`feishu_client.admins` 中配置的用户）可以终止任何人的任务，非管理员只能终止自己的任务。

#### 典型工作流

提交 → 轮询 → 查日志 → 取结果，完整闭环：

```python
import magnus
import time

# 1. 提交
job_id = magnus.submit_job(
    task_name="train-resnet",
    entry_command="python train.py --epochs 50",
    repo_name="ml-experiments",
    gpu_type="A100",
    gpu_count=4,
    job_type="A2",
)

# 2. 等待完成
while True:
    job = magnus.get_job(job_id)
    status = job["status"]
    if status in ("Success", "Failed", "Terminated"):
        break
    time.sleep(5)

# 3. 查日志
logs = magnus.get_job_logs(job_id)
print(logs["logs"])

# 4. 取结果
if status == "Success":
    result = magnus.get_job_result(job_id)
    print(result)

    action = magnus.get_job_action(job_id)
    if action:
        print(f"Action: {action}")
```

或者用 `execute_job` 一步到位：

```python
result = magnus.execute_job(
    task_name="train-resnet",
    entry_command="python train.py --epochs 50",
    repo_name="ml-experiments",
    gpu_type="A100",
    gpu_count=4,
)
```

### 蓝图 (Blueprint)

蓝图是对 Job 的封装。一个蓝图就是一个定义了 `blueprint()` 函数的 Python 文件，函数签名中的参数通过 `Annotated` 类型注解映射为前端表单。蓝图内部调用 `submit_job()` 提交实际任务。

蓝图代码中的 `submit_job()` 与 SDK 的 `magnus.submit_job()` 参数一致。安装 SDK 后，蓝图代码可以直接在本地执行：

```python
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

# 直接运行
blueprint("alice")
```

#### launch_blueprint - 提交蓝图任务

提交后立即返回 Job ID。

```python
import magnus

job_id = magnus.launch_blueprint("quadre-simulation")

# 带参数
job_id = magnus.launch_blueprint(
    "quadre-simulation",
    args={
        "Te": "2.0",
        "B": "1.5",
        "data_dir": "/home/user/outputs/exp1",
    },
)

# 合并用户偏好参数
job_id = magnus.launch_blueprint(
    "quadre-simulation",
    args={"Te": "2.0"},
    use_preference=True,
)
```

**参数**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数
- `use_preference` (bool, 可选): 是否合并用户缓存的偏好参数，默认 False（Web UI 默认 True）
- `save_preference` (bool, 可选): 成功后保存参数为偏好，默认 True
- `expire_minutes` (int, 可选): FileSecret 自动上传的过期时间（分钟），默认 60
- `max_downloads` (int, 可选): FileSecret 自动上传的最大下载次数，默认 1

**返回值**：Job ID (str)

参数校验失败时，错误信息会附带蓝图的完整参数 Schema（包含各参数的类型、合法值和描述）。

#### run_blueprint - 提交并等待完成

提交后轮询等待完成，返回结果。轮询期间遇到瞬时网络错误或 5xx 会自动指数退避重试。任务写入 `MAGNUS_ACTION` 时，默认在客户端自动执行。

```python
import magnus

result = magnus.run_blueprint("my-blueprint", args={"param": "value"})

# 超时和轮询间隔
result = magnus.run_blueprint(
    "long-running-task",
    args={"input": "data"},
    timeout=3600,
    poll_interval=10,
)

# 不自动执行 action
result = magnus.run_blueprint("my-blueprint", execute_action=False)
```

**参数**：
- `blueprint_id` (str): 蓝图 ID
- `args` (dict, 可选): 传递给蓝图函数的参数
- `use_preference` (bool, 可选): 合并偏好参数，默认 False
- `save_preference` (bool, 可选): 保存为偏好，默认 True
- `expire_minutes` (int, 可选): FileSecret 过期时间（分钟），默认 60
- `max_downloads` (int, 可选): FileSecret 最大下载次数，默认 1
- `timeout` (float, 可选): 超时时间（秒），默认无限等待
- `poll_interval` (float, 可选): 轮询间隔（秒），默认 2
- `execute_action` (bool, 可选): 自动执行 action，默认 True

**返回值**：`Optional[str]`

**异常**：
- `TimeoutError`: 超时
- `ExecutionError`: 任务失败

#### list_blueprints - 列出蓝图

```python
import magnus

blueprints = magnus.list_blueprints(limit=20)

for bp in blueprints["items"]:
    print(f"{bp['id']} | {bp['title']}")
```

**参数**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按标题或 ID 搜索

**返回值**：`{"total": int, "items": list}`

#### get_blueprint - 获取蓝图详情

```python
import magnus

bp = magnus.get_blueprint("quadre-simulation")

print(bp["title"])
print(bp["description"])
print(bp["code"])
```

**参数**：
- `blueprint_id` (str): 蓝图 ID

**返回值**：dict，包含 `id`, `title`, `description`, `code`, `user_id`, `updated_at`, `user` 等字段

#### get_blueprint_schema - 获取参数 Schema

返回蓝图函数签名中各参数的类型、约束和描述。`Literal` 类型的参数会包含完整的 `options` 列表。

```python
import magnus

schema = magnus.get_blueprint_schema("my-blueprint")
for param in schema:
    print(f"{param['key']}: {param['type']}")
    if param.get("options"):
        for opt in param["options"]:
            print(f"  {opt['value']}: {opt.get('description', '')}")
```

**参数**：
- `blueprint_id` (str): 蓝图 ID

**返回值**：list，每项包含 `key`, `type`, `label`, `description`, `default`, `is_optional`, `is_list`, `options` 等字段

#### save_blueprint - 创建或更新蓝图

后端为 upsert 语义：同 ID 同 owner 更新，新 ID 创建。

```python
import magnus

bp = magnus.save_blueprint(
    blueprint_id="my-new-blueprint",
    title="My Blueprint",
    description="A test blueprint",
    code=open("blueprint.py").read(),
)
```

**参数**：
- `blueprint_id` (str): 蓝图 ID
- `title` (str): 标题
- `description` (str): 描述
- `code` (str): Python 代码

**返回值**：dict，保存后的蓝图信息

> **Admin 特权**：管理员可以覆盖更新任何人的蓝图，非管理员只能更新自己的蓝图。

#### delete_blueprint - 删除蓝图

```python
import magnus

magnus.delete_blueprint("my-old-blueprint")
```

**参数**：
- `blueprint_id` (str): 蓝图 ID

**返回值**：`None`（HTTP 204 No Content）

> **Admin 特权**：管理员可以删除任何人的蓝图，非管理员只能删除自己的蓝图。

### 服务调用

#### call_service - 调用弹性服务

```python
import magnus

response = magnus.call_service("llm-inference", payload={"prompt": "Hello!"})

response = magnus.call_service(
    "image-generation",
    payload={"prompt": "sunset", "width": 1024},
    timeout=120,
)
```

**参数**：
- `service_id` (str): 服务 ID
- `payload` (dict): 请求负载
- `timeout` (int, 可选): 超时（秒），默认 60

**返回值**：dict

**异常**：
- `TimeoutError`: 超时（服务端请求不会因此中断）
- `MagnusError`: 服务不可用

#### list_services - 列出服务

```python
import magnus

services = magnus.list_services(limit=20)
services = magnus.list_services(active_only=True)

for svc in services["items"]:
    print(f"{svc['id']} | {svc['name']} | Active: {svc['is_active']}")
```

**参数**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按名称或 ID 搜索
- `active_only` (bool, 可选): 仅返回活跃服务，默认 False

**返回值**：`{"total": int, "items": list}`

### 集群与资源

#### get_cluster_stats - 获取集群状态

```python
import magnus

stats = magnus.get_cluster_stats()

resources = stats["resources"]
print(f"GPU: {resources['gpu_model']} x {resources['total']}")
print(f"空闲: {resources['free']}")
print(f"运行中: {stats['total_running']}, 排队: {stats['total_pending']}")
```

**返回值**：dict，包含 `resources`, `running_jobs`, `pending_jobs` 等字段

### 文件传输

蓝图中使用 `FileSecret` 类型声明文件参数，SDK 自动处理上传；蓝图代码中使用 `download_file` 接收。

#### FileSecret 参数 - 自动上传

当蓝图参数类型为 `FileSecret` 时，`launch_blueprint` / `run_blueprint` 会自动检测本地文件路径并上传，将路径替换为 file secret。`FileSecret` 支持 `Optional` 和 `List` 包装。

```python
import magnus

# 单文件
job_id = magnus.launch_blueprint(
    "my-blueprint",
    args={"input_data": "/home/user/data.csv"},
)

# 多文件 (List[FileSecret])
job_id = magnus.launch_blueprint(
    "batch-process",
    args={"files": ["/home/user/a.csv", "/home/user/b.csv"]},
)

# 已有 secret 和本地路径混用
job_id = magnus.launch_blueprint(
    "batch-process",
    args={"files": ["magnus-secret:abc-123", "/home/user/new.csv"]},
)
```

#### download_file - 接收文件

在任务执行环境中接收文件。

```python
from magnus import download_file

download_file(file_secret, "/workspace/data/input.csv")
download_file(file_secret, "data/input.csv")  # 相对路径
```

**参数**：
- `file_secret` (str): file secret，带或不带 `magnus-secret:` 前缀
- `target_path` (str): 目标路径
- `timeout` (float, 可选): 超时（秒），默认无限等待
- `overwrite` (bool, 可选): 覆盖已存在文件，默认 True

**返回值**：`Path`

**异常**：
- `MagnusError`: 文件不存在、已过期、传输失败

`download_file_async` 为异步版本，参数相同。

### 文件代管

#### custody_file - 代管文件

将本地文件/文件夹上传到后端代管，返回 file_secret。

```python
import magnus
import os

secret = magnus.custody_file("/path/to/results.csv")

secret = magnus.custody_file(
    "/path/to/output_dir",
    expire_minutes=120,
)

# 配合 MAGNUS_ACTION 实现一键传输
secret = magnus.custody_file("/workspace/processed.pdf")
with open(os.environ["MAGNUS_ACTION"], "w") as f:
    f.write(f"magnus receive {secret} -o processed.pdf\n")
```

**参数**：
- `path` (str): 本地文件或文件夹路径
- `expire_minutes` (int, 可选): 过期时间（分钟），默认 60
- `max_downloads` (int, 可选): 最大下载次数，默认无限制
- `timeout` (float, 可选): HTTP 超时（秒），默认 300

**返回值**：`str`，`magnus-secret:xxxx` 格式

`custody_file_async` 为异步版本，参数相同。

### 技能 (Skill)

Skill 是 Magnus 的可复用代码包，包含多个文件（必须有 `SKILL.md` 描述文件）。

#### list_skills - 列出技能

```python
import magnus

skills = magnus.list_skills(limit=20, search="pytorch")

for skill in skills["items"]:
    print(f"{skill['id']} | {skill['title']}")
```

**参数**：
- `limit` (int, 可选): 返回数量，默认 20
- `skip` (int, 可选): 跳过数量，默认 0
- `search` (str, 可选): 按标题或 ID 搜索

**返回值**：`{"total": int, "items": list}`

#### get_skill - 获取技能详情

```python
import magnus

skill = magnus.get_skill("my-skill")
for f in skill["files"]:
    print(f"{f['path']}: {len(f['content'])} chars")
```

**参数**：
- `skill_id` (str): 技能 ID

**返回值**：dict，包含 `id`, `title`, `description`, `files`, `user_id`, `user`, `created_at`, `updated_at` 等字段

#### save_skill - 创建或更新技能

后端为 upsert 语义。

```python
import magnus

skill = magnus.save_skill(
    skill_id="my-skill",
    title="My Skill",
    description="A useful skill",
    files=[
        {"path": "SKILL.md", "content": "# My Skill\n\nDescription here."},
        {"path": "main.py", "content": "print('hello')"},
    ],
)
```

**参数**：
- `skill_id` (str): 技能 ID
- `title` (str): 标题
- `description` (str): 描述
- `files` (list): 文件列表，每项包含 `path` 和 `content`

**返回值**：dict，保存后的技能信息

#### delete_skill - 删除技能

```python
import magnus

magnus.delete_skill("my-old-skill")
```

**参数**：
- `skill_id` (str): 技能 ID

**返回值**：`None`（HTTP 204 No Content）

### 镜像 (Image)

管理集群上的容器镜像缓存。拉取和刷新操作是异步的：API 立即返回 202，后台执行实际拉取。

#### list_images - 列出缓存镜像

```python
import magnus

images = magnus.list_images(search="pytorch")

for img in images["items"]:
    print(f"[{img['status']}] {img['uri']} ({img['size_bytes']} bytes)")
```

**参数**：
- `search` (str, 可选): 按 URI 搜索

**返回值**：`{"total": int, "items": list}`

镜像状态：`cached`（已缓存）、`pulling`（正在拉取）、`refreshing`（正在刷新）、`missing`（DB 有记录但文件丢失）、`unregistered`（文件存在但无 DB 记录）

#### pull_image - 拉取新镜像

异步操作，API 返回 202 后后台执行拉取。用 `list_images` 检查进度。

```python
import magnus

result = magnus.pull_image("docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
print(f"Image ID: {result['id']}, Status: {result['status']}")
```

**参数**：
- `uri` (str): 容器镜像 URI

**返回值**：dict，包含 `id`, `uri`, `status` 等字段

#### refresh_image - 刷新已缓存镜像

异步安全刷新：拉新镜像到临时文件，完成后原子替换，旧镜像在刷新期间保持可用。

```python
import magnus

result = magnus.refresh_image(3)
```

**参数**：
- `image_id` (int): 镜像 ID

**返回值**：dict，包含 `id`, `uri`, `status` 等字段

#### remove_image - 删除缓存镜像

```python
import magnus

magnus.remove_image(3)
```

**参数**：
- `image_id` (int): 镜像 ID

**返回值**：`None`（HTTP 204 No Content）

### 异步 API

所有同步 API 均有 `_async` 后缀的异步版本：

```python
import magnus
import asyncio

async def main():
    job_id = await magnus.submit_job_async(
        task_name="async-test",
        entry_command="echo done",
        repo_name="my-project",
    )

    result = await magnus.execute_job_async(
        task_name="async-test",
        entry_command="echo done",
        repo_name="my-project",
        timeout=300,
    )

    job_id = await magnus.launch_blueprint_async(
        "my-blueprint", args={"param": "value"},
    )

    result = await magnus.run_blueprint_async(
        "my-blueprint", args={"param": "value"}, timeout=300,
    )

    jobs = await magnus.list_jobs_async(limit=20)
    job = await magnus.get_job_async("abc123")
    await magnus.terminate_job_async("abc123")

asyncio.run(main())
```

并发提交多个任务：

```python
import magnus
import asyncio

async def run_experiments():
    tasks = [
        magnus.launch_blueprint_async("experiment", args={"seed": i})
        for i in range(10)
    ]
    job_ids = await asyncio.gather(*tasks)

asyncio.run(run_experiments())
```

所有同步 API 均有对应的 `_async` 异步版本。

### API 参考

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `submit_job(task_name, entry_command, repo_name, ...)` | 提交任务，立即返回 | Job ID |
| `execute_job(task_name, entry_command, repo_name, ...)` | 提交并等待完成 | `Optional[str]` |
| `list_jobs(limit, search)` | 列出任务 | `{total, items}` |
| `get_job(job_id)` | 获取任务详情 | 任务信息 |
| `get_job_result(job_id)` | 获取任务结果 | `Optional[str]` |
| `get_job_action(job_id)` | 获取任务动作 | `Optional[str]` |
| `get_job_logs(job_id, page)` | 获取任务日志 | `{logs, page, total_pages}` |
| `terminate_job(job_id)` | 终止任务 | 状态信息 |
| `launch_blueprint(id, args, ...)` | 提交蓝图任务，立即返回 | Job ID |
| `run_blueprint(id, args, timeout, ...)` | 提交蓝图并等待完成 | `Optional[str]` |
| `list_blueprints(limit, search)` | 列出蓝图 | `{total, items}` |
| `get_blueprint(id)` | 获取蓝图详情（含代码） | 蓝图信息 |
| `get_blueprint_schema(id)` | 获取蓝图参数 Schema | 参数列表 |
| `save_blueprint(id, title, description, code)` | 创建或更新蓝图 (upsert) | 蓝图信息 |
| `delete_blueprint(id)` | 删除蓝图 | `None` |
| `call_service(id, payload, timeout)` | 调用弹性服务 | 服务响应 |
| `list_services(limit, search, active_only)` | 列出服务 | `{total, items}` |
| `get_cluster_stats()` | 获取集群状态 | 集群信息 |
| `download_file(secret, target_path)` | 接收文件 | Path |
| `custody_file(path, expire_minutes, max_downloads)` | 代管文件，返回 secret | file_secret |
| `list_skills(limit, search)` | 列出技能 | `{total, items}` |
| `get_skill(id)` | 获取技能详情（含文件） | 技能信息 |
| `save_skill(id, title, description, files)` | 创建或更新技能 (upsert) | 技能信息 |
| `delete_skill(id)` | 删除技能 | `None` |
| `list_images(search)` | 列出缓存镜像 | `{total, items}` |
| `pull_image(uri)` | 拉取新镜像（异步 202） | 镜像信息 |
| `refresh_image(image_id)` | 刷新已缓存镜像（异步 202） | 镜像信息 |
| `remove_image(image_id)` | 删除缓存镜像 | `None` |
| `configure(token, address)` | 配置 SDK | None |

---

## 命令行工具 (CLI)

### 命令结构

CLI 有两层命令结构：

**顶层快捷命令** — 高频操作的简写，永久保留：

```bash
magnus submit ...        # 提交任务 (Fire & Forget)
magnus execute ...       # 提交任务并等待完成
magnus jobs              # 列出任务
magnus status <ref>      # 查看任务详情
magnus logs <ref>        # 查看任务日志
magnus kill <ref>        # 终止任务
magnus launch <id>       # 提交蓝图 (Fire & Forget)
magnus run <id>          # 提交蓝图并等待完成
magnus list              # 列出蓝图
```

**宾动结构子命令** — 完整操作，类似 `git remote add`：

```bash
magnus job submit ...             # = magnus submit
magnus job execute ...            # = magnus execute
magnus job list                   # = magnus jobs
magnus job status <ref>           # = magnus status
magnus job logs <ref>             # = magnus logs
magnus job result <ref>           # 查看任务结果
magnus job action <ref>           # 查看任务动作
magnus job kill <ref>             # = magnus kill

magnus blueprint list             # = magnus list
magnus blueprint get <id>         # 查看蓝图详情（含代码）
magnus blueprint get <id> -c bp.py  # 导出代码到文件
magnus blueprint schema <id>      # 查看参数 Schema
magnus blueprint save <id> ...    # 创建/更新蓝图
magnus blueprint delete <id>      # 删除蓝图
magnus blueprint launch <id>      # = magnus launch
magnus blueprint run <id>         # = magnus run
```

顶层快捷命令与宾动子命令完全等价。`job result/action`、`blueprint get/save/delete/schema` 只在宾动结构下提供。

不变的顶层命令：`config`, `login`, `logout`, `call`, `cluster`, `services`, `skills`, `refresh`, `send`, `receive`, `custody`, `connect`, `disconnect`

### 全局选项

```bash
magnus --help    # 或 magnus -h
magnus --version # 或 magnus -v
```

### magnus config

查看当前配置（地址和令牌）。Token 自动脱敏，仅显示首尾各 4 个字符。

```bash
magnus config
```

```
  MAGNUS_ADDRESS  http://162.105.151.196:8017
  MAGNUS_TOKEN    sk-a****************ZaB
```

### magnus login

配置 `MAGNUS_ADDRESS` 和 `MAGNUS_TOKEN`，验证连通性后保存到 `~/.magnus/config.json`。

```bash
magnus login                                              # 交互式
magnus login prod                                         # 切换到已有 site
magnus login prod -a http://host:8017 -t sk-xxx           # 非交互式（适合脚本/agent）
magnus login default                                      # 切换到硬编码默认 site
```

**选项**（非交互式模式需 site + 两个选项齐全）：
- `-a, --address`: 服务器地址
- `-t, --token`: Trust Token

交互式和非交互式都会验证连通性（`GET /api/auth/my-token`），验证失败会警告但不阻止保存。保存后所有终端立即生效。

### magnus job

任务操作子命令组。

```bash
magnus job --help
```

#### magnus job submit

提交任务 (Fire & Forget)。不需要防波堤 `--`，参数按名称自动路由：`task-name`、`repo-name`、`gpu-type` 等归 Job 层，`timeout`、`verbose` 等归 CLI 层。

```bash
magnus job submit --task-name "Train" --repo-name my_repo --branch main \
  --commit-sha HEAD --entry-command "python train.py" --gpu-type A100 --gpu-count 4
```

#### magnus job execute

提交任务并等待完成。轮询期间遇到瞬时网络错误或 5xx 自动重试（指数退避，最多 30 次连续失败）。参数路由规则同 `submit`。

```bash
magnus job execute --task-name "Quick Test" --repo-name my_repo --branch main \
  --commit-sha HEAD --entry-command "echo hello"
```

#### magnus job list

列出任务。

```bash
magnus job list                          # 最近 10 个
magnus job list -l 20                    # 最近 20 个
magnus job list -n "quadre"              # 按名称搜索
magnus job list --format yaml            # YAML 输出
magnus job list | head -20               # 管道时自动切换为 YAML
```

**选项**：
- `-l, --limit`: 数量，默认 10
- `-n, --name, -s, --search`: 搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

支持负数索引：列表中 `-1` 为最新任务，`-2` 为次新，以此类推。

#### magnus job status

查看任务详情。支持负数索引。

```bash
magnus job status abc123         # 按 Job ID
magnus job status -1             # 最新任务
magnus job status -2             # 次新任务
```

#### magnus job logs

查看任务日志。

```bash
magnus job logs -1               # 最新任务的最新页
magnus job logs -1 --page 0      # 第一页
magnus job logs abc123
```

**选项**：
- `-p, --page`: 页码，-1 表示最新页，默认 -1

#### magnus job result

查看任务结果（`MAGNUS_RESULT` 的内容）。JSON 自动格式化。

```bash
magnus job result -1
magnus job result abc123
```

#### magnus job action

查看任务动作脚本（`MAGNUS_ACTION` 的内容）。

```bash
magnus job action -1             # 查看
magnus job action -1 -e          # 查看并执行
magnus job action abc123
```

**选项**：
- `-e, --execute`: 执行 action

#### magnus job kill

终止任务。

```bash
magnus job kill abc123           # 需确认
magnus job kill -1               # 最新任务
magnus job kill -1 -f            # 跳过确认
```

**选项**：
- `-f, --force`: 跳过确认

### magnus blueprint

蓝图操作子命令组。

```bash
magnus blueprint --help
```

#### magnus blueprint list

列出蓝图。

```bash
magnus blueprint list                    # 列出
magnus blueprint list -l 20             # 20 个
magnus blueprint list -s "sim"           # 搜索
magnus blueprint list -f yaml            # YAML 输出
```

**选项**：
- `-l, --limit`: 数量，默认 10
- `-s, --search`: 搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

#### magnus blueprint get

查看蓝图详情，包含完整代码。

```bash
magnus blueprint get <blueprint-id>
magnus blueprint get my-blueprint -f yaml
magnus blueprint get my-blueprint -c bp.py   # 导出代码到文件
```

**选项**：
- `-f, --format`: 输出格式 (yaml/json)，默认人类可读
- `-c, --code-file`: 导出代码到指定 .py 文件

`--code-file` 与 `save --code-file` 对称，形成完整的 inspect → edit → save 闭环：

```bash
magnus blueprint get my-bp -c bp.py   # 导出
$EDITOR bp.py                          # 编辑
magnus blueprint save my-bp -t "My BP" -c bp.py  # 上传
```

#### magnus blueprint schema

查看蓝图参数 Schema。默认 JSON 输出，包含每个参数的类型、约束、合法值列表。

```bash
magnus blueprint schema <blueprint-id>
magnus blueprint schema my-blueprint -f yaml
```

**选项**：
- `-f, --format`: 输出格式 (yaml/json)，默认 json

#### magnus blueprint save

创建或更新蓝图（upsert 语义）。

```bash
magnus blueprint save <id> --title "标题" --code-file blueprint.py
magnus blueprint save my-bp -t "My BP" -d "描述" -c ./src/bp.py
```

**参数**：
- `<id>`: 蓝图 ID

**选项**：
- `-t, --title`: 标题 (必填)
- `-d, --description, --desc`: 描述，默认空
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

提交蓝图任务，立即返回 Job ID。

```bash
magnus blueprint launch <blueprint-id> [OPTIONS] [-- ARGS...]

magnus blueprint launch quadre-simulation
magnus blueprint launch quadre-simulation --Te 2.0 --B 1.5
magnus blueprint launch my-blueprint -- --param value --flag
magnus blueprint launch my-blueprint --expire-minutes 120 -- --data /path/to/file

# List[FileSecret]：重复 flag 收集为列表
magnus blueprint launch batch-process -- --files a.csv --files b.csv
```

防波堤 `--` 将参数分为两侧：左侧是 CLI 控制参数（类型强转），右侧是蓝图业务参数（**保持为原始字符串**，类型转换由后端负责）。没有 `--` 时，所有参数归蓝图，CLI 控制参数使用默认值。

**选项**（`--` 左侧）：
- `--expire-minutes`: FileSecret 过期时间（分钟），默认 60
- `--max-downloads`: FileSecret 最大下载次数，默认 1
- `--preference`: 合并偏好参数，默认 false
- `--timeout`: HTTP 超时（秒），默认 10
- `--verbose`: 打印参数分区详情，默认 false

#### magnus blueprint run

提交蓝图任务并等待完成。轮询期间遇到瞬时网络错误或 5xx 自动重试（指数退避，最多 30 次连续失败）。任务写入 `MAGNUS_ACTION` 时默认在客户端执行。

```bash
magnus blueprint run <blueprint-id> [OPTIONS] [-- ARGS...]

magnus blueprint run my-blueprint --timeout 300 -- --param value
magnus blueprint run long-task --timeout 3600 --poll-interval 30

# 蓝图内写 MAGNUS_ACTION 实现自动下载
magnus blueprint run scan-pdf-to-vector --file original.pdf --output processed.pdf

# 不执行 action
magnus blueprint run my-blueprint --execute-action false -- --param value
```

防波堤规则同 `launch`。

**选项**（`--` 左侧）：
- `--timeout`: 超时（秒），默认无限等待
- `--poll-interval`: 轮询间隔（秒），默认 2
- `--execute-action`: 执行 action，默认 true
- `--expire-minutes`: FileSecret 过期时间（分钟），默认 60
- `--max-downloads`: FileSecret 最大下载次数，默认 1
- `--preference`: 合并偏好参数，默认 false
- `--verbose`: 打印参数分区详情，默认 false

### magnus call

调用弹性服务。支持可选防波堤 `--`：有 `--` 时左侧归 CLI、右侧归 payload；没有 `--` 时按保留关键字自动路由——`timeout`、`verbose`、`execute-action` 归 CLI，其余归 payload。

```bash
magnus call <service-id> [OPTIONS] [ARGS...]

# 直接传参（timeout 自动识别为 CLI 参数）
magnus call llm-inference --prompt "Hello!" --max_tokens 100 --timeout 120

# 显式防波堤，效果相同
magnus call slow-service --timeout 120 -- --param value

# 从 JSON 文件
magnus call my-service @payload.json

# 从 stdin
echo '{"x": 1, "y": 2}' | magnus call my-service -
cat input.json | magnus call my-service -
```

**参数格式**：
- `--key value`: 直接传参
- `@file.json`: JSON 文件
- `-`: stdin

**选项**（CLI 保留关键字）：
- `--timeout, -t`: 超时（秒），默认 60
- `--verbose`: 打印参数分区详情
- `--execute-action`: 执行 action

### magnus cluster

查看集群资源状态。

```bash
magnus cluster
magnus cluster --format yaml
```

**选项**：
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

### magnus services

列出托管服务。

```bash
magnus services              # 全部
magnus services -a           # 仅活跃
magnus services -s "llm"     # 搜索
magnus services -f yaml
```

**选项**：
- `-l, --limit`: 数量，默认 10
- `-s, --search`: 搜索
- `-a, --active`: 仅活跃
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

### magnus skill

技能操作子命令组。

#### magnus skill list

列出技能。

```bash
magnus skill list                    # 列出
magnus skill list -l 20              # 20 个
magnus skill list -s "pytorch"       # 搜索
magnus skill list -f yaml            # YAML 输出
```

**选项**：
- `-l, --limit`: 数量，默认 10
- `-s, --search`: 搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

#### magnus skill get

查看技能详情，包含文件列表。

```bash
magnus skill get <skill-id>
magnus skill get my-skill -f yaml
```

**选项**：
- `-f, --format`: 输出格式 (yaml/json)，默认人类可读
- `-e, --export DIR`: 导出所有文件到本地目录，方便编辑后用 `skill save` 上传

#### magnus skill save

创建或更新技能（upsert）。从本地目录读取文件并上传。

```bash
magnus skill save my-skill --title "My Skill" --description "..." ./my_skill/
magnus skill save my-skill -t "Updated" -d "New desc" ./my_skill/
```

**参数**：
- `skill_id`: 技能 ID
- `source`: 源目录路径

**选项**：
- `-t, --title`: 技能标题（首次创建时必填）
- `-d, --description`: 技能描述（首次创建时必填）

#### magnus skill delete

删除技能。需确认，或用 `-f` 跳过。

```bash
magnus skill delete my-skill
magnus skill delete my-skill -f
```

### magnus image

镜像缓存操作子命令组。当你推送了新版本到已有 tag，使用 `refresh` 更新本地缓存；使用 `pull` 添加新镜像。

#### magnus image list

列出缓存镜像。合并 DB 记录与 filesystem 扫描，标注镜像状态。

```bash
magnus image list
magnus image list -s "pytorch"
magnus image list -f yaml
```

**选项**：
- `-s, --search`: 按 URI 搜索
- `-f, --format`: 输出格式 (table/yaml/json)，默认 table

#### magnus image pull

拉取新镜像。异步操作：API 返回 202 后后台执行拉取，用 `magnus image list` 检查进度。所有登录用户可拉取新镜像；已存在的镜像仅 owner 或管理员可重新拉取。

```bash
magnus image pull docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
magnus image pull docker://nvcr.io/nvidia/pytorch:24.01-py3
```

#### magnus image refresh

重新拉取已缓存镜像。安全刷新：拉新镜像到 `.tmp` 文件，完成后原子替换，旧镜像在刷新期间保持可用。

```bash
magnus image refresh 3
```

#### magnus image remove

删除缓存镜像（DB 记录 + SIF 文件）。

```bash
magnus image remove 3           # 需确认
magnus image remove 3 -f        # 跳过确认
```

**选项**：
- `-f, --force`: 跳过确认

### magnus refresh

顶层快捷方式，等价于 `magnus image refresh`。

```bash
magnus refresh 3
```

### magnus skills

顶层快捷方式，等价于 `magnus skill list`。

```bash
magnus skills
```

### magnus send

上传文件或文件夹，返回 file secret。

```bash
magnus send data.csv
magnus send ./my_folder
magnus send data.csv --max-downloads 3
```

**选项**：
- `-t, --expire-minutes`: 过期时间（分钟），默认 60
- `-d, --max-downloads`: 最大下载次数，默认 1

### magnus receive

下载文件或文件夹。

```bash
magnus receive 7919-calm-boat-fire
magnus receive 7919-calm-boat-fire -o my_data.csv
magnus receive 7919-calm-boat-fire --output ./downloads/result.tar.gz
```

**选项**：
- `-o, --output`: 目标路径（可重命名），不指定则落到当前目录

### magnus custody

将文件上传到后端代管，返回 file secret。

```bash
magnus custody results.csv
magnus custody ./output_dir --expire-minutes 120
```

**选项**：
- `-t, --expire-minutes`: 过期时间（分钟），默认 60
- `-d, --max-downloads`: 最大下载次数，默认无限制

### magnus connect

连接到运行中的 Magnus Debug 会话。

```bash
magnus connect           # 自动检测最新 debug 任务
magnus connect 12345     # 指定 SLURM Job ID
```

- 已在 Magnus 会话中（`SLURM_JOB_ID` 已设置）时提示退出
- 未指定 Job ID 时自动检测当前用户的 "Magnus Debug" 任务
- 多个任务时连接最新的，提示其他可用任务

### magnus disconnect

断开当前 Magnus Debug 会话。

```bash
magnus disconnect
```

仅在 Magnus 会话内有效，发送 `SIGHUP` 到父进程终止 srun 会话。

---

## 附录

### 输出格式

列表类命令支持三种输出格式：

| 格式 | 适用场景 |
|------|----------|
| `table` | 交互式终端（默认） |
| `yaml` | 脚本处理、管道 |
| `json` | 程序解析 |

`blueprint schema` 默认 JSON（结构化数据，适合程序读取）。其余列表命令在终端默认 table，管道/重定向自动切换为 YAML。

```bash
magnus job list                          # table
magnus job list | grep Running           # 自动 YAML
magnus job list --format json | jq '.'   # 强制 JSON
magnus blueprint schema my-bp            # JSON
magnus blueprint schema my-bp -f yaml    # 强制 YAML
```

### 错误代码

| 代码 | 说明 |
|------|------|
| `AUTH_REQUIRED` | 需要认证，检查 MAGNUS_TOKEN |
| `TOKEN_EXPIRED` | Token 已过期 |
| `BLUEPRINT_NOT_FOUND` | 蓝图不存在 |
| `SERVICE_NOT_FOUND` | 服务不存在 |
| `JOB_NOT_FOUND` | 任务不存在 |
| `VALIDATION_ERROR` | 参数校验失败 |
| `SERVICE_UNAVAILABLE` | 服务不可用 |
| `TIMEOUT` | 请求超时 |

### 常见问题

**Q: 如何获取 MAGNUS_TOKEN？**

Web 界面登录后，点击右上角用户头像，可见 Trust Token（`sk-` 开头）。Trust Token 用于 SDK/CLI 鉴权，与 Web 会话使用的 JWT 是独立的。

**Q: 环境变量和 configure() 哪个优先？**

`magnus.configure()` 优先级最高，会覆盖环境变量和配置文件。

**Q: 负数索引支持哪些命令？**

`magnus job status`, `logs`, `result`, `action`, `kill` 均支持。`-1` 为最新任务，`-2` 为次新。

**Q: call_service 超时后服务还在运行吗？**

是的。`timeout` 只控制客户端等待时间，服务端请求继续执行。

**Q: connect/disconnect 为什么没有 Python SDK 版本？**

`connect` 和 `disconnect` 通过 `srun` 建立交互式 shell 连接，是终端级别的操作，无法用函数调用替代。

**Q: 什么是偏好 (Preference)？**

用户上次运行蓝图时使用的参数缓存。`use_preference=True` 时合并缓存参数（显式传入优先）。`save_preference=True` 时成功运行后保存当前参数。SDK/CLI 默认不合并（`use_preference=False`），Web UI 默认合并。`FileSecret` 类型也会缓存，但 secret 有 TTL。

**Q: 阻塞命令（`run` / `execute`）断网了怎么办？**

轮询期间遇到瞬时网络错误（DNS、连接重置等）或 5xx 会自动指数退避重试（2s → 4s → 8s → ... → 30s 上限），最多容忍 30 次连续失败。超过后报错并提示用 `magnus job status <id>` 检查。任务本身不受客户端断线影响。

**Q: --format yaml 和管道自动切换的区别？**

管道自动切换：输出不是终端时自动使用 YAML。`--format yaml`：强制 YAML，即使在终端中。
