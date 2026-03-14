# Local Magnus Mode

## Overview

Magnus supports a local execution mode that runs jobs in Docker containers on the user's machine instead of on a SLURM-managed HPC cluster. Local mode provides a **full Magnus stack** — backend, frontend, and SDK — identical to the HPC deployment, with Docker replacing SLURM+Apptainer as the execution layer.

```
User/Agent → magnus CLI / SDK / Web UI → HTTP API → Local Magnus Server → Docker containers
```

## Getting Started

Prerequisites: Docker, git, uv, and Node.js must be installed.

```bash
pip install magnus-sdk
git clone https://github.com/rise-agi/magnus.git ~/.magnus/repository
magnus local start    # checks deps, installs backend/frontend deps, starts everything
magnus run hello-world -- --message "It works!"
magnus local stop     # stops backend + frontend, restores previous SDK site
```

After `magnus local start`:
- Backend: `http://127.0.0.1:8017`
- Frontend: `http://localhost:3011` (auto-login, no Feishu required)

## Architecture

### Binary Backend Choice

`execution.backend` is either `"slurm"` (HPC) or `"local"` (Docker). Cross-configuration is not permitted. When `backend: local`:

- `auth.provider` must be `"local"` (free-login)
- `container_runtime` is forced to `"docker"`
- SLURM dependencies are not required
- Cluster resource attributes (gpu_count, memory_demand, cpu_count, ephemeral_storage) are accepted but **not enforced**

### Config Isolation

`magnus local start` generates a **separate** config at `~/.magnus/local_config.yaml`. The production config at `configs/magnus_config.yaml` is never modified. The backend and frontend receive the local config path via:
- Backend: `--config ~/.magnus/local_config.yaml`
- Frontend: `MAGNUS_CONFIG_PATH=~/.magnus/local_config.yaml`

| Field | HPC Mode | Local Mode |
|-------|----------|------------|
| `execution.backend` | `slurm` | `local` |
| `execution.container_runtime` | `apptainer` | `docker` |
| `auth.provider` | `feishu` | `local` |
| `server.root` | site-specific | `~/.magnus/data` |
| `server.back_end_port` | 8017 | 8017 |
| `server.front_end_port` | 3011 | 3011 |
| `cluster.*` | full cluster spec | minimal defaults |

### Ports

Local mode uses fixed ports: **8017** (backend) and **3011** (frontend). These are the same as the production ports — the `--deliver` flag is passed to skip the +2 dev offset.

## Frontend (Web UI)

The frontend runs in local mode with these differences:

- **No login required**: `auth.provider: local` triggers automatic authentication. The `AuthProvider` detects local mode via `NEXT_PUBLIC_AUTH_PROVIDER` and calls `POST /api/auth/local/login` to get a JWT automatically.
- **Config injection**: `MAGNUS_CONFIG_PATH` and `MAGNUS_DELIVER=TRUE` are passed as environment variables to `npm run dev`.
- **API proxy**: The Next.js catch-all route (`/api/[...path]`) proxies requests to the backend, same as HPC mode.

## Directory Layout

```
~/.magnus/
├── config.json              # SDK site config (which server to talk to)
├── local_config.yaml        # Generated server config for local mode
├── local_server.pid         # Backend PID
├── local_frontend.pid       # Frontend PID
├── local_previous_site      # Previous SDK site name (restored on stop)
├── data/                    # Magnus root (database, workspace, caches)
│   ├── database/            # SQLite database
│   └── workspace/           # Job working directories
│       └── jobs/
│           └── {job_id}/
│               ├── repository/              # Git repo checkout
│               ├── slurm/output.txt         # Job logs (streamed incrementally)
│               ├── .magnus_user_script.sh   # User's entry command
│               ├── .magnus_success          # Success marker (written by host)
│               ├── .magnus_result           # Job result (optional)
│               └── .magnus_action           # Post-job action (optional)
└── repository/              # Magnus backend code (if not editable-installed)
```

## Job Execution Flow

### HPC Mode (SLURM + Apptainer)
```
PENDING → wrapper.py → sbatch → SLURM queue → apptainer exec → exit 0 → wrapper writes .magnus_success
```

### Local Mode (Docker)
```
PENDING → docker pull → docker run -d → heartbeat polls docker inspect → host writes .magnus_success
```

