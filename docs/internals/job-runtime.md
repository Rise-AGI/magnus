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
PENDING ───── Resource admission
  │             ├── Priority sort: A1(4) > A2(3) > B1(2) > B2(1), same-level FIFO
  │             ├── Known single-node layout → reserve head; backfill only surplus
  │             ├── Preempt B jobs only with a complete actual-allocation plan
  │             └── Complex layout → priority head to SLURM; tenant → SLURM queue
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

SSH artifact recovery runs separately from status reconciliation and admission, with at most four transfers active and one worker per Job. Transfer failures retain the workspace and receipts and retry after 60 seconds. Markers and live logs use a shared 30-second budget per pass. `transport.ssh.transfer_timeout_seconds` (default 900, a positive integer) bounds each staging transfer and each final recovery pass, including SSH authentication. Increase it for large artifacts or slow links. Timeout stops the local transfer process tree without closing the shared SSH master; terminal state still requires a successful marker read.

Admission and submission also run as a single background pass, so a stalled upload cannot suspend status reconciliation or start overlapping resource decisions. Failures before submission persist a terminal result before cleanup; cleanup retries independently, including Jobs that never received a SLURM ID.

## wrapper.py: three-layer structure

`_build_wrapper_content()` in `_scheduler/_wrapper_template.py` generates `wrapper.py`, which is the actual entry point executed by SLURM. The wrapper contains three nested layers:

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

All paths are based on `{magnus_root}/workspace/jobs/{job_id}/` (abbreviated below as `{work}/`). The three ephemeral artifacts (overlay image + apptainer tmp/cache) instead live under `{magnus_ephemeral_root}/workspace/jobs/{job_id}/` (abbreviated `{ephemeral}/`); `ephemeral_root` defaults to `root`, so `{ephemeral}/` == `{work}/` unless a site sets `server.ephemeral_root` to put the high-frequency random-write overlay on a faster disk:

| Path | Lifecycle | Writer | Reader | Description |
|------|----------|--------|--------|------|
| `{work}/repository/` | prepare → cleanup | resource_manager | container (bind) | git checkout, the working directory inside the container |
| `{work}/wrapper.py` | submit → cleanup | scheduler | SLURM | generated execution entry point |
| `{work}/slurm/output.txt` | submit → retained (until workspace_size_cap eviction) | SLURM | API (logs) | sbatch --output points here |
| `{work}/.magnus_user_script.sh` | wrapper exec → cleanup | wrapper.py | container (bind) | user entry script |
| `{work}/.magnus_success` | epilogue → sync_reality | wrapper.py | scheduler | success marker; its existence means SUCCESS |
| `{work}/.magnus_oom` | epilogue (on non-zero ret) → sync_reality / cleanup | wrapper.py (cgroup memory.events probe) | scheduler | OOM marker; its existence means the kernel OOM-killed something inside the job's cgroup. Scheduler rewrites `job.result` to a memory-limit message instead of the generic FAILED string. Docker mode uses `docker inspect .State.OOMKilled` instead of this file. |
| `{work}/.magnus_result` | user writes inside container → API reads | user code | routers/jobs.py | task result content |
| `{work}/.magnus_action` | user writes inside container → API reads | user code | routers/jobs.py + SDK | client action instruction |
| `{ephemeral}/ephemeral_overlay.img` | Phase 2 → finally | wrapper shell | apptainer | writable layer, deleted after job ends |
| `{ephemeral}/.magnus_tmp/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_TMPDIR |
| `{ephemeral}/.magnus_cache/` | Phase 2 → cleanup | apptainer | apptainer | APPTAINER_CACHEDIR |
| `{work}/metrics/` | submit → retained (until workspace_size_cap eviction) | wrapper sidecar + user code | routers/metrics.py | Magnus Metrics Protocol v1 JSONL metrics files |

When `ephemeral_root` is split onto a separate disk, `{ephemeral}/` is created by the backend account but the job may run as a different OS user, so submission grants that dir the same runner + backend-account ACL the working table receives during repo preparation (shared `setfacl` helper) — the runner needs to create the overlay / apptainer tmp there, and the backend needs to reclaim them at cleanup.

**cleanup** refers to `_clean_up_working_table()`, called when the job ends (SUCCESS/FAILED/TERMINATED/PAUSED). It is a keep-whitelist: only `slurm/`, `metrics/`, `.magnus_result`, `.magnus_action` and `.magnus_custody_receipts.json` survive; everything else under the working table is removed — including anything the user wrote into the workspace bind mount (e.g. checkpoints or outputs dumped straight into `$MAGNUS_HOME/workspace` instead of going through `file_custody` / `.magnus_result`). The separate `{ephemeral}/` dir is dropped wholesale when `ephemeral_root` is split out.

The retained artifacts (`slurm/`, `metrics/`, markers) are additionally subject to a cross-job size cap: `server.scheduler.workspace_size_cap` bounds the total size of `workspace/jobs`, and a low-frequency scheduler janitor (`_reclaim_workspace_over_cap`) evicts whole terminal-job directories oldest-first by mtime once the terminal total exceeds it — active jobs are never touched, and only on-disk artifacts are removed (DB job records are kept).


### Custody during execution

The SSH tenant SDK writes a complete payload and `meta.json` into a hidden staging directory, then atomically renames it to `relay-<uuid>`. Only published entries are importable. The controller discovers them incrementally in background workers, including legacy metadata without `storage_filename` / `size_bytes`. Registered tokens are not fetched again: changing their remote metadata cannot update contents or download allowances. Publish a new token for a new snapshot.

Temporary custody payloads live alongside `_custody.sqlite3`, which records expiry, download counts and import identity. Publication and import identity commit in one transaction: even a final download followed by a crash before writing the Job receipt cannot cause reimport. The temporary import guard is retired only after that receipt is durable. Download claims commit before bytes are exposed; disk capacity stays charged until readers release their leases. Shutdown preserves complete entries, cancels unfinished receives and reaps subprocesses. The legacy permanent manifest remains unchanged.

Job outputs do not count down while execution is ongoing. TTL starts when the scheduler confirms the entire execution is terminal, not when it pauses or a cancellation is merely requested. A later import gets a full TTL from readiness; an expiry already started never moves. Waiting for all transfers would deadlock reclamation when custody capacity is full. Unassociated uploads still expire from publication. The optional HTTP `job_id` accepts only the authenticated owner's active Job; the current SDK supplies the Job identity when both site and credentials match.

Each SSH ControlMaster connection shares two discovery/small-file/log workers and one bulk/final-log worker (8 MiB payload threshold). Each Job/token has one receiver. Discovery and small files have short deadlines; bulk receives and complete final logs use the configured transfer budget. A bounded discovery cache rotates a cursor and retains small files preferentially, so a backlog of failing bulk transfers cannot stop discovery. Capacity is reserved before a bounded receive directly into custody, without a second complete staging copy. Unimported remote data and finalization state remain until transfer succeeds. Runtime import changes neither computation nor admission, and never scans arbitrary result directories that have not been published through custody.

## Signaling and Termination

Magnus distinguishes two external interventions on a running job:

| Action | Entry points | Backend impl | DB state change |
|--------|-------------|--------------|-----------------|
| terminate | `POST /api/jobs/{id}/terminate`, `magnus job kill` | SLURM: `scancel --signal=KILL --full` + bare `scancel`; Docker: `docker stop -t 0 && docker rm -f` | After acknowledgement: `Terminated`; SLURM cleanup waits for release and final artifact transfer |
| signal | `POST /api/jobs/{id}/signal`, `magnus job signal` (Running only) | SLURM: `scancel --signal=TERM --batch <jobid>`; Docker: `docker kill --signal=TERM` | No state change |

**Roles of the two actions**: terminate requests hard cancellation. Failure to acknowledge cancellation leaves the Job state and workspace intact. After acknowledgement Magnus marks the Job terminated, but counts resources from SLURM until release; it retrieves final artifacts before cleanup. A transfer failure retains the execution ID and workspace for retry without blocking other jobs' finalization. An execution association awaiting finalization is not evidence that physical resources are still allocated. Signal forwards SIGTERM without changing DB state. A user process with a handler can save checkpoints and release resources; without a handler it inherits `SIG_IGN` from the user-script shell. Use terminate for forced cancellation.

A handler that wants its partial state to count as success writes `$MAGNUS_RESULT` (mounted into the container at `$MAGNUS_HOME/workspace/.magnus_result`) and `sys.exit(0)` from inside the handler. magnus reconciles the job to `Success` even if the surrounding process tree was forced down by the signal — see "State convergence" below.

**SLURM path (signal)**: `scancel --signal=TERM --batch <jobid>` — `--batch` makes SLURM deliver SIGTERM only to the batch step's parent process (= wrapper.py after `exec wrapper.py` in the sbatch script), without touching the rest of the cgroup. apptainer's starter, fuse-overlayfs, squashfuse_ll and the other container infrastructure processes have no SIGTERM handler installed and would default-disposition-terminate if signaled, tearing the squashfs / overlay mount points down and SIGBUS-ing the user process — `--batch` keeps them intact. wrapper.py then fans out to the user processes itself, no user-script cooperation, no marker file, no protocol surface:

- **Startup window protection**: the sbatch batch script renders as `#!/bin/bash\ntrap '' TERM\n\nexec <wrapper>` so the outer bash sets SIG_IGN before any work and `exec` carries it into wrapper.py via POSIX exec inheritance. The tiny window between wrapper.py process start and `signal.signal(SIGTERM, _on_sigterm)` in `main()` is covered — a signal in that window is dropped rather than killing wrapper.py with SIG_DFL.
- **Wrapper-side fan-out via NSpid**: `_on_sigterm` first records `_signaled[0] = True` (so the Phase 3 marker fallback runs even if the forward fails), then calls `_signal_user_processes(SIGTERM)`. That helper reads this job's `cgroup.procs` (v1 freezer on hybrid systems, v2 unified otherwise), then for each host PID inspects `/proc/<host_pid>/status` — the `NSpid:` field lists the process's PID in every PID namespace it lives in. A single column means the process is only in the host namespace (wrapper itself, the subprocess shell, apptainer's starter, fuse-overlayfs, squashfuse_ll); two columns mean it's also in a child PID namespace, i.e. inside the user's container. The helper skips single-column PIDs (host-only infrastructure) and skips PIDs whose inner value is `1` (apptainer's `appinit` / container PID 1), and calls `kill(host_pid, SIGTERM)` on the rest. `kill(2)` with a host PID crosses PID namespaces directly because kernel PIDs are host-global identifiers — no `nsenter` or `setns` needed. Whatever the user's `entry_command` spawned — main, fork children, MPI workers, nested shells — has a child-namespace NSpid entry and receives the signal. This is the same mental model as `systemd KillMode=cgroup` and Kubernetes graceful termination. Fail-open at every layer: cgroup read failure, status parse failure, individual kill failure all continue silently; the handler never throws back to main.
- **User-script SIG_IGN guard**: the rendered `.magnus_user_script.sh` begins with `trap '' TERM`, so the bash that runs the user's `entry_command` inherits `SIG_IGN` for SIGTERM. The wrapper's fan-out also hits this bash via NSpid; SIG_IGN keeps it from being killed by default disposition, so `wrapper.subprocess.call` doesn't return prematurely and the SLURM step doesn't tear the user processes down while their handler is still running. `SIG_IGN` propagates to descendants through POSIX exec; user code that installs a handler via `signal.signal(SIGTERM, …)` / `sigaction(2)` naturally overrides it.
- **User process**: inside the container the user processes inherit SIG_IGN for SIGTERM from the user-script bash. Without a handler the inherited SIG_IGN means SIGTERM is silently ignored — pressing send-SIGTERM from the UI is a no-op for handler-less code (the standard Unix consequence of running without graceful shutdown wired in; use the terminate button if you want to force-kill, which goes through `kill_job`'s SIGKILL path). With a `signal.signal(SIGTERM, …)` handler (or `sigaction(2)` in C / `SIGNAL` in Fortran / any POSIX runtime's signal idiom) the user code overrides the inherited SIG_IGN, runs its own teardown, decides its own exit code; writing `.magnus_result` from inside the handler lets `sys.exit(0)` count as success (see "State convergence" below). The protocol delivers a plain SIGTERM, the user-side idiom is plain `sigaction(2)` semantics — nothing magnus-specific to learn.

