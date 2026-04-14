> **Language / 语言**: **English** · [简体中文](blueprint-crafting.zh-CN.md)

# Blueprint Crafting Guide

This document is the authoritative reference for the Magnus Blueprint system, aimed at human developers and AI Agents (such as Claude Code).
Target scenario: at the root of a new project, integrate that project into a Magnus Blueprint following this document.

> **Source of truth**: all details in this document come from the code, not second-hand descriptions.
> Core files: `back_end/server/_blueprint_manager.py`, `back_end/server/schemas.py`.

---

## 1. What is a Blueprint

A Blueprint is a Python function `blueprint` that:
- Accepts user parameters (auto-generates a frontend form)
- Calls `submit_job()` inside the function body to submit a compute task

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

That's all. The system automatically:
1. Parses the function signature → generates the frontend form
2. User fills in parameters → Pydantic type conversion → calls the function
3. Hijacks the `submit_job()` call → captures arguments → submits to the scheduler

**Blueprint code is "real, runnable code"** — after installing the Magnus SDK locally, Blueprint code can be executed directly.

---

## 2. Implicit Imports

Blueprint code **does not need, and is not allowed, to** write import statements. The following symbols are available automatically:

```python
# Auto-injected into the Blueprint execution environment
submit_job, JobType, FileSecret              # from magnus
Annotated, Literal, Optional, List, Dict, Any  # from typing
```

If you write the Blueprint file in a local IDE (rather than the Web editor), you can add a comment header to help IDE completion, and omit it when submitting to the Web side:

```python
# ============ Omit these imports when pasting into the web editor ============
from magnus import submit_job, JobType, FileSecret
from typing import Annotated, Literal, Optional, List
# =====================================================
```

---

## 3. submit_job Parameter Reference

The `blueprint` function must call `submit_job()`. Parameters are as follows:

| Parameter | Type | Required | Default | Description |
|------|------|------|--------|------|
| `task_name` | `str` | **Yes** | — | Task display name |
| `entry_command` | `str` | **Yes** | — | Shell command executed inside the container (multi-line supported) |
| `repo_name` | `str` | **Yes** | — | GitHub repository name |
| `branch` | `Optional[str]` | No | `None` | Git branch; when `None`, the server auto-detects the default branch |
| `commit_sha` | `Optional[str]` | No | `None` | Git commit hash; `None` is equivalent to `"HEAD"` |
| `gpu_type` | `str` | No | `"cpu"` | GPU model identifier, e.g. `"rtx5090"`; use `"cpu"` for pure CPU tasks |
| `description` | `Optional[str]` | No | `None` | Task description in Markdown format |
| `namespace` | `str` | No | `"Rise-AGI"` | GitHub organization/user name |
| `gpu_count` | `int` | No | `0` | GPU count |
| `job_type` | `JobType` | No | `JobType.A2` | Priority (see table below) |
| `container_image` | `Optional[str]` | No | `None` | Docker image URI; `None` uses the cluster default image |
| `cpu_count` | `Optional[int]` | No | `None` | CPU core count; `None` uses the cluster default |
| `memory_demand` | `Optional[str]` | No | `None` | Memory demand, e.g. `"32G"`, `"1600M"` |
| `runner` | `Optional[str]` | No | `None` | Executing user; `None` uses the cluster default |
| `system_entry_command` | `Optional[str]` | No | `None` | Host-side command before the container starts |

**`description`, `entry_command`, and `system_entry_command` are automatically stripped of leading and trailing whitespace.**

### JobType Priority

| Value | Meaning | Preemptible |
|----|------|----------|
| `JobType.A1` | Highest priority | No |
| `JobType.A2` | High priority | No |
| `JobType.B1` | Low priority | Yes (preempted by A-class) |
| `JobType.B2` | Lowest priority | Yes (preempted by A-class) |

You can use `getattr(JobType, "A1")` to construct from a string.

### commit_sha Smart Parsing

Besides passing a 40-character SHA directly or `None` (HEAD), `commit_sha` also supports the `msg:regex` pattern — it searches the most recent 200 commit messages and matches the first one:

```python
submit_job(
    ...,
    commit_sha="msg:v2\\.1",  # matches the most recent commit containing "v2.1"
)
```

---

## 4. Parameter Type System

### 4.1 Basic Types

| Python Type | Form Type | Frontend Widget |
|-------------|----------|----------|
| `str` | `"text"` | Single-line/multi-line input |
| `int` | `"number"` | Number stepper |
| `float` | `"float"` | Number input |
| `bool` | `"boolean"` | Switch |
| `Literal["a", "b", ...]` | `"select"` | Dropdown selector |
| `FileSecret` | `"file_secret"` | File secret input |

### 4.2 Type Wrappers

Basic types can be combined with the following wrappers:

| Form | Meaning | Frontend Behavior |
|------|------|----------|
| `T` | Required | Displayed directly |
| `Optional[T]` | Optional (may be None) | With an enable/disable switch |
| `List[T]` | Required list | Dynamically add/remove items |
| `Optional[List[T]]` | Optional list | Dynamic list with switch |

