> **Language / 语言**: [English](blueprint-crafting.md) · **简体中文**

# Blueprint Crafting Guide

本文档是 Magnus 蓝图系统的权威参考，面向人类开发者和 AI Agent。
目标场景：在一个新项目根目录下，根据本文档将该项目接入 Magnus 蓝图。

> **源码真相**：本文档所有细节均来自代码，而非二手描述。
> 核心文件：`back_end/server/_blueprint_manager/`、`back_end/server/schemas/`。

---

## 1. 蓝图是什么

一个蓝图就是一个 Python 函数 `blueprint`，它：
- 接收用户参数（自动生成前端表单）
- 在函数体内调用 `submit_job()` 提交计算任务

```python
def blueprint(
    user_name: UserName,
    gpu_count: GpuCount = 1,
):
    submit_job(
        task_name="My Task",
        entry_command=f"python train.py --user {user_name}",
        repo_name="my-project",
        gpu_type="rtx5090",
        gpu_count=gpu_count,
    )
```

就这么多。系统会自动：
1. 解析函数签名 → 生成前端表单
2. 用户填写参数 → Pydantic 类型转换 → 调用函数
3. 劫持 `submit_job()` 调用 → 捕获参数 → 提交到调度器

**蓝图代码是"真实可运行的代码"** — 本地安装 Magnus SDK 后，蓝图代码可以直接执行。

---

## 2. 隐式导入

蓝图代码不需要 import 语句。也可显式导入 `typing`、`__future__` 和 `magnus` 的蓝图符号，包括别名；其它 import 会被拒绝。以下符号自动可用：

```python
# 自动注入到蓝图执行环境
submit_job, JobType, FileSecret              # from magnus
Annotated, Literal, Optional, List, Dict, Any  # from typing
```

本地文件和 Web 编辑器可以保留同一组导入：

```python
from magnus import submit_job, JobType, FileSecret
from typing import Annotated, Literal, Optional, List
```

---

## 3. submit_job 参数参考