**SLURM path (terminate / preemption)**: `kill_job` uses `scancel --signal=KILL --full` to deliver SIGKILL to all PIDs immediately (SIGKILL cannot be ignored at the kernel level), followed by a bare `scancel <jobid>` to flip the SLURM job state to CANCELLED. Falling back to the default `scancel <jobid>` (KillSignal=SIGTERM, KillWait grace before SIGKILL) would burn the full KillWait — wrapper.py's handler does not exit but forwards instead, and handler-aware user code spends the grace window doing its own graceful shutdown rather than yielding. SIGKILL-direct keeps terminate and preemption latencies in the seconds range.

**Docker path (signal)**: a two-layer relay symmetric to SLURM's per-process disposition:

1. `docker run --init` so docker's bundled tini becomes PID 1. tini reaps orphans and forwards SIGTERM to its direct child (the outer bash). tini does **not** broadcast to all container PIDs the way SLURM proctrack does — only the outer bash receives it, which is why the script-level relay below is necessary.
2. `.magnus_user_script.sh` (rendered by `_render_docker_user_script` in `_scheduler/_submit.py`) opens with `set -m` to enable bash monitor mode, then places the user's `entry_command` inside `( set -e; ... ) &` — a backgrounded subshell that, under `set -m`, becomes its own process group leader (`_magnus_pid` is both PID and pgid). Without `set -m`, non-interactive bash leaves background subshells in the parent's pgrp, and a negative-PID kill would target a non-existent pgrp. The outer bash installs `trap 'kill -TERM -- -$_magnus_pid' TERM` so SIGTERM relays via `killpg` to the entire user pgrp — main, fork children, MPI workers, nested shells all receive the signal. A `while ... wait` loop re-waits after signal-interrupted waits (which return 128+sig) and propagates the child's real exit status through `exit $?`. The outer bash itself does not deferred-terminate because its trap is non-empty.

