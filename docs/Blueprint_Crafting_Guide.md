# Blueprint Crafting Guide

本文档是 Magnus 蓝图系统的权威参考，面向人类开发者和 AI Agent（如 Claude Code）。
目标场景：在一个新项目根目录下，根据本文档将该项目接入 Magnus 蓝图。

> **源码真相**：本文档所有细节均来自代码，而非二手描述。
> 核心文件：`back_end/server/_blueprint_manager.py`、`back_end/server/schemas.py`。

---

## 1. 蓝图是什么

一个蓝图就是一个 Python 函数 `generate_job`，它：
- 接收用户参数（自动生成前端表单）
- 返回一个 `JobSubmission` 对象（描述要提交的计算任务）

```python
def generate_job(
    user_name: UserName,
    gpu_count: GpuCount = 1,
) -> JobSubmission:
    return JobSubmission(
        task_name="My Task",
        repo_name="my-project",
        branch="main",
        commit_sha="HEAD",
        entry_command=f"python train.py --user {user_name}",
        gpu_type="rtx5090",
        gpu_count=gpu_count,
    )
```

就这么多。系统会自动：
1. 解析函数签名 → 生成前端表单
2. 用户填写参数 → Pydantic 类型转换 → 调用函数
3. 拿到 `JobSubmission` → 提交到调度器

---

## 2. 隐式导入

蓝图代码**不需要也不允许**写 import 语句。以下符号自动可用：

```python
# 自动注入到蓝图执行环境
JobSubmission, JobType, FileSecret          # from server
Annotated, Literal, Optional, List, Dict, Any  # from typing
```

如果你在本地 IDE 里编写蓝图文件（而非 Web 编辑器），可以加一段注释头帮助 IDE 补全，提交到 Web 端时忽略即可：

```python
# ============ 复制进 web 端时省略这些导入 ============
from server import JobSubmission, JobType, FileSecret
from typing import Annotated, Literal, Optional, List
# =====================================================
```

---

## 3. JobSubmission 字段参考

`generate_job` 必须返回 `JobSubmission` 实例（或可解包为 `JobSubmission` 的 dict）。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `task_name` | `str` | **是** | — | 任务显示名称 |
| `description` | `Optional[str]` | 否 | `None` | Markdown 格式的任务描述 |
| `namespace` | `str` | 否 | `"Rise-AGI"` | GitHub 组织/用户名 |
| `repo_name` | `str` | **是** | — | GitHub 仓库名 |
| `branch` | `str` | **是** | — | Git 分支 |
| `commit_sha` | `str` | **是** | — | Git commit hash，`"HEAD"` 表示最新 |
| `entry_command` | `str` | **是** | — | 容器内执行的 shell 命令（支持多行） |
| `gpu_type` | `str` | **是** | — | GPU 型号标识，如 `"rtx5090"`；纯 CPU 任务用 `"cpu"` |
| `gpu_count` | `int` | 否 | `1` | GPU 数量；纯 CPU 任务设为 `0` |
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

可以用 `JobType["A1"]` 从字符串构造。

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

蓝图代码在受限沙箱中执行。

### 可用的 builtins

**常量**：`True`, `False`, `None`

**类型**：`str`, `int`, `float`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`, `type`, `object`

**函数**：`len`, `range`, `enumerate`, `zip`, `map`, `filter`, `sorted`, `reversed`, `sum`, `min`, `max`, `abs`, `round`, `pow`, `divmod`, `any`, `all`, `isinstance`, `issubclass`, `hasattr`, `getattr`, `setattr`, `callable`, `repr`, `hash`, `id`, `print`

**异常**：`Exception`, `ValueError`, `TypeError`, `KeyError`, `IndexError`, `AttributeError`, `RuntimeError`

### 禁止的操作

- **所有 import**（`typing` 除外）
- 文件 I/O（`open`、`read`、`write`）
- 网络操作
- 系统调用（`os`、`subprocess`、`sys`）
- `eval`、`exec`、`compile`

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

### 格式

值必须以 `magnus-secret:` 开头，后跟服务器返回的文件凭证：
```
magnus-secret:7453-calm-boat-fire
```

### 蓝图中定义

```python
InputData = Annotated[FileSecret, {
    "label": "Input Data",
    "description": "Upload your dataset",
    "placeholder": "file secret code",
}]