`blueprint` 函数必须调用 `submit_job()`。参数如下：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_name` | `str` | **是** | — | 任务显示名称 |
| `entry_command` | `str` | **是** | — | 容器内执行的 shell 命令（支持多行） |
| `repo_name` | `str` | **是** | — | GitHub 仓库名 |
| `branch` | `Optional[str]` | 否 | `None` | Git 分支；`None` 时服务端自动检测默认分支 |
| `commit_sha` | `Optional[str]` | 否 | `None` | Git commit hash；`None` 等同于 `"HEAD"` |
| `gpu_type` | `str` | 否 | `"cpu"` | GPU 型号标识，如 `"rtx5090"`；纯 CPU 任务用 `"cpu"` |
| `description` | `Optional[str]` | 否 | `None` | Markdown 格式的任务描述 |
| `namespace` | `str` | 否 | `"Rise-AGI"` | GitHub 组织/用户名 |
| `gpu_count` | `int` | 否 | `0` | GPU 数量 |
| `job_type` | `JobType` | 否 | `JobType.A2` | 优先级（见下表） |
| `container_image` | `Optional[str]` | 否 | `None` | Docker 镜像 URI；`None` 使用集群默认镜像 |
| `cpu_count` | `Optional[int]` | 否 | `None` | CPU 核心数；`None` 使用集群默认 |
| `memory_demand` | `Optional[str]` | 否 | `None` | 内存需求，如 `"32G"`、`"1600M"` |
| `runner` | `Optional[str]` | 否 | `None` | 执行用户；`None` 使用集群默认 |
| `system_entry_command` | `Optional[str]` | 否 | `None` | 容器启动前的宿主机命令 |

**`description`、`entry_command`、`system_entry_command` 会自动 strip 首尾空白。**

### JobType 优先级

| 值 | 含义 | 可被抢占 |
|----|------|----------|
| `JobType.A1` | 最高优先级 | 否 |
| `JobType.A2` | 高优先级 | 否 |
| `JobType.B1` | 低优先级 | 是（被 A 类抢占） |
| `JobType.B2` | 最低优先级 | 是（被 A 类抢占） |

可以用 `getattr(JobType, "A1")` 从字符串构造。

### commit_sha 智能解析

`commit_sha` 除了直接传 40 位 SHA 或 `None`（HEAD）外，还支持 `msg:正则` 模式——搜索最近 200 条 commit message，匹配第一个符合的：

```python
submit_job(
    ...,
    commit_sha="msg:v2\\.1",  # 匹配含 "v2.1" 的最近提交
)
```

---

## 4. 参数类型系统

### 4.1 基础类型

| Python 类型 | 表单类型 | 前端控件 |
|-------------|----------|----------|
| `str` | `"text"` | 单行/多行输入框 |
| `int` | `"number"` | 数字步进器 |
| `float` | `"float"` | 数字输入框 |
| `bool` | `"boolean"` | 开关 |
| `Literal["a", "b", ...]` | `"select"` | 下拉选择器 |
| `FileSecret` | `"file_secret"` | 文件凭证输入框 |

### 4.2 类型包装器

基础类型可以用以下包装器组合：

| 写法 | 含义 | 前端行为 |
|------|------|----------|
| `T` | 必填 | 直接显示 |
| `Optional[T]` | 可选（可为 None） | 带启用/禁用开关 |
| `List[T]` | 必填列表 | 动态添加/删除项 |
| `Optional[List[T]]` | 可选列表 | 带开关的动态列表 |

**不支持**：嵌套泛型 `List[List[T]]`、`Union` 类型（`Optional` 除外）、自定义类、`Dict` 参数。

### 4.3 Annotated 元数据

用 `Annotated[Type, {...}]` 给参数附加 UI 元数据。所有 key 均可选。

#### 通用 key（所有类型）

| Key | 类型 | 说明 |
|-----|------|------|
| `"label"` | `str` | 字段显示名称（默认：参数名 `_` → 空格，Title Case） |
| `"description"` | `str` | 字段下方的帮助文字 |
| `"scope"` | `str` | 参数分组名（相同 scope 的参数归为一组显示） |

#### str 专用

| Key | 类型 | 默认 | 说明 |
|-----|------|------|------|
| `"allow_empty"` | `bool` | `True` | `False` 时字段不能为空 |
| `"placeholder"` | `str` | — | 输入框占位提示 |
| `"multi_line"` | `bool` | `False` | 启用多行文本框 |
| `"min_lines"` | `int` | — | 多行文本框最小行数 |
| `"color"` | `str` | — | 文字 CSS 颜色 |
| `"border_color"` | `str` | — | 边框 CSS 颜色 |

#### int / float 专用

| Key | 类型 | 说明 |
|-----|------|------|
| `"min"` | `float` | 最小值（含） |
| `"max"` | `float` | 最大值（含） |
| `"placeholder"` | `str` | 占位提示（仅 float） |

#### Literal 专用

| Key | 类型 | 说明 |
|-----|------|------|
| `"options"` | `Dict[value, info]` | 为每个选项自定义显示。`info` 可以是 `str`（label）或 `{"label": str, "description": str}` |

#### FileSecret 专用

| Key | 类型 | 说明 |
|-----|------|------|
| `"placeholder"` | `str` | 占位提示 |

FileSecret 的 `allow_empty` 始终为 `False`（必填）。

---

## 5. 沙箱环境

蓝图代码在独立的操作系统沙箱内执行，保存代码、读取表单 schema 时同样如此。Python builtins/import 限制定义语言接口，不承担安全边界。控制面重新校验返回的 schema 和 Job 字段；拼错的 `submit_job` 字段会被拒绝，不再静默采用默认值。

只支持带命名参数的同步函数；位置专用参数、`*args`、`**kwargs`、生成器和异步函数在保存时即被拒绝。`min`、`max`、`allow_empty` 在服务端执行校验，也覆盖默认值和列表项。可选文件列表项保留 `None`；提供的文件在函数内仍为 `FileSecret`，可读取 `.token`。

### 运维配置

Linux 使用 [Bubblewrap](https://github.com/containers/bubblewrap)、非特权用户命名空间和 `libseccomp2`。只读挂载解释器、运行库和求值器代码，不挂载服务环境、账号 home、数据库或工作区；禁用网络、IPC 和进程创建。系统调用允许列表防止代码修改工作进程的期限或限额。

Windows/macOS，或设置 `server.blueprint.backend: docker` 的 Linux，使用 Linux Docker 容器。启用蓝图前，从同一 checkout 准备运行时镜像：

```bash
docker build -t magnus-blueprint-runtime:1 -f back_end/containers/blueprint-runtime.Dockerfile back_end/containers
```

镜像包含 Python 运行时和 Pydantic；求值器源码从当前 checkout 只读挂载。容器无网络、无 capabilities、根目录只读，以非 root 用户运行，运行时源码须对该用户可读。可通过 `docker_image` 指定兼容的预制运行时；求值时不会隐式拉取镜像。

默认限额为求值 8 秒、512 MiB 虚拟地址空间、请求和输出各 4 MiB、最多四个并行工作进程。Docker 另有内存、CPU 和进程数限制。这些约束用于蓝图求值，不限制生成的计算 Job。可在 `server.blueprint` 下配置，见[配置示例](../../configs/magnus_config.yaml.example)。并发满或隔离不可用返回 HTTP 503；代码、参数或资源限额违规返回 HTTP 400。没有同进程或无隔离的降级路径，工作进程回收后才释放槽位。

### 可用的 builtins

**常量**：`True`, `False`, `None`

**类型**：`str`, `int`, `float`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`, `type`, `object`