Together both backends behave the same way at the user-process level: the inner subshell wears `SIG_IGN` for SIGTERM (`trap '' TERM` inside `( … ) &`), symmetric to SLURM's `.magnus_user_script.sh` guard, keeping the subshell alive when the outer trap fans signals out and protecting in-flight handlers from being interrupted by SLURM step teardown. handler-less user code therefore treats SIGTERM as a no-op (inherited `SIG_IGN`); handler-aware code installs its own `signal.signal` / `sigaction` and naturally overrides the guard, runs its handler, chooses its own exit. The pgrp relay covers nested shells transparently — no per-script trap required from user code.

**Docker path (terminate)**: symmetric to the SLURM SIGKILL-direct rationale, `terminate_job` invokes `stop_container(container_name, timeout=0)` which renders as `docker stop -t 0`, sending SIGKILL immediately instead of the default 10-second SIGTERM grace. Without `timeout=0` docker would spend the grace period sending a SIGTERM that the outer bash would only forward (not die on), leaving terminate's "no coordination window" promise unmet.

**State convergence**: `signal_job` never touches the DB. After the signal lands the regular sync loop reconciles the job to `Success` or `Failed`:

- **SLURM mode** is wrapper + sync defense-in-depth. `_wrapper_template.py`'s Phase 3: `ret_code == 0` writes the success marker as before; otherwise if `_signaled` is `True` and the user wrote `.magnus_result` from inside the SIGTERM handler the wrapper still writes the success marker (label `success-after-signal`) and `sys.exit(0)` so SLURM has a chance to mark the job COMPLETED. `_sync.py:_sync_reality_slurm` then prefers the marker over the SLURM state — its `FAILED / CANCELLED / TIMEOUT` branch reads `.magnus_success` first and runs `_finalize_completed_job` if the marker is present, otherwise records the SLURM state as the failure cause. This handles real SLURM versions / configs that flip a batch step to CANCELLED whenever it received SIGTERM regardless of wrapper's exit code. ret ≠ 0 with no handler-written result and no marker stays `Failed`.
- **Docker mode**: container `exit_code == 0` reconciles to `Success` (handler exited cleanly via `sys.exit(0)`); non-zero is `Failed`. No wrapper layer in between, so no fallback marker is needed — the container's exit code is the user's exit code.

**Preemption semantics preserved**: `_decisions._kill_and_pause` still calls `kill_job`; the "yield GPUs immediately" promise is upheld by the SIGKILL-direct design above. Granting B-class jobs a checkpointing window requires a separate preemption-pre-notification design and is out of scope here.

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