def generate_job(data: InputData) -> JobSubmission:
    # data 的值形如 "magnus-secret:7453-calm-boat-fire"
    # 在容器内用 magnus SDK 接收文件：
    #   from magnus import download_file
    #   download_file(data, "my_data.csv")
    ...
```

### 使用方式

- **Web 端**：用户输入文件凭证（`magnus-secret:` 前缀已预填）
- **SDK 端**：直接传文件路径，SDK 自动上传到服务器并转换为 secret
- **缓存行为**：FileSecret 是一次性凭证，**永远不会**从参数缓存中预填

---

## 7. 参数缓存（Preference）

通过 Web 界面成功运行蓝图后，系统自动保存用户填写的参数值。下次打开同一蓝图时，如果蓝图签名未变化（通过 SHA256 hash 检测），自动恢复上次的参数值。

规则：
- 显式传入的参数 > 缓存的参数
- 蓝图代码修改导致参数签名变化时，缓存自动失效
- `FileSecret` 类型的参数永远不预填

---

## 8. 完整示例

### 8.1 最小蓝图

```python
def generate_job() -> JobSubmission:
    return JobSubmission(
        task_name="Hello World",
        repo_name="my-project",
        branch="main",
        commit_sha="HEAD",
        entry_command="echo hello",
        gpu_type="rtx5090",
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

def generate_job(
    user_name: UserName,
    data_dir: DataDir,
    epochs: Epochs = 100,
    learning_rate: LearningRate = None,
    gpu_count: GpuCount = 1,
    priority: Priority = "A2",
) -> JobSubmission:

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

    return JobSubmission(
        task_name=f"Train-{epochs}ep",
        description=description,
        namespace="Rise-AGI",
        repo_name="my-ml-project",
        branch="main",
        commit_sha="HEAD",
        entry_command="\n".join(cmd_parts),
        gpu_count=gpu_count,
        gpu_type="rtx5090",
        job_type=JobType[priority],
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

def generate_job(
    data: InputData,
    config: BaseConfig = "default",
    notes: Notes = None,
) -> JobSubmission:

    entry_command = f"""cd back_end/python_scripts
uv sync --quiet
uv run python my_pipeline.py --config {config}"""

    desc = f"## Pipeline Run\n- Config: {config}\n"
    if notes:
        desc += f"\n### Notes\n{notes}\n"

    return JobSubmission(
        task_name=f"Pipeline-{config}",
        description=desc,
        namespace="Rise-AGI",
        repo_name="my-project",
        branch="main",
        commit_sha="HEAD",
        entry_command=entry_command,
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
return JobSubmission(
    ...,
    gpu_type="cpu",
    gpu_count=0,
    memory_demand="100M",
)
```

---

## 10. 硬性约束清单

1. 函数**必须**命名为 `generate_job`
2. 返回类型**必须**是 `JobSubmission` 或可解包为 `JobSubmission` 的 `dict`
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

当 AI Agent（如 Claude Code）被要求"把这个项目接入 Magnus 蓝图"时，按以下步骤操作：

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
6. **填写 JobSubmission**：
   - `repo_name` / `namespace` / `branch`：项目的 Git 信息
   - `entry_command`：拼接用户参数到启动命令
   - `gpu_type` / `gpu_count`：根据项目需求
   - `runner`：通常暴露为参数让用户填
   - `description`：用 Markdown 总结本次运行配置
7. **测试**：在 Web 编辑器中粘贴代码，检查表单是否正确生成
