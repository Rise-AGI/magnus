> **Language / 语言**: **English** · [简体中文](job-runtime.zh-CN.md)

# Magnus Job Runtime

This document describes the full runtime path of a Magnus job from submission to in-container execution, as well as the filesystem protocol and environment variable protocol between the host and the container.

## Execution path overview

```
User submission (POST /api/jobs/submit)
  │
  ▼
PREPARING ─── Async resource preparation (parallel)
  │             ├── ensure_image: docker:// → .sif (LRU cache, 80G)
  │             └── ensure_repo:  git clone → copy → checkout → setfacl
  ▼
PENDING ───── Head-of-queue scheduling
  │             ├── Priority sort: A1(4) > A2(3) > B1(2) > B2(1), same-level FIFO
  │             ├── A-class can preempt RUNNING B-class (B2 first, LIFO)
  │             └── Only one QUEUED job is allowed in the SLURM queue
  ▼
QUEUED ────── SLURM sbatch submitted
  │             └── sbatch script: python3 {workspace}/jobs/{id}/wrapper.py
  ▼
RUNNING ───── wrapper.py starts executing
  │             ├── Phase 1: GPU spy daemon thread (nvidia-smi polling)
  │             ├── Phase 2: write .magnus_user_script.sh
  │             ├── Phase 3: shell bootstrap layer → apptainer exec → user script
  │             └── Phase 4: epilogue, write .magnus_success
  ▼
SUCCESS / FAILED
```

## wrapper.py: three-layer structure

`_build_wrapper_content()` in `_scheduler.py` generates `wrapper.py`, which is the actual entry point executed by SLURM. The wrapper contains three nested layers:

```
wrapper.py (Python, run directly by SLURM)
  ├── GPU spy thread        ← Python threading
  ├── .magnus_user_script.sh write
  └── shell_cmd (Bash)      ← subprocess.call(shell=True)
        ├── Env var injection (APPTAINERENV_*)
        ├── system_entry_command execution
        ├── overlay creation
        └── apptainer exec   ← container entry
              └── .magnus_user_script.sh  ← user code
```

### Phase 1: user script

Write `job.entry_command` into `.magnus_user_script.sh`, prefixed with `set -e`.

### Phase 2: shell bootstrap layer

This is the most complex part, executed in order:

1. **Inject container environment variables** — via the `APPTAINERENV_` prefix (apptainer automatically strips the prefix when injecting into the container)
2. **Execute `system_entry_command`** — a per-job configurable shell script on the host side
3. **Fallback `MAGNUS_HOME`** — `export MAGNUS_HOME=${MAGNUS_HOME:-/magnus}`; all subsequent paths reference `$MAGNUS_HOME`
4. **Set apptainer runtime directories** — `APPTAINER_TMPDIR`, `APPTAINER_CACHEDIR`
5. **Append bind mount** — mount workspace to `$MAGNUS_HOME/workspace`
6. **Proxy pass-through** — in bridge mode, replace `127.0.0.1`/`localhost` with `$MAGNUS_HOST_GATEWAY`
7. **Detect setuid apptainer** — `[ -u "$(command -v apptainer)" ]`, zero I/O; the result decides subsequent branches
8. **Determine isolation level** — rootless default `containall`, setuid default `contain` (to avoid userns conflict), `MAGNUS_CONTAIN_LEVEL=none` falls back to bare run
9. **Create overlay or downgrade** — with isolation + rootless, create a sparse overlay (`--sparse` completes instantly, apptainer ≥ 1.3; older versions automatically fall back to dense creation and warn); with isolation + setuid or `MAGNUS_NO_OVERLAY=1`, downgrade to `--writable-tmpfs`; with no isolation (none), bare run
10. **Inject HOME** — `--env HOME=$MAGNUS_HOME` (`APPTAINERENV_HOME` is forbidden by apptainer; use `--env` to bypass)
11. **Execute apptainer** — host mode execs directly; bridge mode goes through `rootlesskit`

### Phase 3: Epilogue

When apptainer returns 0, write the `.magnus_success` marker. The finally block cleans up overlay files.

## Filesystem protocol

### Host side

