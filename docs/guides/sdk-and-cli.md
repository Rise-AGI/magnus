> **Language / 语言**: **English** · [简体中文](sdk-and-cli.zh-CN.md)

# Magnus SDK & CLI Guide

The CLI API is consistent with the Python SDK. The same operations have the same semantics and parameter structures across different interfaces.

---

## Table of Contents

- [Python SDK](#python-sdk)
  - [Installation](#installation)
  - [Environment Configuration](#environment-configuration)
  - [Configuration Priority](#configuration-priority)
  - [In-Container Environment Variables](#in-container-environment-variables)
  - [Job](#job)
  - [Blueprint](#blueprint)
  - [Service Invocation](#service-invocation)
  - [Cluster and Resources](#cluster-and-resources)
  - [File Transfer](#file-transfer)
  - [File Custody](#file-custody)
  - [Skill](#skill)
  - [Image](#image)
  - [Async API](#async-api)
  - [API Reference](#api-reference)
- [Command Line Tool (CLI)](#command-line-tool-cli)
  - [Command Structure](#command-structure)
  - [Global Options](#global-options)
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
- [Appendix](#appendix)
  - [Output Formats](#output-formats)
  - [Error Codes](#error-codes)
  - [FAQ](#faq)

---

## Python SDK

### Installation

```bash
pip install magnus-sdk        # pip
uv add magnus-sdk             # uv
cd sdks/python && pip install -e .  # source
```

### Environment Configuration

The SDK connects to the backend via two configuration items:

| Config | Description | Example |
|--------|-------------|---------|
| `address` | Backend address | `http://your-magnus-host:8017` |
| `token` | Trust Token, obtained from the user menu in the Web UI | `sk-aBcDeFgHiJkLmNoPqRsTuVwXyZaB` |

Interactive configuration (saved to `~/.magnus/config.json`):

```bash
magnus login
```

Environment variables (higher priority than the config file):

```bash
export MAGNUS_TOKEN="sk-your-trust-token"
export MAGNUS_ADDRESS="http://your-server:8017"
```

In-code configuration (highest priority):

```python
import magnus

magnus.configure(
    token="sk-your-trust-token",
    address="http://your-server:8017",
)
```

### Configuration Priority

Resolved in the following order; the first match wins:

| Priority | Source | Notes |
|----------|--------|-------|
| 1 (highest) | `magnus.configure()` explicit arguments | Only affects the current process |
| 2 | Environment variables `MAGNUS_TOKEN` / `MAGNUS_ADDRESS` | CI/CD, in-container injection, `.bashrc` |
| 3 | `~/.magnus/config.json` | Written by `magnus login`, takes effect across shells |
| 4 (lowest) | Default values | address defaults to `http://127.0.0.1:8017`, token defaults to empty |

Notes:

- `magnus login` is configured once and is immediately available in all terminals; no need to `source` or restart the terminal.
- Users who have already added `export MAGNUS_TOKEN=...` in `.bashrc` are unaffected, since environment variables have higher priority.
- Magnus injects the `MAGNUS_TOKEN` environment variable when executing jobs inside containers, so in Job-in-Job scenarios the inner job's SDK calls need no additional configuration.
- The `magnus config` command shows the actual source of each config item (env / file / default).

### In-Container Environment Variables

When a job runs inside an Apptainer container, the following environment variables are automatically injected:

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `MAGNUS_HOME` | The Magnus root directory inside the container (`$HOME` is isolated by `--containall` and points to the container default, different from this) | `/magnus` |
| `MAGNUS_TOKEN` | The current user's Trust Token | `sk-...` |
| `MAGNUS_ADDRESS` | Backend address | `http://your-magnus-host:3011` |
| `MAGNUS_JOB_ID` | Current Job ID | `abc123` |
| `MAGNUS_RESULT` | Path to the result file; its contents are returned as the job result | `$MAGNUS_HOME/workspace/.magnus_result` |
| `MAGNUS_ACTION` | Path to the action file; commands written to this file are auto-executed by the client | `$MAGNUS_HOME/workspace/.magnus_action` |

The workspace is located at `$MAGNUS_HOME/workspace/`, and the code repository is at `$MAGNUS_HOME/workspace/repository/`.

Jobs run in `--containall` mode: the host's `$HOME`, `/tmp`, and environment variables do not leak into the container. Inside the container, `$HOME` points to an in-memory temporary directory (tmpfs), unrelated to `$MAGNUS_HOME`. The container filesystem provides a writable layer through Ephemeral Storage overlay (size configurable when submitting the job), which is destroyed when the job ends.

### Job

A Job is Magnus's basic execution unit. Each Job contains an entry command, a code repository reference, and resource configuration, executed in a cluster container.

#### submit_job - Submit a Job

Returns the Job ID immediately after submission; does not wait for completion.

```python
import magnus

job_id = magnus.submit_job(
    task_name="My Experiment",
    entry_command="python train.py --lr 0.001",
    repo_name="my-project",
)
print(job_id)
```

**Parameters**:

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `task_name` | str | Yes | - | Task name |
| `entry_command` | str | Yes | - | Entry command |
| `repo_name` | str | Yes | - | Repository name |
| `branch` | str \| None | No | None | Branch name (None = server-side detects default branch) |
| `commit_sha` | str \| None | No | None | Commit SHA (None = HEAD) |
| `gpu_type` | str | No | "cpu" | GPU model ("cpu" means no GPU used) |
| `gpu_count` | int | No | 0 | Number of GPUs |
| `namespace` | str | No | "Rise-AGI" | Namespace |
| `job_type` | str | No | "A2" | Priority (A1/A2/B1/B2) |
| `description` | str \| None | No | None | Task description (Markdown) |
| `container_image` | str \| None | No | None | Container image (None = cluster default) |
| `cpu_count` | int \| None | No | None | CPU count (None = cluster default) |
| `memory_demand` | str \| None | No | None | Memory demand (None = cluster default) |
| `runner` | str \| None | No | None | Runner (None = cluster default) |

**Returns**: Job ID (str)

#### execute_job - Submit and Wait for Completion

The blocking version of `submit_job`: submits and then polls while waiting for completion. During polling, transient network errors or 5xx responses automatically trigger exponential backoff retries.

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

**Additional Parameters** (on top of `submit_job`):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | float \| None | None | Wait timeout (seconds), None = wait indefinitely |
| `poll_interval` | float | 2.0 | Polling interval (seconds) |
| `execute_action` | bool | True | Whether to auto-execute the action after completion |

**Returns**: `Optional[str]`, the content written to `MAGNUS_RESULT` by the job

**Exceptions**:
- `TimeoutError`: timed out
- `ExecutionError`: job status is Failed or Terminated

#### list_jobs - List Jobs

```python
import magnus

result = magnus.list_jobs(limit=50, search="quadre")

for job in result["items"]:
    print(f"{job['id']} | {job['task_name']} | {job['status']}")
```

**Parameters**:
- `limit` (int, optional): Number of results to return, default 20
- `skip` (int, optional): Number to skip, default 0
- `search` (str, optional): Search by task name or ID

**Returns**: `{"total": int, "items": list}`

#### get_job - Get Job Details

```python
import magnus

job = magnus.get_job("abc123")

print(f"Task: {job['task_name']}")
print(f"Status: {job['status']}")
print(f"SLURM Job ID: {job['slurm_job_id']}")
```

**Parameters**:
- `job_id` (str): Job ID

**Returns**: dict containing `id`, `task_name`, `status`, `entry_command`, `repo_name`, `gpu_type`, `gpu_count`, `slurm_job_id`, `created_at`, `start_time`, `result`, `action`, etc.

#### get_job_result - Get Job Result

Reads the content written by the job to the `MAGNUS_RESULT` file. Returns None if the job did not write a result.

```python
import magnus

result = magnus.get_job_result("abc123")
if result is not None:
    print(result)
```

**Parameters**:
- `job_id` (str): Job ID
- `timeout` (float, optional): HTTP timeout (seconds), default 10

**Returns**: `Optional[str]`

#### get_job_action - Get Job Action

Reads the content written by the job to the `MAGNUS_ACTION` file. This content is typically a shell command, executed by the client.

```python
import magnus

action = magnus.get_job_action("abc123")
if action is not None:
    print(action)
```

**Parameters**:
- `job_id` (str): Job ID
- `timeout` (float, optional): HTTP timeout (seconds), default 10

**Returns**: `Optional[str]`

#### get_job_logs - Get Job Logs

```python
import magnus

result = magnus.get_job_logs("abc123")
print(f"Page {result['page'] + 1}/{result['total_pages']}")
print(result["logs"])

# Get the first page
result = magnus.get_job_logs("abc123", page=0)
```

**Parameters**:
- `job_id` (str): Job ID
- `page` (int, optional): Page number, -1 means the latest page, default -1

**Returns**: `{"logs": str, "page": int, "total_pages": int}`

#### terminate_job - Terminate Job

```python
import magnus

magnus.terminate_job("abc123")
```

**Parameters**:
- `job_id` (str): Job ID

> **Admin Privilege**: Administrators (users configured in `feishu_client.admins`) can terminate anyone's job; non-admins can only terminate their own jobs.

#### Typical Workflow

Submit → poll → check logs → fetch result, a complete loop:

```python
import magnus
import time

# 1. Submit
job_id = magnus.submit_job(
    task_name="train-resnet",
    entry_command="python train.py --epochs 50",
    repo_name="ml-experiments",
    gpu_type="A100",
    gpu_count=4,
    job_type="A2",
)

# 2. Wait for completion
while True:
    job = magnus.get_job(job_id)
    status = job["status"]
    if status in ("Success", "Failed", "Terminated"):
        break
    time.sleep(5)

# 3. Check logs
logs = magnus.get_job_logs(job_id)
print(logs["logs"])

# 4. Fetch result
if status == "Success":
    result = magnus.get_job_result(job_id)
    print(result)

    action = magnus.get_job_action(job_id)
    if action:
        print(f"Action: {action}")
```

Or use `execute_job` to do it all in one step:

```python
result = magnus.execute_job(
    task_name="train-resnet",
    entry_command="python train.py --epochs 50",
    repo_name="ml-experiments",
    gpu_type="A100",
    gpu_count=4,
)
```

### Blueprint

A Blueprint is a wrapper around a Job. A blueprint is a Python file that defines a `blueprint()` function, whose signature parameters map to a frontend form via `Annotated` type annotations. The blueprint internally calls `submit_job()` to submit the actual job.

The `submit_job()` call inside blueprint code has the same parameters as the SDK's `magnus.submit_job()`. After installing the SDK, blueprint code can run directly locally:

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

# Run directly
blueprint("alice")
```

#### launch_blueprint - Submit a Blueprint Job

Returns the Job ID immediately after submission.

```python
import magnus

job_id = magnus.launch_blueprint("quadre-simulation")

# With parameters
job_id = magnus.launch_blueprint(
    "quadre-simulation",
    args={
        "Te": "2.0",
        "B": "1.5",
        "data_dir": "/home/user/outputs/exp1",
    },
)

# Merge with user preference parameters
job_id = magnus.launch_blueprint(
    "quadre-simulation",
    args={"Te": "2.0"},
    use_preference=True,
)
```

**Parameters**:
- `blueprint_id` (str): Blueprint ID
- `args` (dict, optional): Arguments passed to the blueprint function
- `use_preference` (bool, optional): Whether to merge the user's cached preference parameters, default False (Web UI default True)
- `save_preference` (bool, optional): Save arguments as preference on success, default True
- `expire_minutes` (int, optional): Expiration time (minutes) for auto-uploaded FileSecret, default 60
- `max_downloads` (int, optional): Max downloads for auto-uploaded FileSecret, default 1

**Returns**: Job ID (str)

When parameter validation fails, the error message includes the blueprint's full parameter schema (containing each parameter's type, allowed values, and description).

#### run_blueprint - Submit and Wait for Completion

Submits and then polls while waiting for completion, returning the result. During polling, transient network errors or 5xx responses automatically trigger exponential backoff retries. When the job writes `MAGNUS_ACTION`, it is by default auto-executed on the client.

```python
import magnus

result = magnus.run_blueprint("my-blueprint", args={"param": "value"})

# Timeout and polling interval
result = magnus.run_blueprint(
    "long-running-task",
    args={"input": "data"},
    timeout=3600,
    poll_interval=10,
)

# Do not auto-execute the action
result = magnus.run_blueprint("my-blueprint", execute_action=False)
```

**Parameters**:
- `blueprint_id` (str): Blueprint ID
- `args` (dict, optional): Arguments passed to the blueprint function
- `use_preference` (bool, optional): Merge preference parameters, default False
- `save_preference` (bool, optional): Save as preference, default True
- `expire_minutes` (int, optional): FileSecret expiration (minutes), default 60
- `max_downloads` (int, optional): FileSecret max downloads, default 1
- `timeout` (float, optional): Timeout (seconds), default wait indefinitely
- `poll_interval` (float, optional): Polling interval (seconds), default 2
- `execute_action` (bool, optional): Auto-execute action, default True

**Returns**: `Optional[str]`

**Exceptions**:
- `TimeoutError`: timed out
- `ExecutionError`: job failed

#### list_blueprints - List Blueprints

```python
import magnus

blueprints = magnus.list_blueprints(limit=20)

for bp in blueprints["items"]:
    print(f"{bp['id']} | {bp['title']}")
```

**Parameters**:
- `limit` (int, optional): Number of results to return, default 20
- `skip` (int, optional): Number to skip, default 0
- `search` (str, optional): Search by title or ID

**Returns**: `{"total": int, "items": list}`

#### get_blueprint - Get Blueprint Details

```python
import magnus

bp = magnus.get_blueprint("quadre-simulation")

print(bp["title"])
print(bp["description"])
print(bp["code"])
```

**Parameters**:
- `blueprint_id` (str): Blueprint ID

**Returns**: dict containing `id`, `title`, `description`, `code`, `user_id`, `updated_at`, `user`, etc.

#### get_blueprint_schema - Get Parameter Schema

Returns the types, constraints, and descriptions of the parameters in the blueprint function signature. Parameters of `Literal` type include a full `options` list.

```python
import magnus

schema = magnus.get_blueprint_schema("my-blueprint")
for param in schema:
    print(f"{param['key']}: {param['type']}")
    if param.get("options"):
        for opt in param["options"]:
            print(f"  {opt['value']}: {opt.get('description', '')}")
```

**Parameters**:
- `blueprint_id` (str): Blueprint ID

**Returns**: list, each item containing `key`, `type`, `label`, `description`, `default`, `is_optional`, `is_list`, `options`, etc.

#### save_blueprint - Create or Update a Blueprint

The backend has upsert semantics: same ID with same owner updates, new ID creates.

```python
import magnus

# From a code string
bp = magnus.save_blueprint(
    blueprint_id="my-new-blueprint",
    title="My Blueprint",
    description="A test blueprint",
    code=open("blueprint.py").read(),
)
```

**Parameters**:
- `blueprint_id` (str): Blueprint ID
- `title` (str): Title
- `description` (str): Description
- `code` (str): Python code

**Returns**: dict, the saved blueprint info

> **Admin Privilege**: Administrators can overwrite anyone's blueprint; non-admins can only update their own blueprints.

#### delete_blueprint - Delete a Blueprint

```python
import magnus

magnus.delete_blueprint("my-old-blueprint")
```

**Parameters**:
- `blueprint_id` (str): Blueprint ID

**Returns**: `None` (HTTP 204 No Content)

> **Admin Privilege**: Administrators can delete anyone's blueprint; non-admins can only delete their own blueprints.

### Service Invocation

#### call_service - Invoke an Elastic Service

```python
import magnus

response = magnus.call_service("llm-inference", payload={"prompt": "Hello!"})

response = magnus.call_service(
    "image-generation",
    payload={"prompt": "sunset", "width": 1024},
    timeout=120,
)
```

**Parameters**:
- `service_id` (str): Service ID
- `payload` (dict): Request payload
- `timeout` (int, optional): Timeout (seconds), default 60

**Returns**: dict

**Exceptions**:
- `TimeoutError`: timed out (the server-side request is not interrupted as a result)
- `MagnusError`: service unavailable

#### list_services - List Services

```python
import magnus

services = magnus.list_services(limit=20)
services = magnus.list_services(active_only=True)

for svc in services["items"]:
    print(f"{svc['id']} | {svc['name']} | Active: {svc['is_active']}")
```

**Parameters**:
- `limit` (int, optional): Number to return, default 20
- `skip` (int, optional): Number to skip, default 0
- `search` (str, optional): Search by name or ID
- `active_only` (bool, optional): Return only active services, default False

**Returns**: `{"total": int, "items": list}`

### Cluster and Resources

#### get_cluster_stats - Get Cluster Status

```python
import magnus

stats = magnus.get_cluster_stats()

resources = stats["resources"]
print(f"GPU: {resources['gpu_model']} x {resources['total']}")
print(f"Free: {resources['free']}")
print(f"Running: {stats['total_running']}, Pending: {stats['total_pending']}")
```

**Returns**: dict containing `resources`, `running_jobs`, `pending_jobs`, etc.

### File Transfer

In blueprints, use the `FileSecret` type to declare file parameters; the SDK automatically handles uploads. In blueprint code, use `download_file` to receive them.

File flow is divided into two layers:

1. **Job workspace**: Each job has an independent temporary working directory, and the code repository appears inside the container at `$MAGNUS_HOME/workspace/repository/`.
2. **File Custody transit layer**: User-uploaded local files or directories are not directly "mounted" into the container; they are first uploaded to server-side file custody, then passed to the job as `magnus-secret:...`; the job explicitly downloads them at runtime.

This means Magnus's file protocol is closer to "object passing" than to "shared disk mounting":

- **Input**: local path -> upload -> `FileSecret`
- **Inside job**: `FileSecret` -> download to a local path inside the container
- **Output**: job artifacts -> custody again -> new `FileSecret`

#### When to Use FileSecret and When Not To

`FileSecret` is suitable for:

- One-off experiment inputs
- Files or directories that need to travel with a single job
- Small to medium-sized result returns

Not suitable for:

- Long-term reusable large model weight directories
- Hundreds-of-GB-scale large datasets
- Shared assets that need long-term retention

For long-term reusable large files, it is recommended to:

- Place base models on cluster shared storage / object storage
- Place datasets on persistent data disks
- Have the blueprint only pass "paths, versions, manifests, or object storage URIs"

#### FileSecret Parameters - Auto Upload

When a blueprint parameter is typed as `FileSecret`, `launch_blueprint` / `run_blueprint` will automatically detect local file paths and upload them, replacing the path with a file secret. `FileSecret` supports `Optional` and `List` wrapping.

```python
import magnus

# Single file
job_id = magnus.launch_blueprint(
    "my-blueprint",
    args={"input_data": "/home/user/data.csv"},
)

# Multiple files (List[FileSecret])
job_id = magnus.launch_blueprint(
    "batch-process",
    args={"files": ["/home/user/a.csv", "/home/user/b.csv"]},
)

# Mixing existing secrets with local paths
job_id = magnus.launch_blueprint(
    "batch-process",
    args={"files": ["magnus-secret:abc-123", "/home/user/new.csv"]},
)
```

Auto upload details:

- When a regular file path is passed, the SDK directly uploads the file content
- When a directory path is passed, the SDK first packs the directory into `tar.gz` and uploads it
- The server returns `magnus-secret:xxxx`, and what the blueprint sees at runtime is this string
- Uploaded files are subject to TTL and download-count limits; defaults are `expire_minutes=60`, `max_downloads=1`

If you just want to get a secret without immediately running a blueprint, you can also manually call `custody_file()` first, then fill the returned secret into the blueprint parameters.

#### Web UI Upload and Download

If you use file features from the Magnus Web UI rather than initiating jobs from a local SDK/CLI, there are now two entry points:

1. **`FileSecret` field in the blueprint runner**: supports directly selecting a single file, or selecting a small folder to upload; on successful upload, `magnus-secret:...` is automatically filled in
2. **`Files` page in the left navigation**: suitable for uploading a file/folder separately to get a secret, or downloading a file in the browser using an existing secret

The boundaries of this Web entry need to be made clear:

- Files are uploaded directly; **folders are first packed into `.tar.gz` in the browser before upload**
- When downloading a "directory secret" in the browser, what you get is the archive file itself
- If a directory secret is received in a job environment via `download_file()` or `magnus receive`, the SDK/CLI will auto-unpack it
- Better suited for config files, sample data, small result directories, i.e., **small files, small directories, and temporary files**
- Not suitable for large model weights, large datasets, huge directories, or long-term reusable assets

If you have obtained `magnus-secret:...` in the browser, you can subsequently:

- Fill it directly into a blueprint parameter
- Download it again on the `Files` page
- Download it in a job via `download_file()` or `magnus receive` into the working directory

For scenarios such as LLM training and post-training, Web upload is better regarded only as a "temporary convenience entry"; the primary paths for base models, datasets, and checkpoints should still be placed on shared storage, object storage, or server paths.

#### How to Declare File Parameters in a Blueprint

```python
from magnus import submit_job, JobType, FileSecret
from typing import Annotated

BaseModel = Annotated[FileSecret, {
    "label": "Base Model",
    "description": "Base model directory or archive",
}]

Dataset = Annotated[FileSecret, {
    "label": "Dataset",
    "description": "Training data directory or data file",
}]

def blueprint(
    base_model: BaseModel,
    dataset: Dataset,
):
    submit_job(
        task_name="llm-sft",
        repo_name="my-trainer",
        entry_command="bash scripts/train.sh",
        gpu_type="A100",
        gpu_count=4,
        job_type=JobType.A2,
    )
```

#### download_file - Receive Files

Receives files in the job execution environment.

```python
from magnus import download_file

download_file(file_secret, "/workspace/data/input.csv")
download_file(file_secret, "data/input.csv")  # relative path
```

**Parameters**:
- `file_secret` (str): file secret, with or without the `magnus-secret:` prefix
- `target_path` (str): target path
- `timeout` (float, optional): timeout (seconds), default wait indefinitely
- `overwrite` (bool, optional): overwrite existing file, default True

**Returns**: `Path`

**Exceptions**:
- `MagnusError`: file does not exist, has expired, or transfer failed

`download_file_async` is the async version with the same parameters.

Behavior notes:

- `file_secret` can be passed with the `magnus-secret:` prefix or just the token itself
- If the downloaded object is a directory, the SDK automatically unpacks it to the target path
- If the downloaded object is a file, the SDK writes it directly as that file
- Temporary unpacking/download intermediate files inside a job are preferentially placed under `$MAGNUS_HOME/.tmp/`

#### Recommended In-Job Receive Pattern

In most cases, it is recommended that the training script corresponding to `entry_command` first downloads all inputs to the local working directory, and then starts training:

```python
from magnus import download_file

download_file(base_model_secret, "/tmp/base_model")
download_file(dataset_secret, "/tmp/dataset")
```

Or use the CLI:

```bash
magnus receive "$BASE_MODEL_SECRET" --output /tmp/base_model
magnus receive "$DATASET_SECRET" --output /tmp/dataset
```

The benefits of this approach are:

- The training script faces real local paths and does not need to know about `FileSecret`
- Directory unpacking and path organization are uniformly handled by the SDK/CLI
- Model training frameworks (Transformers, DeepSpeed, Megatron, etc.) can directly read local directories

#### Two Ways to Return Job Results

After a job completes, results typically fall into two categories:

1. **Small text results**: written to `MAGNUS_RESULT`
2. **Large file/directory results**: first put into custody, then have a download command written to `MAGNUS_ACTION`

`MAGNUS_RESULT` is suitable for:

- Whether training succeeded
- A checkpoint path summary
- A metrics JSON
- An evaluation report summary

`MAGNUS_ACTION` is suitable for:

- Checkpoint directories
- LoRA adapter directories
- Exported tokenizer / config
- Evaluation artifact directories

Example:

```bash
echo '{"success": true, "best_step": 1200}' > "$MAGNUS_RESULT"

SECRET=$(magnus custody ./outputs/checkpoint-1200)
echo "magnus receive $SECRET --output ./checkpoint-1200" > "$MAGNUS_ACTION"
```

When you use `magnus run` / `magnus blueprint run`, the client by default auto-executes `MAGNUS_ACTION`, downloading artifacts back to the local machine.

#### The Actual Backend Logic of `MAGNUS_RESULT` / `MAGNUS_ACTION`

These two conventions are essentially just **marker files in the Job working directory**:

- `MAGNUS_RESULT` corresponds to `{magnus_root}/workspace/jobs/{job_id}/.magnus_result` on the host
- `MAGNUS_ACTION` corresponds to `{magnus_root}/workspace/jobs/{job_id}/.magnus_action` on the host

When the scheduler starts a job, it injects these two paths into the container's environment variables. During job execution, the user script is responsible for writing them; the backend does not proactively parse their business semantics, only lazily reads and returns their content in the Job API.

In other words:

- The backend **does not auto-download artifacts**
- The backend **does not auto-execute shell**
- The backend is only responsible for exposing the content of `result/action` to the client

In the current Magnus:

- The SDK / CLI client reads `MAGNUS_ACTION` and auto-executes it by default
- The Web client does not execute arbitrary shell; it only maps a supported safe subset

#### How the Web Handles File Output

The Web does not directly execute the shell commands in `MAGNUS_ACTION`. It supports only a safe whitelist:

```bash
magnus receive <file-secret>
magnus receive <file-secret> --output <target>
magnus receive <file-secret> -o <target>
```

When a Job's `action` matches this pattern, the Web will:

1. Parse `file_secret` from the action
2. Call `/api/files/download/{token}` to trigger a browser download
3. If the action has `--output/-o`, treat it as a **suggested download name**

Notes:

- The Web does not treat `target` as an absolute local path for the browser to execute
- For the browser, `target` is only a suggested filename or relative save name
- If `action` is not in the supported `magnus receive ...` form, the Web just shows the raw text and does not execute

There is an additional compatibility rule for legacy blueprints:

- If the Job did not write `MAGNUS_ACTION`
- But `MAGNUS_RESULT` text contains `magnus-secret:...`
- The Web will also extract the secret from the result text and provide a download button

This is mainly used for compatibility with the legacy `transfer_file` blueprint: when `target` is empty, it only writes a download hint into `MAGNUS_RESULT`.

#### Large Model Post-Training Example

Below is a typical SFT / continued pretraining file flow:

1. Local invocation:

```python
import magnus

result = magnus.run_blueprint(
    "llm-sft",
    args={
        "base_model": "/data/models/qwen2.5-7b",
        "dataset": "/data/datasets/my-sft",
    },
)
print(result)
```

2. SDK auto-behavior:

- Uploads `/data/models/qwen2.5-7b` as a `FileSecret`
- Uploads `/data/datasets/my-sft` as a `FileSecret`
- Passes both secrets to the blueprint

3. Training script inside the container:

```bash
magnus receive "$BASE_MODEL_SECRET" --output /tmp/base_model
magnus receive "$DATASET_SECRET" --output /tmp/dataset
python train.py \
  --model_name_or_path /tmp/base_model \
  --data_path /tmp/dataset \
  --output_dir /tmp/output
```

4. Return transfer after training:

```bash
echo '{"success": true, "message": "training finished"}' > "$MAGNUS_RESULT"
SECRET=$(magnus custody /tmp/output)
echo "magnus receive $SECRET --output ./output" > "$MAGNUS_ACTION"
```

5. Local client:

- First reads `MAGNUS_RESULT`
- Then auto-executes `MAGNUS_ACTION`
- Ultimately downloads the training artifacts to local `./output`

### File Custody

#### custody_file - Put a File into Custody

Uploads a local file/folder to the backend for custody, and returns a file_secret.

```python
import magnus
import os

secret = magnus.custody_file("/path/to/results.csv")

secret = magnus.custody_file(
    "/path/to/output_dir",
    expire_minutes=120,
)

# Combine with MAGNUS_ACTION for one-click transfer
secret = magnus.custody_file("/workspace/processed.pdf")
with open(os.environ["MAGNUS_ACTION"], "w") as f:
    f.write(f"magnus receive {secret} -o processed.pdf\n")
```

**Parameters**:
- `path` (str): local file or folder path
- `expire_minutes` (int, optional): expiration time (minutes), default 60
- `max_downloads` (int, optional): max downloads, default unlimited
- `timeout` (float, optional): HTTP timeout (seconds), default 300

**Returns**: `str`, in `magnus-secret:xxxx` format

`custody_file_async` is the async version with the same parameters.

Additional notes:

- Directories are automatically packed and uploaded; automatically unpacked on download
- `expire_minutes` is subject to the maximum TTL configured on the server
- `max_downloads=1` is suitable for "one-off return"; when not set, downloads are unlimited
- File custody is a transit layer, not a long-term archival system; files are cleaned up by the server after expiration

#### Manual Upload/Download Loop

If you are not going through a blueprint's auto-upload, you can also manually walk the full loop:

```python
import magnus

secret = magnus.custody_file("./dataset")
print(secret)

path = magnus.download_file(secret, "./restored_dataset")
print(path)
```

Corresponding CLI:

```bash
magnus custody ./dataset
magnus receive magnus-secret:7919-calm-boat-fire --output ./restored_dataset
```

### Skill

A Skill is Magnus's reusable code package, containing multiple files (must include a `SKILL.md` description file).

#### list_skills - List Skills

```python
import magnus

skills = magnus.list_skills(limit=20, search="pytorch")

for skill in skills["items"]:
    print(f"{skill['id']} | {skill['title']}")
```

**Parameters**:
- `limit` (int, optional): number to return, default 20
- `skip` (int, optional): number to skip, default 0
- `search` (str, optional): search by title or ID

**Returns**: `{"total": int, "items": list}`

#### get_skill - Get Skill Details

```python
import magnus

skill = magnus.get_skill("my-skill")
for f in skill["files"]:
    print(f"{f['path']}: {len(f['content'])} chars")
```

**Parameters**:
- `skill_id` (str): Skill ID

**Returns**: dict containing `id`, `title`, `description`, `files`, `user_id`, `user`, `created_at`, `updated_at`, etc.

#### save_skill - Create or Update a Skill

The backend has upsert semantics.

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

**Parameters**:
- `skill_id` (str): Skill ID
- `title` (str): title
- `description` (str): description
- `files` (list): file list, each item containing `path` and `content`

**Returns**: dict, the saved skill info

#### delete_skill - Delete a Skill

```python
import magnus

magnus.delete_skill("my-old-skill")
```

**Parameters**:
- `skill_id` (str): Skill ID

**Returns**: `None` (HTTP 204 No Content)

### Image

Manages the container image cache on the cluster. Pull and refresh operations are asynchronous: the API returns 202 immediately, and the actual pull runs in the background.

#### list_images - List Cached Images

```python
import magnus

images = magnus.list_images(search="pytorch")

for img in images["items"]:
    print(f"[{img['status']}] {img['uri']} ({img['size_bytes']} bytes)")
```

**Parameters**:
- `search` (str, optional): search by URI

**Returns**: `{"total": int, "items": list}`

Image statuses: `cached` (already cached), `pulling` (pulling in progress), `refreshing` (refresh in progress), `missing` (DB record exists but file is missing), `unregistered` (file exists but no DB record)

#### pull_image - Pull a New Image

Asynchronous operation; the API returns 202 and then the pull runs in the background. Use `list_images` to check progress.

```python
import magnus

result = magnus.pull_image("docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
print(f"Image ID: {result['id']}, Status: {result['status']}")
```

**Parameters**:
- `uri` (str): container image URI

**Returns**: dict containing `id`, `uri`, `status`, etc.

#### refresh_image - Refresh an Already-Cached Image

Asynchronous safe refresh: pulls the new image to a temporary file, and atomically replaces upon completion; the old image remains usable during the refresh.

```python
import magnus

result = magnus.refresh_image(3)
```

**Parameters**:
- `image_id` (int): Image ID

**Returns**: dict containing `id`, `uri`, `status`, etc.

#### remove_image - Remove a Cached Image

```python
import magnus

magnus.remove_image(3)
```

**Parameters**:
- `image_id` (int): Image ID

**Returns**: `None` (HTTP 204 No Content)

### Async API

All synchronous APIs have an async version with an `_async` suffix:

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

Concurrently submitting multiple jobs:

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

All synchronous APIs have a corresponding `_async` async version.

### API Reference

| Function | Description | Returns |
|----------|-------------|---------|
| `submit_job(task_name, entry_command, repo_name, ...)` | Submit a job, return immediately | Job ID |
| `execute_job(task_name, entry_command, repo_name, ...)` | Submit and wait for completion | `Optional[str]` |
| `list_jobs(limit, search)` | List jobs | `{total, items}` |
| `get_job(job_id)` | Get job details | Job info |
| `get_job_result(job_id)` | Get job result | `Optional[str]` |
| `get_job_action(job_id)` | Get job action | `Optional[str]` |
| `get_job_logs(job_id, page)` | Get job logs | `{logs, page, total_pages}` |
| `terminate_job(job_id)` | Terminate a job | Status info |
| `launch_blueprint(id, args, ...)` | Submit a blueprint job, return immediately | Job ID |
| `run_blueprint(id, args, timeout, ...)` | Submit a blueprint and wait for completion | `Optional[str]` |
| `list_blueprints(limit, search)` | List blueprints | `{total, items}` |
| `get_blueprint(id)` | Get blueprint details (including code) | Blueprint info |
| `get_blueprint_schema(id)` | Get blueprint parameter schema | Parameter list |
| `save_blueprint(id, title, description, code)` | Create or update a blueprint (upsert) | Blueprint info |
| `delete_blueprint(id)` | Delete a blueprint | `None` |
| `call_service(id, payload, timeout)` | Invoke an elastic service | Service response |
| `list_services(limit, search, active_only)` | List services | `{total, items}` |
| `get_cluster_stats()` | Get cluster status | Cluster info |
| `download_file(secret, target_path)` | Receive files | Path |
| `custody_file(path, expire_minutes, max_downloads)` | Put a file into custody, return secret | file_secret |
| `list_skills(limit, search)` | List skills | `{total, items}` |
| `get_skill(id)` | Get skill details (including files) | Skill info |
| `save_skill(id, title, description, files)` | Create or update a skill (upsert) | Skill info |
| `delete_skill(id)` | Delete a skill | `None` |
| `list_images(search)` | List cached images | `{total, items}` |
| `pull_image(uri)` | Pull a new image (async 202) | Image info |
| `refresh_image(image_id)` | Refresh a cached image (async 202) | Image info |
| `remove_image(image_id)` | Remove a cached image | `None` |
| `configure(token, address)` | Configure the SDK | None |

---

## Command Line Tool (CLI)

### Command Structure

The CLI has a two-layer command structure:

**Top-level shortcut commands** — shorthand for high-frequency operations, kept permanently:

```bash
magnus submit ...        # Submit a job (Fire & Forget)
magnus execute ...       # Submit a job and wait for completion
magnus jobs              # List jobs
magnus status <ref>      # View job details
magnus logs <ref>        # View job logs
magnus kill <ref>        # Terminate a job
magnus launch <id>       # Submit a blueprint (Fire & Forget)
magnus run <id>          # Submit a blueprint and wait for completion
magnus list              # List blueprints
```

**Object-verb subcommands** — full operations, similar to `git remote add`:

```bash
magnus job submit ...             # = magnus submit
magnus job execute ...            # = magnus execute
magnus job list                   # = magnus jobs
magnus job status <ref>           # = magnus status
magnus job logs <ref>             # = magnus logs
magnus job result <ref>           # View job result
magnus job action <ref>           # View job action
magnus job kill <ref>             # = magnus kill

magnus blueprint list             # = magnus list
magnus blueprint get <id>         # View blueprint details (including code)
magnus blueprint get <id> -o bp.yaml  # Export as a YAML blueprint file
magnus blueprint get <id> -c bp.py  # Export code to a file
magnus blueprint schema <id>      # View parameter Schema
magnus blueprint save <id> ...    # Create/update a blueprint
magnus blueprint delete <id>      # Delete a blueprint
magnus blueprint launch <id>      # = magnus launch
magnus blueprint run <id>         # = magnus run
```

The top-level shortcut commands and object-verb subcommands are fully equivalent. `job result/action` and `blueprint get/save/delete/schema` are only provided under the object-verb structure.

Unchanging top-level commands: `config`, `login`, `logout`, `call`, `cluster`, `services`, `skills`, `refresh`, `send`, `receive`, `custody`, `connect`, `disconnect`

### Global Options

```bash
magnus --help    # or magnus -h
magnus --version # or magnus -v
```

### magnus config

View the current configuration (address and token). The token is auto-masked, showing only the first and last 4 characters.

```bash
magnus config
```

```
  MAGNUS_ADDRESS  http://your-magnus-host:8017
  MAGNUS_TOKEN    sk-a****************ZaB
```

### magnus login

Configures `MAGNUS_ADDRESS` and `MAGNUS_TOKEN`, verifies connectivity, and saves to `~/.magnus/config.json`.

```bash
magnus login                                              # interactive
magnus login prod                                         # switch to an existing site
magnus login prod -a http://host:8017 -t sk-xxx           # non-interactive (suitable for scripts/agents)
magnus login default                                      # switch to the hardcoded default site
```

**Options** (non-interactive mode requires site + both options):
- `-a, --address`: server address
- `-t, --token`: Trust Token

Both interactive and non-interactive modes verify connectivity (`GET /api/auth/my-token`); verification failure produces a warning but does not block saving. After saving, it takes effect immediately in all terminals.

### magnus job

Job operation subcommand group.

```bash
magnus job --help
```

#### magnus job submit

Submit a job (Fire & Forget). The breakwater `--` is not required; arguments are routed automatically by name: `task-name`, `repo-name`, `gpu-type`, etc. belong to the Job layer; `timeout`, `verbose`, etc. belong to the CLI layer.

```bash
magnus job submit --task-name "Train" --repo-name my_repo --branch main \
  --commit-sha HEAD --entry-command "python train.py" --gpu-type A100 --gpu-count 4
```

#### magnus job execute

Submit a job and wait for completion. During polling, transient network errors or 5xx responses are automatically retried (exponential backoff, up to 30 consecutive failures). Argument routing rules are the same as `submit`.

```bash
magnus job execute --task-name "Quick Test" --repo-name my_repo --branch main \
  --commit-sha HEAD --entry-command "echo hello"
```

#### magnus job list

List jobs.

```bash
magnus job list                          # latest 10
magnus job list -l 20                    # latest 20
magnus job list -n "quadre"              # search by name
magnus job list --format yaml            # YAML output
magnus job list | head -20               # auto switches to YAML when piped
```

**Options**:
- `-l, --limit`: number, default 10
- `-n, --name, -s, --search`: search
- `-f, --format`: output format (table/yaml/json), default table

Supports negative indices: in the list, `-1` is the latest job, `-2` is the second latest, and so on.

#### magnus job status

View job details. Supports negative indices.

```bash
magnus job status abc123         # by Job ID
magnus job status -1             # latest job
magnus job status -2             # second latest job
```

#### magnus job logs

View job logs.

```bash
magnus job logs -1               # latest page of the latest job
magnus job logs -1 --page 0      # first page
magnus job logs abc123
```

**Options**:
- `-p, --page`: page number, -1 means the latest page, default -1

#### magnus job result

View job result (content of `MAGNUS_RESULT`). JSON is auto-formatted.

```bash
magnus job result -1
magnus job result abc123
```

#### magnus job action

View the job action script (content of `MAGNUS_ACTION`).

```bash
magnus job action -1             # view
magnus job action -1 -e          # view and execute
magnus job action abc123
```

**Options**:
- `-e, --execute`: execute the action

#### magnus job kill

Terminate a job.

```bash
magnus job kill abc123           # requires confirmation
magnus job kill -1               # latest job
magnus job kill -1 -f            # skip confirmation
```

**Options**:
- `-f, --force`: skip confirmation

### magnus blueprint

Blueprint operation subcommand group.

```bash
magnus blueprint --help
```

#### magnus blueprint list

List blueprints.

```bash
magnus blueprint list                    # list
magnus blueprint list -l 20             # 20
magnus blueprint list -s "sim"           # search
magnus blueprint list -f yaml            # YAML output
```

**Options**:
- `-l, --limit`: number, default 10
- `-s, --search`: search
- `-f, --format`: output format (table/yaml/json), default table

#### magnus blueprint get

View blueprint details, including full code.

```bash
magnus blueprint get <blueprint-id>
magnus blueprint get my-blueprint -f yaml
magnus blueprint get my-blueprint -o bp.yaml    # export as YAML blueprint file
magnus blueprint get my-blueprint -c bp.py      # export code to file
```

**Options**:
- `-f, --format`: output format (yaml/json), default human-readable
- `-o, --output`: export as YAML blueprint file (including title/description/code)
- `-c, --code-file`: export code to the specified .py file

`--output` is symmetric with `save --file`, and `--code-file` is symmetric with `save --code-file`, forming a complete loop:

```bash
# YAML flow
magnus blueprint get my-bp -o bp.yaml    # export
$EDITOR bp.yaml                           # edit
magnus blueprint save my-bp --file bp.yaml  # upload

# .py flow
magnus blueprint get my-bp -c bp.py      # export code
$EDITOR bp.py                             # edit
magnus blueprint save my-bp -t "My BP" -c bp.py  # upload
```

#### magnus blueprint schema

View the blueprint parameter Schema. Defaults to JSON output, including the type, constraints, and list of allowed values for each parameter.

```bash
magnus blueprint schema <blueprint-id>
magnus blueprint schema my-blueprint -f yaml
```

**Options**:
- `-f, --format`: output format (yaml/json), default json

#### magnus blueprint save

Create or update a blueprint (upsert semantics). Supports two modes:

```bash
# Mode 1: YAML blueprint file (recommended)
magnus blueprint save my-bp --file blueprint.yaml
magnus blueprint save my-bp --file bp.yaml -t "Override Title"

# Mode 2: Python code file
magnus blueprint save <id> --title "Title" --code-file blueprint.py
magnus blueprint save my-bp -t "My BP" -d "Description" -c ./src/bp.py
```

**Parameters**:
- `<id>`: Blueprint ID

**Options**:
- `--file`: YAML blueprint file path (contains title/description/code, mutually exclusive with `--code-file`)
- `-t, --title`: title (optional in YAML mode, overrides YAML value; required in code-file mode)
- `-d, --description, --desc`: description, default empty
- `-c, --code-file`: Python source file path (mutually exclusive with `--file`)

YAML blueprint file format:

```yaml
title: My Blueprint
description: Blueprint description
code: |
  from magnus import submit_job, JobType
  from typing import Annotated

  Param = Annotated[str, {"description": "Parameter description"}]

  def blueprint(param: Param):
      submit_job(...)
```

`import` statements in the code are automatically removed on upload.

#### magnus blueprint delete

Delete a blueprint.

```bash
magnus blueprint delete <id>             # interactive confirmation
magnus blueprint delete my-blueprint -f  # skip confirmation
```

**Options**:
- `-f, --force`: skip confirmation

#### magnus blueprint launch

Submit a blueprint job; returns the Job ID immediately.

```bash
magnus blueprint launch <blueprint-id> [OPTIONS] [-- ARGS...]

magnus blueprint launch quadre-simulation
magnus blueprint launch quadre-simulation --Te 2.0 --B 1.5
magnus blueprint launch my-blueprint -- --param value --flag
magnus blueprint launch my-blueprint --expire-minutes 120 -- --data /path/to/file

# List[FileSecret]: repeated flags are collected into a list
magnus blueprint launch batch-process -- --files a.csv --files b.csv
```

The breakwater `--` divides arguments into two sides: the left side is CLI control arguments (type-coerced), the right side is blueprint business arguments (**kept as raw strings**, with type conversion handled by the backend). Without `--`, all arguments belong to the blueprint, and CLI control arguments use defaults.

**Options** (left side of `--`):
- `--expire-minutes`: FileSecret expiration time (minutes), default 60
- `--max-downloads`: FileSecret max downloads, default 1
- `--preference`: merge preference parameters, default false
- `--timeout`: HTTP timeout (seconds), default 10
- `--verbose`: print argument partition details, default false

#### magnus blueprint run

Submit a blueprint job and wait for completion. During polling, transient network errors or 5xx responses are automatically retried (exponential backoff, up to 30 consecutive failures). When the job writes `MAGNUS_ACTION`, it is by default executed on the client.

```bash
magnus blueprint run <blueprint-id> [OPTIONS] [-- ARGS...]

magnus blueprint run my-blueprint --timeout 300 -- --param value
magnus blueprint run long-task --timeout 3600 --poll-interval 30

# Blueprint writes MAGNUS_ACTION to achieve auto-download
magnus blueprint run scan-pdf-to-vector --file original.pdf --output processed.pdf

# Do not execute action
magnus blueprint run my-blueprint --execute-action false -- --param value
```

Breakwater rules are the same as `launch`.

**Options** (left side of `--`):
- `--timeout`: timeout (seconds), default wait indefinitely
- `--poll-interval`: polling interval (seconds), default 2
- `--execute-action`: execute action, default true
- `--expire-minutes`: FileSecret expiration time (minutes), default 60
- `--max-downloads`: FileSecret max downloads, default 1
- `--preference`: merge preference parameters, default false
- `--verbose`: print argument partition details, default false

### magnus call

Invoke an elastic service. Supports an optional breakwater `--`: with `--`, the left side belongs to the CLI and the right side to the payload; without `--`, arguments are auto-routed by reserved keywords — `timeout`, `verbose`, `execute-action` belong to the CLI, and the rest to the payload.

```bash
magnus call <service-id> [OPTIONS] [ARGS...]

# Pass arguments directly (timeout auto-recognized as a CLI argument)
magnus call llm-inference --prompt "Hello!" --max_tokens 100 --timeout 120

# Explicit breakwater, same effect
magnus call slow-service --timeout 120 -- --param value

# From JSON file
magnus call my-service @payload.json

# From stdin
echo '{"x": 1, "y": 2}' | magnus call my-service -
cat input.json | magnus call my-service -
```

**Argument formats**:
- `--key value`: pass directly
- `@file.json`: JSON file
- `-`: stdin

**Options** (CLI reserved keywords):
- `--timeout, -t`: timeout (seconds), default 60
- `--verbose`: print argument partition details
- `--execute-action`: execute action

### magnus cluster

View cluster resource status.

```bash
magnus cluster
magnus cluster --format yaml
```

**Options**:
- `-f, --format`: output format (table/yaml/json), default table

### magnus services

List hosted services.

```bash
magnus services              # all
magnus services -a           # active only
magnus services -s "llm"     # search
magnus services -f yaml
```

**Options**:
- `-l, --limit`: number, default 10
- `-s, --search`: search
- `-a, --active`: active only
- `-f, --format`: output format (table/yaml/json), default table

### magnus skill

Skill operation subcommand group.

#### magnus skill list

List skills.

```bash
magnus skill list                    # list
magnus skill list -l 20              # 20
magnus skill list -s "pytorch"       # search
magnus skill list -f yaml            # YAML output
```

**Options**:
- `-l, --limit`: number, default 10
- `-s, --search`: search
- `-f, --format`: output format (table/yaml/json), default table

#### magnus skill get

View skill details, including the file list.

```bash
magnus skill get <skill-id>
magnus skill get my-skill -f yaml
```

**Options**:
- `-f, --format`: output format (yaml/json), default human-readable
- `-o, --output DIR`: export all files to a local directory, convenient for editing and uploading back with `skill save`

#### magnus skill save

Create or update a skill (upsert). Reads files from a local directory and uploads them.

```bash
magnus skill save my-skill --title "My Skill" --description "..." ./my_skill/
magnus skill save my-skill -t "Updated" -d "New desc" ./my_skill/
```

**Parameters**:
- `skill_id`: Skill ID
- `source`: source directory path

**Options**:
- `-t, --title`: skill title (required on first creation)
- `-d, --description`: skill description (required on first creation)

#### magnus skill delete

Delete a skill. Requires confirmation, or use `-f` to skip.

```bash
magnus skill delete my-skill
magnus skill delete my-skill -f
```

### magnus image

Image cache operation subcommand group. When you push a new version to an existing tag, use `refresh` to update the local cache; use `pull` to add a new image.

#### magnus image list

List cached images. Merges DB records with filesystem scans and annotates image statuses.

```bash
magnus image list
magnus image list -s "pytorch"
magnus image list -f yaml
```

**Options**:
- `-s, --search`: search by URI
- `-f, --format`: output format (table/yaml/json), default table

#### magnus image pull

Pull a new image. Asynchronous operation: the API returns 202 and the pull runs in the background; use `magnus image list` to check progress. All logged-in users can pull new images; only the owner or an admin can re-pull an existing image.

```bash
magnus image pull docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
magnus image pull docker://nvcr.io/nvidia/pytorch:24.01-py3
```

#### magnus image refresh

Re-pull an already-cached image. Safe refresh: pulls the new image to a `.tmp` file, and atomically replaces upon completion; the old image remains usable during the refresh.

```bash
magnus image refresh 3
```

#### magnus image remove

Remove a cached image (DB record + SIF file).

```bash
magnus image remove 3           # requires confirmation
magnus image remove 3 -f        # skip confirmation
```

**Options**:
- `-f, --force`: skip confirmation

### magnus refresh

Top-level shortcut, equivalent to `magnus image refresh`.

```bash
magnus refresh 3
```

### magnus skills

Top-level shortcut, equivalent to `magnus skill list`.

```bash
magnus skills
```

### magnus send

Upload a file or folder, returning a file secret.

```bash
magnus send data.csv
magnus send ./my_folder
magnus send data.csv --max-downloads 3
```

**Options**:
- `-t, --expire-minutes`: expiration time (minutes), default 60
- `-d, --max-downloads`: max downloads, default 1

### magnus receive

Download a file or folder.

```bash
magnus receive 7919-calm-boat-fire
magnus receive 7919-calm-boat-fire -o my_data.csv
magnus receive 7919-calm-boat-fire --output ./downloads/result.tar.gz
```

**Options**:
- `-o, --output`: target path (can rename); if not specified, saved to the current directory

### magnus custody

Upload a file to the backend for custody, returning a file secret.

```bash
magnus custody results.csv
magnus custody ./output_dir --expire-minutes 120
```

**Options**:
- `-t, --expire-minutes`: expiration time (minutes), default 60
- `-d, --max-downloads`: max downloads, default unlimited

### magnus connect

Connect to a running Magnus Debug session.

```bash
magnus connect           # auto-detects the latest debug job
magnus connect 12345     # specify a SLURM Job ID
```

- When already inside a Magnus session (`SLURM_JOB_ID` is set), prompts to exit
- When no Job ID is specified, auto-detects the current user's "Magnus Debug" jobs
- When there are multiple jobs, connects to the latest one and prompts about other available jobs

### magnus disconnect

Disconnect the current Magnus Debug session.

```bash
magnus disconnect
```

Valid only inside a Magnus session; sends `SIGHUP` to the parent process to terminate the srun session.

---

## Appendix

### Output Formats

List-type commands support three output formats:

| Format | Use Case |
|--------|----------|
| `table` | Interactive terminal (default) |
| `yaml` | Script processing, pipes |
| `json` | Programmatic parsing |

`blueprint schema` defaults to JSON (structured data, suitable for programmatic reading). Other list commands default to table in the terminal, and auto-switch to YAML when piped/redirected.

```bash
magnus job list                          # table
magnus job list | grep Running           # auto YAML
magnus job list --format json | jq '.'   # force JSON
magnus blueprint schema my-bp            # JSON
magnus blueprint schema my-bp -f yaml    # force YAML
```

### Error Codes

| Code | Description |
|------|-------------|
| `AUTH_REQUIRED` | Authentication required; check MAGNUS_TOKEN |
| `TOKEN_EXPIRED` | Token has expired |
| `BLUEPRINT_NOT_FOUND` | Blueprint does not exist |
| `SERVICE_NOT_FOUND` | Service does not exist |
| `JOB_NOT_FOUND` | Job does not exist |
| `VALIDATION_ERROR` | Parameter validation failed |
| `SERVICE_UNAVAILABLE` | Service unavailable |
| `TIMEOUT` | Request timed out |

### FAQ

**Q: How do I obtain MAGNUS_TOKEN?**

After logging into the Web UI, click on the user avatar in the top-right corner to see the Trust Token (starts with `sk-`). The Trust Token is used for SDK/CLI authentication, independent of the JWT used by the Web session.

**Q: Which has priority, environment variables or configure()?**

`magnus.configure()` has the highest priority and overrides environment variables and the config file.

**Q: Which commands support negative indices?**

`magnus job status`, `logs`, `result`, `action`, `kill` all support them. `-1` is the latest job, `-2` is the second latest.

**Q: Does the service continue running after call_service times out?**

Yes. `timeout` only controls the client-side wait time; the server-side request continues executing.

**Q: Why is there no Python SDK version of connect/disconnect?**

`connect` and `disconnect` establish an interactive shell connection via `srun`; they are terminal-level operations and cannot be replaced by function calls.

**Q: What is a Preference?**

The cached parameters from the user's last run of a blueprint. When `use_preference=True`, cached parameters are merged (explicitly passed ones take priority). When `save_preference=True`, the current parameters are saved after a successful run. SDK/CLI defaults to not merging (`use_preference=False`); the Web UI defaults to merging. `FileSecret` types are also cached, but secrets have TTL.

**Q: What happens if a blocking command (`run` / `execute`) loses network connection?**

During polling, transient network errors (DNS, connection reset, etc.) or 5xx responses automatically trigger exponential backoff retries (2s → 4s → 8s → ... → 30s cap), tolerating up to 30 consecutive failures. Beyond that, it errors out and suggests checking with `magnus job status <id>`. The job itself is unaffected by the client disconnect.

**Q: What is the difference between `--format yaml` and auto-switching when piped?**

Auto-switch when piped: YAML is used automatically when the output is not a terminal. `--format yaml`: forces YAML, even in a terminal.