**函数**：`len`, `range`, `enumerate`, `zip`, `map`, `filter`, `sorted`, `reversed`, `sum`, `min`, `max`, `abs`, `round`, `pow`, `divmod`, `any`, `all`, `isinstance`, `issubclass`, `hasattr`, `getattr`, `setattr`, `callable`, `repr`, `hash`, `id`, `print`

**异常**：`Exception`, `ValueError`, `TypeError`, `KeyError`, `IndexError`, `AttributeError`, `RuntimeError`

### 语言和隔离边界

- 应用依赖和副作用应放进 Job 的 `entry_command`，不放进蓝图求值过程。
- 语言接口不暴露 `open`、`eval`、`exec`、`compile` 或任意 import。
- 即使代码通过反射取得额外 Python 对象，操作系统隔离仍禁止访问宿主数据、网络、可写文件和子进程。

### 运行时类型转换

参数从前端/CLI 传入时可能是字符串。系统通过 Pydantic 动态模型自动转换：

```
"10"    → 10      (str → int)
"3.14"  → 3.14    (str → float)
"true"  → True    (str → bool)
"A1"    → "A1"    (str 保持，但校验 Literal 范围)
```

---

## 6. FileSecret 文件传输

`FileSecret` 用于将本地文件传输到远程执行环境，通过 Magnus 服务器中转。

### Token 格式规范

完整格式为 `magnus-secret:` 前缀 + token 本体：

```
magnus-secret:{prime}-{word}-{word}-{word}
```

| 部分 | 规则 | 示例 |
|------|------|------|
| `magnus-secret:` | 固定前缀（SDK 构造时可省略，会自动补全） | |
| `{prime}` | 4–5 位质数（1000–99999 范围） | `7919` |
| `{word}` | 3 个英文单词，每个 4–5 个小写字母 | `calm-boat-fire` |

完整示例：`magnus-secret:7919-calm-boat-fire`

Token 由服务端 `_file_custody_manager` 生成，SDK 端 `FileSecret()` 构造时会校验格式合法性。

### 蓝图中定义

```python
InputData = Annotated[FileSecret, {
    "label": "Input Data",
    "description": "Upload your dataset",
    "placeholder": "file secret code",
}]

def blueprint(data: InputData):
    # data 的值形如 "magnus-secret:7919-calm-boat-fire"
    # 在容器内用 magnus SDK 接收文件：
    #   from magnus import download_file
    #   download_file(data, "my_data.csv")
    submit_job(
        task_name="Process Data",
        entry_command="python process.py",
        repo_name="my-project",
    )
```

### 使用方式

- **Web 端**：用户既可以手动输入文件凭证，也可以在蓝图表单里直接上传单文件或小型文件夹。文件夹会先打包成 `.tar.gz`，再上传并回填 `magnus-secret:...`
- **SDK 端**：直接传文件路径，SDK 自动上传到服务器并转换为 secret
- **缓存行为**：FileSecret 与其他参数一样参与缓存预填（注意 secret 会过期）

FileSecret 与其他基本类型一样，天然支持 `Optional` 和 `List` 包装（参见 Section 4）。

SDK/CLI 的特殊行为：对 FileSecret 参数，SDK 自动将本地路径上传为 secret；列表中逐个上传，已是 `magnus-secret:` 的跳过。CLI 用重复 flag 传多文件：`--batch-files a.csv --batch-files b.csv`。

### Web 端文件输出约定

如果蓝图任务把文件产物重新托管为新的 `magnus-secret`，推荐：

```bash
echo "magnus receive $SECRET --output ./output" > "$MAGNUS_ACTION"
```

先用 `magnus custody` 或 `custody_file()` 取得 `SECRET`，检查非空后再写成功结果。必需成果上传失败必须让任务以非零状态退出；仅打包成功不算完成交付。Shell runner 可使用：