All paths are based on `{magnus_root}/workspace/jobs/{job_id}/` (abbreviated below as `{work}/`):

| Path | Lifecycle | Writer | Reader | Description |
|------|----------|--------|--------|------|
| `{work}/repository/` | prepare → cleanup | resource_manager | container (bind) | git checkout, the working directory inside the container |
| `{work}/wrapper.py` | submit → cleanup | scheduler | SLURM | generated execution entry point |
| `{work}/slurm/output.txt` | submit → permanent | SLURM | API (logs) | sbatch --output points here |
| `{work}/.magnus_user_script.sh` | wrapper exec → cleanup | wrapper.py | container (bind) | user entry script |
| `{work}/.magnus_success` | epilogue → sync_reality | wrapper.py | scheduler | success marker; its existence means SUCCESS |
| `{work}/.magnus_result` | user writes inside container → API reads | user code | routers/jobs.py | task result content |
| `{work}/.magnus_action` | user writes inside container → API reads | user code | routers/jobs.py + SDK | client action instruction |
| `{work}/ephemeral_overlay.img` | Phase 2 → finally | wrapper shell | apptainer | writable layer, deleted after job ends |
| `{work}/.magnus_tmp/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_TMPDIR |
| `{work}/.magnus_cache/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_CACHEDIR |
| `{work}/metrics/` | submit → permanent | wrapper sidecar + user code | routers/metrics.py | Magnus Metrics Protocol v1 JSONL metrics files |

**cleanup** refers to `_clean_up_working_table()`, called when the job ends (SUCCESS/FAILED/TERMINATED/PAUSED). `slurm/output.txt` is not cleaned.

### Container side

```
${MAGNUS_HOME}/                              default /magnus
${MAGNUS_HOME}/workspace/                    bind mount ← {work}/
${MAGNUS_HOME}/workspace/repository/         git checkout, also --pwd
${MAGNUS_HOME}/workspace/.magnus_user_script.sh
${MAGNUS_HOME}/workspace/.magnus_result      $MAGNUS_RESULT
${MAGNUS_HOME}/workspace/.magnus_action      $MAGNUS_ACTION
${MAGNUS_HOME}/workspace/metrics/            $MAGNUS_METRICS_DIR (Metrics Protocol v1)
${MAGNUS_HOME}/.tmp/                         SDK file relay directory (container writable layer, auto-created)
```

`MAGNUS_ACTION` is just a plain text file; the backend does not automatically execute it at runtime. The current behavior of each client is:

- SDK / CLI: by default reads and executes `MAGNUS_ACTION`
- Web: does not execute arbitrary shell; only maps the whitelisted form `magnus receive <secret> [--output/-o <target>]` to a browser download

Therefore, in the Web scenario, the semantics of `--output/-o <target>` is not "write to a specified local path in the browser", but rather "suggested download name".

The container filesystem is a read-only squashfs (SIF). The writable layer depends on the isolation mode:

| Mode | Writable layer | Capacity limit | Description |
|------|--------|----------|------|
| containall + overlay | ephemeral overlay (sparse ext3 image) | `ephemeral_storage` | Default path (rootless apptainer); `--sparse` creation is instant and disk usage is on-demand |
| containall/contain + writable-tmpfs | RAM tmpfs | shared with `memory_demand` | setuid apptainer or `MAGNUS_NO_OVERLAY=1` |
| none (bare run) | host filesystem pass-through | unlimited | `MAGNUS_CONTAIN_LEVEL=none`, equivalent to pre-overlay behavior |

### SDK runtime detection protocol

The SDK's `file_transfer.get_tmp_base()` uses the following criteria to determine the file relay directory (temporary files for upload tar compression and download tar extraction):

1. The environment variable `MAGNUS_HOME` exists
2. `$MAGNUS_HOME/workspace/` is an existing directory (created and bind-mounted by the Magnus runtime)

When both conditions are met, the relay directory is `$MAGNUS_HOME/.tmp/` (container writable layer); otherwise it falls back to the system `/tmp`.

**Why not `/tmp`**: under both isolation modes, `/tmp` is not an ideal relay location:

| Mode | `/tmp` location | Problem |
|------|-------------|------|
| overlay | inside the overlay image | shares the `ephemeral_storage` quota with in-container writes like pip install |
| writable-tmpfs | RAM tmpfs | limited capacity (kernel default 50% cgroup RAM), large files trigger ENOSPC |

`$MAGNUS_HOME/.tmp/` is also located on the container writable layer (overlay or tmpfs) and shares the same write budget as `/tmp`, but avoids mingling with system temporary files. More importantly, it does **not** write into `$MAGNUS_HOME/workspace/` (a host-disk bind mount), thereby preserving container isolation — user code inside the container cannot escape to the host filesystem via relay files.

In nested container scenarios, the inner `$MAGNUS_HOME/workspace/` is also part of the bind-mount chain, and the detection protocol still works; the inner `$MAGNUS_HOME/.tmp/` is still located on the inner container's writable layer.

## Environment variable protocol

### Environment variables injected into the container

Injected via the `APPTAINERENV_` prefix mechanism; inside the container, the prefix is stripped and the variable can be read directly:

| Variable | Source | Description |
|------|------|------|
| `MAGNUS_TOKEN` | `job.user.token` | Current user's trust token; SDK recognizes it automatically |
| `MAGNUS_ADDRESS` | `{server.address}:{server.front_end_port}` | Magnus backend address |
| `MAGNUS_JOB_ID` | `job.id` | Current job ID |
| `MAGNUS_HOME` | `${MAGNUS_HOME:-/magnus}` | Container root; child Magnus may override |
| `MAGNUS_RESULT` | `$MAGNUS_HOME/workspace/.magnus_result` | Result file path |
| `MAGNUS_ACTION` | `$MAGNUS_HOME/workspace/.magnus_action` | Action file path |
| `HOME` | `$MAGNUS_HOME` (injected via `--env`) | HOME inside the container; user entry_command may override |
| `HTTP_PROXY` etc. | inherited from host | In bridge mode, localhost → gateway is substituted automatically |

### Shell bootstrap layer environment variable knobs

These variables are set by `system_entry_command` and control wrapper shell behavior:

| Variable | Default | Effect |
|------|--------|------|
| `MAGNUS_HOME` | `/magnus` | Container root path, affecting bind mount target and all internal paths. Falls back to default after system_entry_command; all subsequent references use `$MAGNUS_HOME` |
| `MAGNUS_NO_OVERLAY` | `0` | Set to `1` to skip the ephemeral overlay and downgrade to `--writable-tmpfs` (RAM) |
| `MAGNUS_CONTAIN_LEVEL` | `containall` (rootless) / `contain` (setuid) | apptainer isolation level; set to `none` to disable isolation entirely (bare run, host /tmp pass-through) |
| `MAGNUS_FAKEROOT` | `0` | Set to `1` to add `--fakeroot` |
| `MAGNUS_NET_MODE` | `host` | Set to `bridge` to enable rootlesskit network isolation |
| `MAGNUS_PORT_MAP` | (none) | Port mapping for rootlesskit in bridge mode |
| `MAGNUS_HOST_GATEWAY` | `10.0.2.2` | Proxy address substitution target in bridge mode |
| `MAGNUS_HOST_LOOPBACK` | `0` | Set to `1` to allow the container to access the host loopback |
| `APPTAINER_BIND` | (none) | Additional bind mounts; wrapper will append the workspace binding |

`system_entry_command` is per-job configurable; if not set, `cluster.default_system_entry_command` is used. It executes on the host side, outside the container.

## apptainer execution parameters

### setuid detection and adaptive decision tree

apptainer has two installation modes, with very different behaviors:

| | rootless (`-rwxr-xr-x`) | setuid (`-rwsr-xr-x`) |
|---|---|---|
| Detection | `[ -u apptainer ]` is false | `[ -u apptainer ]` is true |
| overlay creation | file owner is the calling user ✓ | file owner is root:0600 ✗ |
| `--containall` | normal (`--userns` available) | **errors** (setuid + userns conflict) |

Decision tree:

```
[ -u apptainer ]?
├── no (rootless)
│   └── MAGNUS_CONTAIN_LEVEL=none?
│       ├── yes → bare run --nv
│       └── no  → --containall + overlay
└── yes (setuid)
    └── MAGNUS_CONTAIN_LEVEL=none?
        ├── yes → bare run --nv
        └── no  → --contain + --writable-tmpfs (WARNING)
```

### Command template

```bash
# Isolated mode (default)
apptainer exec \
  --nv \                                  # GPU driver pass-through
  --${APPTAINER_CONTAIN} \               # containall or contain
  --no-mount tmp \                        # forbid the 64MB tmpfs on /tmp
  [--overlay ephemeral_overlay.img] \     # for rootless + overlay (sparse, on-demand disk usage)
  [--writable-tmpfs] \                    # for setuid or MAGNUS_NO_OVERLAY=1
  --env HOME=$MAGNUS_HOME \              # HOME inside the container
  [--fakeroot] \                          # when MAGNUS_FAKEROOT=1
  --pwd $MAGNUS_HOME/workspace/repository \
  {sif_path} \
  bash $MAGNUS_HOME/workspace/.magnus_user_script.sh

# Bare-run mode (MAGNUS_CONTAIN_LEVEL=none)
apptainer exec \
  --nv \
  --env HOME=$MAGNUS_HOME \
  --pwd $MAGNUS_HOME/workspace/repository \
  {sif_path} \
  bash $MAGNUS_HOME/workspace/.magnus_user_script.sh
```

In bridge mode, the entire apptainer command is wrapped by rootlesskit:
```bash
rootlesskit \
  --net=slirp4netns \
  --port-driver=builtin \
  --publish $MAGNUS_PORT_MAP \
  [--disable-host-loopback] \             # when MAGNUS_HOST_LOOPBACK!=1
  apptainer exec ...
```

## SLURM submission parameters

```bash
sbatch --parsable \
  --job-name={task_name} \
  --output={work}/slurm/output.txt \
  --gres=gpu:{gpu_type}:{gpu_count} \
  --mem={memory_demand} \
  --cpus-per-task={cpu_count} \
  # Script content: python3 {work}/wrapper.py
```

The environment variables `MAGNUS_RUNNER` and `MAGNUS_TOKEN` are passed via sbatch's process environment.

## Scheduler heartbeat and state sync

Heartbeat interval is `scheduler.heartbeat_interval` (default 2 seconds); each tick:

1. **`_sync_reality`**: iterate QUEUED/RUNNING jobs and check real state via `squeue`
   - SLURM reports RUNNING → DB marks RUNNING
   - SLURM reports COMPLETED + `.magnus_success` exists → SUCCESS, and check `.magnus_result` and `.magnus_action`
   - SLURM reports COMPLETED but no `.magnus_success` → FAILED
   - SLURM reports FAILED/CANCELLED/TIMEOUT → FAILED
2. **`_make_decisions`**: schedule PENDING/PAUSED jobs
3. **`_record_snapshot`**: record a cluster snapshot every `snapshot_interval` (default 300 seconds)

## Resource preparation

Executed in parallel in the PREPARING phase:

**Image pull** (`_resource_manager.ensure_image`):
- docker URI → SIF filename mapping (`docker://a/b:tag` → `a_b_tag.sif`)
- Cache directory `{magnus_root}/container_cache/`, LRU eviction, cap `resource_cache.container_cache_size`
- Per-image asyncio.Lock to prevent duplicate pulls
- 3 retries + exponential backoff; non-transient errors (unauthorized, not found) fail immediately

**Repo clone** (`_resource_manager.ensure_repo`):
- Cache directory `{magnus_root}/repo_cache/`, LRU eviction, cap `resource_cache.repo_cache_size`
- cache → copy to `{work}/repository/` → fetch + checkout to the specified commit SHA
- `setfacl` sets runner user permissions (required when executing inside the container as the runner)

## Child Magnus (nested containers)

Child Magnus is the scenario of running a full Magnus + SLURM stack inside a container.

### Known low-level pitfalls

**SLURM `PartitionName=default` is a reserved word**: SLURM interprets `default` (case-insensitive) as the partition default template, not an actual partition name. The child SLURM cluster uses `PartitionName=batch`.