With the default `execution.container_image_mode: native`, the runtime receives
the SIF directly. Its writable layer depends on the isolation mode:

| Mode | Writable layer | Capacity limit | Description |
|------|--------|----------|------|
| containall + overlay | ephemeral overlay (sparse ext3 image) | `ephemeral_storage` | Default path (rootless apptainer); `--sparse` creation is instant and disk usage is on-demand |
| containall/contain + writable-tmpfs | RAM tmpfs | shared with `memory_demand` | setuid apptainer or `MAGNUS_NO_OVERLAY=1` |
| none (bare run) | host filesystem pass-through | unlimited | `MAGNUS_CONTAIN_LEVEL=none`, equivalent to pre-overlay behavior |

### Runtime image preparation

Some unprivileged Singularity installations extract a SIF to a directory on every
execution. Launching many tasks can therefore exhaust shared filesystem file-count
quotas even when the compressed image is small and byte capacity is available.
On these Slurm targets, set `execution.container_image_mode: sandbox`.
This mode requires the execution UID to own the Job temporary directory. Local
Slurm uses only the backend's default runner in this mode; cross-user runners
remain on `native`. A directory image's permissions can mask inherited cleanup
ACLs, so unsupported ownership is rejected before conversion.

The wrapper invokes the selected runtime's native
`build --sandbox --notest --disable-cache` once, from the already staged local SIF,
before launching tasks. GNU `timeout` bounds the conversion to
`execution.container_prepare_timeout_seconds` (default 300), followed by a
five-second termination grace. Preparation consumes the existing Slurm allocation
and time limit. Failed or timed-out conversion starts no user tasks and writes no
success marker. Each attempt uses a fresh Job-private directory under
`{ephemeral}/.magnus_tmp/`; partial preparations are never reused.

All tasks execute the same directory image read-only, without an overlay or
`--writable-tmpfs`. The workspace remains writable. Separate Job-and-node-private
directories are bound to `/tmp`, `/var/tmp` and `$MAGNUS_HOME/.tmp`; `/tmp` and
`/var/tmp` share one directory, while SDK transfer scratch has its own directory.
Same-node tasks share these temporary paths; different nodes and Jobs do not.
`TMPDIR` is set to `/tmp`, and the entry command may override it. Dependencies
belong in the image; arbitrary writes to its root filesystem are not supported
in this mode. `ephemeral_storage` is not a hard quota for these directory writes.

The preparation and temporary paths must be visible to the allocated nodes, like
the existing Job workspace. SSH targets place them under the remote Job directory,
not the controller's `server.ephemeral_root`. The existing terminal cleanup owns
these directories, including leftovers after cancellation or host failure; there
is no persistent sandbox-image cache. Cleanup restores owner traversal/write
permissions on directories it owns, without changing files or following image
symlinks, before removing scratch. Container conversion may temporarily need
more than one image's unpacked space. Configure storage for simultaneous Jobs as
well as image size. The default `native` mode and Docker/local behavior are
unchanged; sandbox preparation is available to both Singularity and Apptainer.

### SDK runtime detection protocol

The SDK's `file_transfer.get_tmp_base()` uses the following criteria to determine the file relay directory (temporary files for upload tar compression and download tar extraction):

1. The environment variable `MAGNUS_HOME` exists
2. `$MAGNUS_HOME/workspace/` is an existing directory (created and bind-mounted by the Magnus runtime)

When both conditions are met, the relay directory is `$MAGNUS_HOME/.tmp/`; otherwise
it falls back to the system temporary directory. Native mode supplies this path
through the container writable layer; sandbox mode explicitly binds writable
Job-and-node-private storage at the same path, so old SDKs retain their behavior.

**Why not `/tmp`**: under both isolation modes, `/tmp` is not an ideal relay location:

| Mode | `/tmp` location | Problem |
|------|-------------|------|
| overlay | inside the overlay image | shares the `ephemeral_storage` quota with in-container writes like pip install |
| writable-tmpfs | RAM tmpfs | bounded by runtime session-directory settings and Job memory; large files can trigger ENOSPC |