### Docker Run Command

```bash
docker run -d \
  --name magnus-job-{job_id} \
  --network host \              # Linux only; Windows/macOS use bridge mode
  -v {job_working_table}:/magnus/workspace \
  -e MAGNUS_TOKEN=... \
  -e MAGNUS_ADDRESS=... \       # Linux: http://127.0.0.1:8017, Windows/macOS: http://host.docker.internal:8017
  -e MAGNUS_JOB_ID=... \
  -e MAGNUS_HOME=/magnus \
  -w /magnus/workspace/repository \
  {image} \
  bash /magnus/workspace/.magnus_user_script.sh
```

Key differences from HPC mode:
- **No wrapper.py**: Docker run is a single subprocess call
- **Host-side success marker**: The host Python writes `.magnus_success` after detecting exit code 0 (symmetric with HPC mode where wrapper.py writes the marker)
- **Incremental log streaming**: Each heartbeat calls `docker logs --since <timestamp>` and appends to `slurm/output.txt`
- **No overlay filesystem**: Docker provides its own writable layer
- **No GPU spy thread**: GPU monitoring is skipped
- **Resource attributes accepted but not enforced**: Memory/CPU limits are not applied to containers
- **GPU passthrough**: If `gpu_count > 0`, `--gpus all` is added (requires NVIDIA Container Toolkit)

### Three-Phase Sync

`_sync_reality_docker` follows a three-phase pattern symmetric with `_sync_reality_slurm`:

1. **Phase 1** — Collect active job IDs and statuses (short DB session)
2. **Phase 2** — Check Docker container status + dump incremental logs (no DB session)
3. **Phase 3** — Batch update job statuses in DB (short session)

This avoids holding a DB session during external Docker calls.

## Cross-Platform Support

| Feature | Linux | Windows | macOS |
|---------|-------|---------|-------|
| Docker networking | `--network host` | bridge + `host.docker.internal` | bridge + `host.docker.internal` |
| system_entry_command | bash execution | skipped (no bash) | bash execution |
| Process management | `start_new_session` | `CREATE_NEW_PROCESS_GROUP` | `start_new_session` |
| npm command | `npm` | `npm.cmd` | `npm` |

## system_entry_command Interpretation

Blueprints may specify a `system_entry_command` that sets `APPTAINER_BIND` for bind mounts. In local mode, the scheduler (`_extract_bind_mounts_from_system_entry_command`):

1. Executes the command in a bash subprocess
2. Reads the resulting `APPTAINER_BIND` environment variable
3. Translates each entry to Docker `-v` flags

This is a **lossy conversion** — only the `APPTAINER_BIND` variable is extracted. Other environment variables and side effects (e.g., `module load`) are discarded.

On Windows, `system_entry_command` is skipped entirely (no bash available).

## Authentication

Local mode disables authentication entirely. Since the server binds to `127.0.0.1`, all requests are trusted. At server startup:
1. A default user is created (named after the OS user)
2. `get_current_user` returns this user without checking any token
3. All users are treated as admins
4. No Feishu OAuth is required

The frontend auto-initializes via `POST /api/auth/local/login` (returns user info for UI display). The SDK sends a placeholder token (`"local"`) to satisfy its own non-empty validation, but the backend ignores it. On `magnus local stop`, the previous SDK site is restored.

## Bundled Blueprints

`magnus local start` automatically registers blueprints bundled in `sdks/python/src/magnus/bundled/blueprints/`. This provides a working out-of-the-box experience. Bundled blueprints are re-registered on each start (idempotent overwrite).

To add a bundled blueprint, place a `.py` file in the `bundled/blueprints/` directory. The filename (minus `.py`) becomes the blueprint ID.

## Explorer

Explorer (AI assistant) is enabled by the **presence** of `server.explorer` in the config, not by the backend mode. Local mode users who provide an API key in their config will have full Explorer access.

## Limitations

- **No GPU monitoring**: nvidia-smi polling is not implemented for Docker containers
- **No resource enforcement**: Memory/CPU limits are not enforced on containers
- **No service proxy**: The service system is not tested in local mode
- **No preemption**: All jobs run without priority-based preemption
- **No ephemeral storage enforcement**: Docker's writable layer is used without size limits