**Not supported**: nested generics `List[List[T]]`, `Union` types (except `Optional`), custom classes, `Dict` parameters.

### 4.3 Annotated Metadata

Use `Annotated[Type, {...}]` to attach UI metadata to a parameter. All keys are optional.

#### Common keys (all types)

| Key | Type | Description |
|-----|------|------|
| `"label"` | `str` | Field display name (default: parameter name `_` → space, Title Case) |
| `"description"` | `str` | Help text below the field |
| `"scope"` | `str` | Parameter group name (parameters with the same scope are grouped for display) |

#### str-only

| Key | Type | Default | Description |
|-----|------|------|------|
| `"allow_empty"` | `bool` | `True` | When `False`, the field cannot be empty |
| `"placeholder"` | `str` | — | Input placeholder hint |
| `"multi_line"` | `bool` | `False` | Enable multi-line text box |
| `"min_lines"` | `int` | — | Minimum number of lines for multi-line text box |
| `"color"` | `str` | — | Text CSS color |
| `"border_color"` | `str` | — | Border CSS color |

#### int / float-only

| Key | Type | Description |
|-----|------|------|
| `"min"` | `float` | Minimum value (inclusive) |
| `"max"` | `float` | Maximum value (inclusive) |
| `"placeholder"` | `str` | Placeholder hint (float only) |

#### Literal-only

| Key | Type | Description |
|-----|------|------|
| `"options"` | `Dict[value, info]` | Customize display per option. `info` can be a `str` (label) or `{"label": str, "description": str}` |

#### FileSecret-only

| Key | Type | Description |
|-----|------|------|
| `"placeholder"` | `str` | Placeholder hint |

The `allow_empty` of FileSecret is always `False` (required).

---

## 5. Sandbox Environment

Blueprint code executes in a restricted sandbox.

### Available builtins

**Constants**: `True`, `False`, `None`

**Types**: `str`, `int`, `float`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`, `type`, `object`

**Functions**: `len`, `range`, `enumerate`, `zip`, `map`, `filter`, `sorted`, `reversed`, `sum`, `min`, `max`, `abs`, `round`, `pow`, `divmod`, `any`, `all`, `isinstance`, `issubclass`, `hasattr`, `getattr`, `setattr`, `callable`, `repr`, `hash`, `id`, `print`

**Exceptions**: `Exception`, `ValueError`, `TypeError`, `KeyError`, `IndexError`, `AttributeError`, `RuntimeError`

### Forbidden operations

- **All imports** (except `typing`)
- File I/O (`open`, `read`, `write`)
- Network operations
- System calls (`os`, `subprocess`, `sys`)
- `eval`, `exec`, `compile`

### Runtime type conversion

Parameters passed from the frontend/CLI may be strings. The system converts them automatically via a Pydantic dynamic model:

```
"10"    → 10      (str → int)
"3.14"  → 3.14    (str → float)
"true"  → True    (str → bool)
"A1"    → "A1"    (str preserved, but validated against the Literal range)
```

---

## 6. FileSecret File Transfer

`FileSecret` is used to transfer local files to the remote execution environment, relayed through the Magnus server.

### Token format specification

The full format is the `magnus-secret:` prefix plus the token body:

```
magnus-secret:{prime}-{word}-{word}-{word}
```

| Part | Rule | Example |
|------|------|------|
| `magnus-secret:` | Fixed prefix (may be omitted when constructing in the SDK; it is auto-completed) | |
| `{prime}` | 4–5 digit prime (1000–99999 range) | `7919` |
| `{word}` | 3 English words, each 4–5 lowercase letters | `calm-boat-fire` |

Full example: `magnus-secret:7919-calm-boat-fire`

The token is generated by the server-side `_file_custody_manager`, and on the SDK side, `FileSecret()` validates the format on construction.

### Defining in a Blueprint

```python
InputData = Annotated[FileSecret, {
    "label": "Input Data",
    "description": "Upload your dataset",
    "placeholder": "file secret code",
}]

def blueprint(data: InputData):
    # data's value looks like "magnus-secret:7919-calm-boat-fire"
    # Inside the container, receive the file using the magnus SDK:
    #   from magnus import download_file
    #   download_file(data, "my_data.csv")
    submit_job(
        task_name="Process Data",
        entry_command="python process.py",
        repo_name="my-project",
    )