In native mode, `$MAGNUS_HOME/.tmp/` shares the container writable layer's budget
with `/tmp`, while keeping transfer files separate. Explicit Job-directory binds
are also part of the runtime's isolation boundary; a writable layer alone does
not establish filesystem isolation.

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
  [--partition={partition}] \            # GPU type's partition, else execution.slurm.partition; omitted when null
  [--qos={qos}] \                        # execution.slurm.qos, omitted when null
  [--account={account}] \                # execution.slurm.account, omitted when null
  --output={work}/slurm/output.txt \
  [--open-mode=append] \                 # unless output is overwritten
  --gres=gpu:[{gres_type}:]{gpu_count} \ # only when gpu_count > 0; type name dropped for untyped gres
  --mem={memory_demand} \                # explicit mem_mode only (see below)
  --cpus-per-task={effective_cpu_count} \
  [--time={time_limit}] \                # per-job walltime in minutes, omitted when null
  # Script content: {module load ...}; exec python3 {work}/wrapper.py
```

`partition` / `qos` / `account` are emitted only when set under `execution.slurm`; self-owned sites leave them null and the flags are absent (byte-identical to the historical command). Tenant clusters that mandate them set them here. Any `execution.slurm.module_loads` entries are prepended to the batch script as `module load ...` lines before the runtime is on `PATH`.

**Per-GPU-type partition + gres routing** (`cluster.gpus[]`): each GPU type may declare its own optional `partition` and `gres_type`. When a job requests that GPU type it is submitted to the type's `partition` (falling back to `execution.slurm.partition` when unset), and `--gres` uses its `gres_type` — the type name by default (`--gres=gpu:{value}:N`), or an untyped `--gres=gpu:N` when `gres_type` is null. CPU jobs, and GPU types without a `partition`, use the default partition. This lets one station drive a shared cluster that splits CPU and each GPU model into separate SLURM partitions (e.g. CPU jobs on the default partition, A100-80G jobs on a dedicated `gpu-a100` partition with untyped gres), or host multiple GPU models in one partition distinguished by typed gres (e.g. `--gres=gpu:rtx5090:N` vs `--gres=gpu:a100:N`). Single-partition sites set neither field and the command is byte-identical to before. In tenant mode the cluster view aggregates capacity and running tasks across the default partition plus every distinct GPU partition.

**Memory mode** (`execution.slurm.mem_mode`):
- `explicit` (default, self-owned sites): `--mem={memory_demand}` requests memory **per node**, independently of tasks per node. `--mem=0` requests all available memory; its total cannot be calculated from the request alone.
- `per_cpu` (clusters that disable `--mem`): no `--mem` is passed. SLURM assigns memory through `DefMemPerCPU`, and `memory_demand` is folded into the core count: `effective_cpu_count = max(cpu_count, ceil(memory_demand_mb / mem_per_cpu_mb))`, so requesting more memory raises the core count. The submission path **normalizes** stored `cpu_count` / `memory_demand` to the effective **per-task** request (`normalize_per_cpu_resources`, after default-fill and before limit validation). The fold is shared with the sbatch builder (`effective_cpu_count_per_cpu`) and is idempotent. These fields are not whole-job allocations or measurements of memory use.

**Multi-node resource display**: `node_count` and `tasks_per_node` default to one. CPU is requested per task (`--cpus-per-task`), while GPUs are requested per node (`--gres`). Job list/detail responses retain these input fields and add `resource_summary` for display:

| Whole-job request | Calculation |
|---|---|
| CPU | `cpu_count × node_count × tasks_per_node` |
| RAM, `per_cpu` | normalized `memory_demand × node_count × tasks_per_node` |
| RAM, `explicit` | `memory_demand × node_count` |
| GPU | `gpu_count × node_count` |

For example, two nodes with 64 tasks per node, one CPU per task and 4000 MiB per CPU request **128 CPUs and 500 GiB** in total. The table and detail view show totals with a topology breakdown. `resource_summary.memory_bytes` is in bytes; null means the total is unknown, not zero. New jobs store a server-owned `resource_mode` snapshot (`slurm_per_cpu`, `slurm_per_node`, or `local`). Existing jobs retain null snapshots: their request summaries use current site settings and set `mode_inferred=true`, which the UI labels as an estimate. No historical rows are rewritten to assert an unrecorded mode.

The cluster running list instead uses SLURM's whole-job allocation for both Magnus and external jobs (`resource_summary.basis="allocation"`), without multiplying it again. Ordinary job lists/details use `basis="request"`; neither is a live utilization metric. Local/Docker summaries show the submitted values without multiplying unsupported node/task topology, and do not claim resource limits are enforced. Raw API fields and SDK submission semantics remain unchanged.

`time_limit` (minutes) maps to `--time`; when null no `--time` is emitted and the job runs under the partition's default walltime. Declaring a short walltime makes a job more eligible for backfill on shared clusters.

The environment variables `MAGNUS_RUNNER` and `MAGNUS_TOKEN` are passed via sbatch's process environment.

### Resource accounting boundaries

Requests, allocations, and utilization have different sources. Submission limits and request summaries use the same memory units (unsuffixed SLURM memory means MiB); per-CPU normalization rounds up without first discarding fractional MiB. Explicit memory is rounded to whole MiB per node before multiplying by node count, and sbatch receives that same value. Storage quantities keep their byte convention. Allocations inferred from legacy SLURM request fields carry `allocation_inferred=true` and source fields, rather than claiming direct allocation evidence.

Cluster cards show available CPU, memory and GPU / configured capacity for the union of selected nodes. Available capacity is configured capacity minus node allocations, clamped at zero; admission restrictions are not subtracted again. CPU/memory allocation comes from each node's `AllocTRES`, with `CPUAlloc`/`AllocMem` fallbacks. GPU allocation uses detailed `GresUsed`, then explicit GPU TRES; missing GPU TRES alone does not imply zero; it is neither live utilization nor an immediate placement promise. A running job on a draining/reserved node is counted once. Whole-job rows may span beyond this pool or remain in CG after node accounting changes; they are not summed into these cards. The nullable `cpu_used`, `mem_used_mb` and `gpu_used` API fields preserve unknown dimensions, without substituting zero. Legacy numeric `used`/`free` fields remain; free means configured minus allocated, and `allocation_tracked=false` guards incomplete observations for older clients. Node admission restrictions remain in the scheduler. Local/Docker allocation is not tracked and is shown as unknown.

Incomplete accounting and failed queries cannot turn into an empty, fully free cluster. A combined capacity/allocation observation carries its timestamp; cached stale results are bounded and labelled. Refresh failure returns 503 rather than a fresh-looking zero snapshot. Historical GPU samples match observed SLURM IDs to Magnus ownership and sum actual allocation, including CG and DB-state lag. Failed, inferred-GPU or cross-scope observations do not write fabricated exact history. Old samples remain unchanged. GPU history requires known allocations on every GPU node and validates GPU visibility only: hidden CPU or memory jobs do not invalidate a known GPU sample. All sample attempts, including skipped or failed queries, respect `snapshot_interval`; authoritative admission still validates all resource dimensions.

Authoritative scheduling uses whole-job CPU/RAM/GPU demand and actual GPU types. An accepted job not yet observed in allocated resources blocks further admission, preventing repeated promises of the same free capacity. For a fully known single-node layout, backfill spends only the current per-dimension surplus after reserving the head's demand. This is conservative resource reservation, **not time-based EASY**: the old `head + candidate <= total` condition did not prove no delay. Preemption needs a complete plan that recovers every deficient dimension using observed B-job allocations; irrelevant GPU types, inferred allocations or a partial plan do not justify kills. Cancellation is followed by another real resource observation.

For complex/multi-node layouts, aggregate counts cannot prove placement. Magnus sends only the priority head to SLURM and waits for its queue/allocation state; it does not preempt or backfill on an unproven placement assumption. Unknown/default or all-node-memory requests are also resolved by SLURM. Tenant mode continues to delegate queueing and preemption to SLURM. These rules change admission decisions, not the requested SLURM layout.

System metrics identify sampling scope. CPU/RAM from the SLURM batch wrapper carry `scope=batch_step`; with `srun`, MPI ranks may live in sibling cgroups, so coverage is `partial`, not whole-job utilization. Docker CPU/RAM use `scope=container`. GPU samples use `scope=device`: device measurements are not process-attributed usage, and node zero cannot represent remote nodes. CPU samples include their normalization CPU count; values above 100% are retained instead of hiding scope/quota discrepancies. The UI explains coverage for both new labelled samples and historical multi-task jobs. This does not introduce a distributed metrics collector.

## Scheduler heartbeat and state sync

Heartbeat interval is `scheduler.heartbeat_interval` (default 2 seconds); each tick:

1. **`_sync_reality`**: iterate QUEUED/RUNNING jobs and check real state via `squeue`
   - SLURM reports RUNNING → DB marks RUNNING
   - SLURM reports COMPLETED + `.magnus_success` exists → SUCCESS, and check `.magnus_result` and `.magnus_action`
   - SLURM reports COMPLETED but no `.magnus_success` → FAILED
   - SLURM reports FAILED/CANCELLED/TIMEOUT → FAILED
2. **`_make_decisions`**: schedule PENDING/PAUSED jobs
3. **`_record_snapshot`**: record a cluster snapshot every `snapshot_interval` (default 300 seconds)
4. **`_reclaim_workspace_over_cap`**: at most hourly, evict oldest terminal-job directories under `workspace/jobs` while their total exceeds `scheduler.workspace_size_cap`

## Resource preparation

Executed in parallel in the PREPARING phase:

**Image pull** (`_resource_manager.ensure_image`):
- docker URI → SIF filename mapping (`docker://a/b:tag` → `a_b_tag.sif`)
- Cache directory `{magnus_root}/container_cache/`, LRU eviction, cap `resource_cache.container_cache_size`
- Per-image asyncio.Lock to prevent duplicate pulls
- 3 retries + exponential backoff; non-transient errors (unauthorized, not found) fail immediately