```bash
if ! SECRET=$(magnus custody ./output --secret-only); then
    echo "Required output upload failed" >&2
    exit 1
fi
test -n "$SECRET" || exit 1
echo "magnus receive $SECRET --output ./output" > "$MAGNUS_ACTION"
```

文件代管沿用 `magnus-secret` 协议。已完成上传的 secret 可跨后端重启使用，原有 TTL 和下载次数限制保持。HTTP 上传每次使用一个请求，公网网关限制仍然适用。离线 relay 任务原子发布快照后，由控制器在运行中增量导入；Job 成果的 TTL 从任务整体执行结束后起算，更晚导入的文件从就绪时起算，提前领取照常计次。完整约定见 [SDK 文件传输指南](sdk-and-cli.zh-CN.md#custody_file---代管文件)。

当前 Web 端不会执行任意 shell，只会识别安全白名单形式的 `magnus receive ...`，并映射成浏览器下载。

兼容旧蓝图时还有一个补充规则：

- 如果 `MAGNUS_ACTION` 为空，但 `MAGNUS_RESULT` 文本里包含 `magnus-secret:...`
- Web 也会从结果文本中提取 secret，并提供下载按钮

这主要是为了兼容旧版 `transfer_file` 一类蓝图：它们在 `target` 为空时只写 `MAGNUS_RESULT`，不写 `MAGNUS_ACTION`。

---

## 7. 参数缓存（Preference）

通过 Web 界面成功运行蓝图后，系统自动保存用户填写的参数值。下次打开同一蓝图时，如果蓝图签名未变化（通过 SHA256 hash 检测），自动恢复上次的参数值。

规则：
- 显式传入的参数 > 缓存的参数
- 蓝图代码修改导致参数签名变化时，缓存自动失效
- Web UI 默认合并缓存，SDK/CLI 默认不合并（避免不可见的外部状态）

---

## 8. 完整示例

### 8.1 最小蓝图

```python
def blueprint():
    submit_job(
        task_name="Hello World",
        entry_command="echo hello",
        repo_name="my-project",
    )
```

### 8.2 典型蓝图：接入一个有 CLI 的项目

```python
UserName = Annotated[str, {
    "label": "User Name",
    "placeholder": "your username on the cluster",
    "allow_empty": False,
}]

DataDir = Annotated[str, {
    "label": "Data Directory",
    "placeholder": "/home/<you>/data/exp1",
    "allow_empty": False,
}]

LearningRate = Annotated[Optional[float], {
    "label": "Learning Rate",
    "description": "Override default LR",
    "scope": "Hyperparameters",
    "min": 0.0,
    "max": 1.0,
    "placeholder": "e.g. 0.001",
}]

Epochs = Annotated[int, {
    "label": "Epochs",
    "scope": "Hyperparameters",
    "min": 1,
    "max": 1000,
}]

GpuCount = Annotated[int, {
    "label": "GPU Count",
    "min": 1,
    "max": 4,
}]

Priority = Annotated[Literal["A1", "A2", "B1", "B2"], {
    "label": "Priority",
    "options": {
        "A1": {"label": "A1", "description": "Highest priority"},
        "A2": {"label": "A2", "description": "High priority"},
        "B1": {"label": "B1", "description": "Low priority"},
        "B2": {"label": "B2", "description": "Lowest priority"},
    },
}]

def blueprint(
    user_name: UserName,
    data_dir: DataDir,
    epochs: Epochs = 100,
    learning_rate: LearningRate = None,
    gpu_count: GpuCount = 1,
    priority: Priority = "A2",
):
    cmd_parts = [
        "cd /path/to/project",
        "uv sync --quiet",
        f"uv run python train.py --data-dir '{data_dir}' --epochs {epochs}",
    ]
    if learning_rate is not None:
        cmd_parts[-1] += f" --lr {learning_rate}"

    description = f"""## Training Run
- User: {user_name}
- Data: {data_dir}
- Epochs: {epochs}, LR: {learning_rate or 'default'}
- GPUs: {gpu_count} × rtx5090
"""

    submit_job(
        task_name=f"Train-{epochs}ep",
        entry_command="\n".join(cmd_parts),
        repo_name="my-ml-project",
        description=description,
        gpu_count=gpu_count,
        gpu_type="rtx5090",
        job_type=getattr(JobType, priority),
        runner=user_name,
    )
```

### 8.3 进阶：带文件上传和参数分组

```python
InputData = Annotated[FileSecret, {
    "label": "Training Data",
    "description": "Upload via: magnus send data.tar.gz",
    "placeholder": "file secret code",
}]

BaseConfig = Annotated[Literal["default", "large", "debug"], {
    "label": "Base Config",
    "scope": "Model",
    "options": {
        "default": {"label": "Default", "description": "Standard config"},
        "large":   {"label": "Large",   "description": "Large model, more VRAM"},
        "debug":   {"label": "Debug",   "description": "Fast iteration, small batch"},
    },
}]

Notes = Annotated[Optional[str], {
    "label": "Notes",
    "scope": "Misc",
    "multi_line": True,
    "min_lines": 3,
    "placeholder": "Experiment notes (optional)",
}]

def blueprint(
    data: InputData,
    config: BaseConfig = "default",
    notes: Notes = None,
):
    entry_command = f"""cd back_end/python_scripts
uv sync --quiet
uv run python my_pipeline.py --config {config}"""

    desc = f"## Pipeline Run\n- Config: {config}\n"
    if notes:
        desc += f"\n### Notes\n{notes}\n"

    submit_job(
        task_name=f"Pipeline-{config}",
        entry_command=entry_command,
        repo_name="my-project",
        description=desc,
        gpu_type="rtx5090",
        gpu_count=1,
    )
```

---

## 9. 常见接入模式

### 模式 A：项目有 CLI 入口

最常见。蓝图参数映射到 CLI 参数，`entry_command` 拼命令行。

```python
entry_command = f"python main.py --lr {lr} --epochs {epochs}"
```

**注意 shell 注入**：用户输入的字符串参数如果拼进命令行，需要转义：

```python
def safe_quote(s: str) -> str:
    return f"'{str(s).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"

entry_command = f"python main.py --data-dir {safe_quote(data_dir)}"
```

### 模式 B：项目是 Python 脚本

直接在 `entry_command` 里调用：

```python
entry_command = f"""cd back_end/python_scripts
uv sync --quiet
uv run python my_script.py"""
```

### 模式 C：项目需要数据文件

用 `FileSecret` 参数接收文件。在项目代码中用 Magnus SDK 下载：

```python
# 蓝图中
data: InputData  # FileSecret 类型

# 项目代码中（容器内执行）
from magnus import download_file
download_file(data, "input.csv")
```

### 模式 D：纯 CPU 任务

```python
submit_job(
    ...,
    gpu_type="cpu",   # 默认值，可省略
    gpu_count=0,      # 默认值，可省略
    memory_demand="100M",
)
```

---

## 10. 硬性约束清单

1. 函数**必须**命名为 `blueprint`
2. 函数体内**必须**调用 `submit_job()`
3. 所有参数**必须**有类型注解
4. 除 `typing` 外**禁止 import**
5. 禁止文件 I/O、网络、系统调用
6. `FileSecret` 的 `allow_empty` 始终为 `False`
7. `Literal` 的选项值必须是字符串
8. 参数默认值必须与声明类型兼容
9. `Optional` 参数的默认值通常为 `None`
10. `entry_command` 和 `description` 首尾空白会被自动 strip

---

## 11. AI Agent 接入 Checklist

当 AI Agent 被要求"把这个项目接入 Magnus 蓝图"时，按以下步骤操作：

1. **理解项目**：阅读项目的 README、入口脚本、CLI 参数
2. **确定 entry_command**：找到项目的启动命令（`python main.py ...`、`bash run.sh ...` 等）
3. **识别用户参数**：哪些值应该让用户在表单里填写？哪些可以写死？
4. **选择参数类型**：
   - 文件路径/用户名/自由文本 → `str`（加 `allow_empty: False`）
   - 数值 → `int` 或 `float`（加 `min`/`max`）
   - 有限选项 → `Literal["a", "b", "c"]`
   - 可选覆盖 → `Optional[T]`
   - 上传文件 → `FileSecret`
5. **用 scope 分组**：把高级/不常改的参数放到单独的 scope
6. **调用 submit_job**：
   - `repo_name` / `namespace`：项目的 Git 信息
   - `entry_command`：拼接用户参数到启动命令
   - `gpu_type` / `gpu_count`：根据项目需求（纯 CPU 可省略）
   - `runner`：通常暴露为参数让用户填
   - `description`：用 Markdown 总结本次运行配置
7. **测试**：在 Web 编辑器中粘贴代码，检查表单是否正确生成