**Bind mount paths inside the container must not conflict with the parent Magnus**: the parent Magnus has already bind-mounted `/magnus`; the child apptainer binding the same path will conflict. Solution: in the child Magnus's `system_entry_command`, `export MAGNUS_HOME=/submagnus`, and all internal paths follow automatically.

### Typical system_entry_command for child Magnus

```bash
# Extra bind mounts
mounts=(
  "/dev/fuse:/dev/fuse"           # child apptainer requires the fuse device
)
export APPTAINER_BIND=$(IFS=,; echo "${mounts[*]}")

# Path isolation
export MAGNUS_HOME=/submagnus     # cannot be called /magnus; occupied by the parent container

# Downgrade isolation
export MAGNUS_CONTAIN_LEVEL=contain  # containall is too strict in nested scenarios
export MAGNUS_NO_OVERLAY=1           # fuse-overlayfs does not support nesting

# Network
export MAGNUS_HOST_LOOPBACK=1     # allow access to the host proxy

# Permissions
# Pair with server.scheduler.allow_root=true
```

### Child SLURM bootstrap

`scripts/setup_single_node_slurm.sh` bootstraps a single-node SLURM cluster inside the container:
- Cluster name `magnus-child`, partition name `batch`
- Start munge → slurmctld → slurmd
- Verify the cluster is ready via `sinfo`

### Known limitations of nested containers

**Ephemeral overlay (fuse-overlayfs) does not work in nested containers**. The first-layer apptainer already uses squashfuse (SIF mount) + fuse-overlayfs (writable layer); the second layer stacking another fuse-overlayfs forms FUSE-on-FUSE, and the Linux kernel's mount namespace isolation prevents the inner FUSE process from unmounting correctly — mount state is inconsistent across namespaces. This is not an apptainer bug, but rather the Linux kernel's lack of support for unlimited nested isolation (`CAP_SYS_ADMIN` is stripped at the first layer, FUSE is a compromise for the no-capabilities case, and mount propagation of nested FUSE across namespaces goes wrong). Currently bypassed via `MAGNUS_NO_OVERLAY=1`.

Other nesting pitfalls already encountered:

| Problem | Root cause | Solution |
|------|------|------|
| `/dev/fuse` unavailable | `--containall` isolates devices | bind mount `/dev/fuse:/dev/fuse` |
| Proxy `10.0.2.2` unreachable | rootlesskit `--disable-host-loopback` | `MAGNUS_HOST_LOOPBACK=1` |
| `setfacl` not present | container image lacks the `acl` package | resource_manager downgrades to warning |
| root user denied | wrapper.py hard-codes forbidding root | `server.scheduler.allow_root=true` |
| git clone SSH fails | no SSH client inside the container | resource_manager HTTPS fallback |
| SLURM `PartitionName=default` | SLURM reserved word | change to `PartitionName=batch` |

## Configuration reference

### Job-runtime-related configuration in `magnus_config.yaml`

```yaml
server:
  root: /home/magnus/magnus-data            # root for all paths
  scheduler:
    heartbeat_interval: 2                   # heartbeat interval (seconds)
    snapshot_interval: 300                  # cluster snapshot interval (seconds)
    allow_root: false                       # whether to allow root runner
  resource_cache:
    container_cache_size: 80G               # SIF cache cap (LRU)
    repo_cache_size: 20G                    # git repo cache cap (LRU)

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

### Source file index

| File | Responsibility |
|------|------|
| `back_end/server/_scheduler.py` | Scheduler core: heartbeat, state sync, wrapper generation, SLURM submission |
| `back_end/server/_slurm_manager.py` | SLURM CLI wrapper (sbatch/squeue/scancel/sinfo) |
| `back_end/server/_resource_manager.py` | Image pull + repo clone, with LRU cache |
| `back_end/server/routers/jobs.py` | Job CRUD API, lazy reading of .magnus_result/.magnus_action |
| `back_end/server/models.py` | Job model (SQLAlchemy) |
| `configs/magnus_config.yaml` | Configuration source |
| `docker/magnus-runtime/Dockerfile` | Child Magnus runtime image |
| `scripts/setup_single_node_slurm.sh` | In-container SLURM bootstrap script |