**Repo clone** (`_resource_manager.ensure_repo`):
- Cache directory `{magnus_root}/repo_cache/`, one directory per (namespace, repo, branch) with a hash suffix so distinct triples never share one, LRU eviction, cap `resource_cache.repo_cache_size`
- fetch in the cache when needed → copy to `{work}/repository/` → checkout to the specified commit SHA
- Clone and fetch each get 3 retries + exponential backoff; an out-of-space failure fails immediately
- A cache directory that cannot be used (not a git repository of its own, HEAD not resolving to a commit, or a stale `index.lock` — what a killed clone, an interrupted copy or a damaged `.git` leaves) is treated as a miss: removed and cloned again
- A job re-entering preparation (e.g. after a backend restart) rebuilds its `repository/` copy instead of trusting a leftover one
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
    workspace_size_cap: 50G                 # cap on terminal-job workspace dirs (LRU); null disables
    allow_root: false                       # whether to allow root runner
  resource_cache:
    container_cache_size: 80G               # SIF cache cap (LRU)
    repo_cache_size: 20G                    # git repo cache cap (LRU)

cluster:
  default_cpu_count: 4
  default_memory_demand: 1600M
  max_cpu_count: 128                       # upper bound enforced at submission (HTTP 400 over)
  max_memory_demand: 256G                  # upper bound enforced at submission
  default_time_limit: null                 # default --time (minutes); null = no --time emitted
  max_time_limit: null                     # walltime cap (minutes); null = uncapped
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