```

### Usage

- **Web**: users can either manually enter the file secret or upload a single file or a small folder directly in the Blueprint form. A folder is first packed into `.tar.gz`, then uploaded and filled back in as `magnus-secret:...`
- **SDK**: pass the file path directly; the SDK automatically uploads to the server and converts it to a secret
- **Caching behavior**: FileSecret participates in cache pre-fill like other parameters (note that secrets can expire)

Like other basic types, FileSecret naturally supports `Optional` and `List` wrappers (see Section 4).

SDK/CLI special behavior: for FileSecret parameters, the SDK automatically uploads a local path as a secret; in a list, each item is uploaded one by one, and items already in `magnus-secret:` form are skipped. In CLI, use repeated flags to pass multiple files: `--batch-files a.csv --batch-files b.csv`.

### Web-side file output convention

If a Blueprint task re-hosts its file artifacts as a new `magnus-secret`, it is recommended to:

```bash
echo "magnus receive $SECRET --output ./output" > "$MAGNUS_ACTION"
```

Currently the Web side does not execute arbitrary shell; it only recognizes the safe whitelist form `magnus receive ...` and maps it to a browser download.

For backward compatibility with older Blueprints, there is an additional rule:

- If `MAGNUS_ACTION` is empty, but the `MAGNUS_RESULT` text contains `magnus-secret:...`
- The Web side will also extract the secret from the result text and provide a download button

This is mainly for compatibility with old `transfer_file`-type Blueprints: when `target` is empty, they only write `MAGNUS_RESULT` without writing `MAGNUS_ACTION`.

---

## 7. Parameter Caching (Preference)

After successfully running a Blueprint via the Web interface, the system automatically saves the parameter values filled by the user. Next time the same Blueprint is opened, if the Blueprint signature has not changed (detected by SHA256 hash), the previous parameter values are automatically restored.

Rules:
- Explicitly passed parameters > cached parameters
- When Blueprint code changes cause the parameter signature to change, the cache is automatically invalidated
- The Web UI merges cache by default; the SDK/CLI does not merge by default (to avoid invisible external state)

---

## 8. Complete Examples

### 8.1 Minimal Blueprint

```python
def blueprint():
    submit_job(
        task_name="Hello World",
        entry_command="echo hello",
        repo_name="my-project",
    )
```

### 8.2 Typical Blueprint: integrating a project with a CLI

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

### 8.3 Advanced: with file upload and parameter grouping

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

## 9. Common Integration Patterns

### Pattern A: Project has a CLI entry point

The most common. Blueprint parameters map to CLI arguments, and `entry_command` assembles the command line.

```python
entry_command = f"python main.py --lr {lr} --epochs {epochs}"
```

**Beware of shell injection**: if user-provided string arguments are concatenated into the command line, they need to be escaped:

```python
def safe_quote(s: str) -> str:
    return f"'{str(s).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"

entry_command = f"python main.py --data-dir {safe_quote(data_dir)}"
```

### Pattern B: Project is a Python script

Invoke it directly in `entry_command`:

```python
entry_command = f"""cd back_end/python_scripts
uv sync --quiet
uv run python my_script.py"""
```

### Pattern C: Project needs data files

Use a `FileSecret` parameter to receive the file. In project code, download it using the Magnus SDK:

```python
# In the Blueprint
data: InputData  # FileSecret type

# In project code (executed inside the container)
from magnus import download_file
download_file(data, "input.csv")
```

### Pattern D: Pure CPU task

```python
submit_job(
    ...,
    gpu_type="cpu",   # default, can be omitted
    gpu_count=0,      # default, can be omitted
    memory_demand="100M",
)
```

---

## 10. Hard Constraints Checklist

1. The function **must** be named `blueprint`
2. The function body **must** call `submit_job()`
3. All parameters **must** have type annotations
4. **Imports are forbidden** except for `typing`
5. File I/O, network, and system calls are forbidden
6. The `allow_empty` of `FileSecret` is always `False`
7. The option values of `Literal` must be strings
8. Parameter default values must be compatible with the declared type
9. The default value of an `Optional` parameter is usually `None`
10. Leading and trailing whitespace of `entry_command` and `description` are stripped automatically

---

## 11. AI Agent Integration Checklist

When an AI Agent (such as Claude Code) is asked to "integrate this project into a Magnus Blueprint", follow these steps:

1. **Understand the project**: read the project's README, entry scripts, and CLI arguments
2. **Determine entry_command**: find the project's launch command (`python main.py ...`, `bash run.sh ...`, etc.)
3. **Identify user parameters**: which values should the user fill in the form? Which can be hard-coded?
4. **Choose parameter types**:
   - File path / user name / free text → `str` (add `allow_empty: False`)
   - Numeric → `int` or `float` (add `min`/`max`)
   - Finite options → `Literal["a", "b", "c"]`
   - Optional override → `Optional[T]`
   - File upload → `FileSecret`
5. **Group with scope**: put advanced / rarely changed parameters into a separate scope
6. **Call submit_job**:
   - `repo_name` / `namespace`: the project's Git information
   - `entry_command`: assemble user parameters into the launch command
   - `gpu_type` / `gpu_count`: based on project needs (can be omitted for pure CPU)
   - `runner`: usually exposed as a parameter for the user to fill
   - `description`: summarize this run's configuration in Markdown
7. **Test**: paste the code into the Web editor and check that the form is generated correctly