execution:
  backend: slurm                           # slurm | local
  container_runtime: apptainer             # apptainer | singularity (HPC); docker (local)
  slurm:                                   # SLURM submission dialect; every field defaults to current behavior
    partition: null                        # null = don't pass --partition
    qos: null                              # null = don't pass --qos
    account: null                          # null = don't pass --account
    mem_mode: explicit                     # explicit (pass --mem) | per_cpu (derive cores, omit --mem)
    mem_per_cpu_mb: 4000                   # only used when mem_mode == per_cpu
    module_loads: []                       # `module load` lines prepended in the job script
```

### Source file index

| File | Responsibility |
|------|------|
| `back_end/server/_scheduler/` | Scheduler core: heartbeat, state sync, wrapper generation, SLURM submission |
| `back_end/server/_slurm_manager/` | SLURM CLI wrapper (sbatch/squeue/scancel/sinfo) |
| `back_end/server/_resource_manager/` | Image pull + repo clone, with LRU cache |
| `back_end/server/routers/jobs.py` | Job CRUD API, lazy reading of .magnus_result/.magnus_action |
| `back_end/server/models/` | Job model (SQLAlchemy) |
| `configs/magnus_config.yaml` | Configuration source |
| `docker/magnus-runtime/Dockerfile` | Child Magnus runtime image |
| `scripts/setup_single_node_slurm.sh` | In-container SLURM bootstrap script |
