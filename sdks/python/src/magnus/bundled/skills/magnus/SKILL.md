---
name: magnus
description: Run blueprints on the Magnus cloud platform via the `magnus` CLI. Use this skill when you need to execute remote computations on GPU/CPU clusters.
---

# Magnus CLI

Magnus executes **blueprints** on remote GPU/CPU clusters. The CLI is self-documenting — use `--help` at each level to discover what's available:

```bash
magnus --help                          # top-level commands and shortcuts
magnus run --help                      # CLI options for blueprint execution
magnus job --help                      # job inspection and management
magnus blueprint schema <blueprint-id> # parameter schema for a specific blueprint
```

## Setup

```bash
pip install "magnus-sdk>=0.6.0"
magnus config                  # check current connection — skip setup if already configured
```

If not configured, run `magnus login` (saves to `~/.magnus/config.json`, all shells pick up immediately):

```bash
magnus login                                      # interactive
magnus login <site> -a <address> -t <token>        # non-interactive (for scripts and agents)
```

#### Config resolution order

| Priority | Source | Use case |
|----------|--------|----------|
| 1 (highest) | Environment variables `MAGNUS_ADDRESS`, `MAGNUS_TOKEN` | CI/CD pipelines, automation scripts |
| 2 | `~/.magnus/config.json` (via `magnus login`) | **Recommended for interactive use** |
| 3 (lowest) | Built-in default site | Fallback |

**Do not use environment variables for interactive work.** Inline `MAGNUS_ADDRESS=... magnus run ...` is non-portable and not persisted. If `magnus config` shows an env-override warning, remove the `export` lines from your shell profile (`.bashrc`, `.zshrc`, or system environment variables on Windows) to let `magnus login` take effect.

## Core Workflow

Run `magnus config` first to verify your connection. If not configured, use `magnus login` once — credentials persist across all shells and subsequent commands. Do not inline `MAGNUS_ADDRESS`/`MAGNUS_TOKEN` environment variables before each command.

```
magnus list                           # discover available blueprints
magnus blueprint schema <id>          # inspect parameters, types, defaults
magnus run <id> [--] --key value ...  # execute and wait for result
```

### The `--` separator

Divides CLI options (left) from blueprint arguments (right). **Optional** — without it, all arguments route to the blueprint and CLI options use defaults. See `magnus run --help` for available CLI options.

### FileSecret — automatic file upload

When a blueprint parameter is typed `FileSecret`, passing a local file path triggers automatic upload. Files and directories both work. Check `magnus blueprint schema <id>` to see which parameters accept file paths.

### Output download (MAGNUS_ACTION)

Blueprints that produce output files write a `magnus receive` command to `MAGNUS_ACTION`. With `--execute-action true` (default), `magnus run` auto-executes this receive command.

## Job Management

```bash
magnus job result <job-id>    # structured result (same as magnus run output)
magnus logs <job-id>          # stdout + stderr from cloud execution
magnus status <job-id>        # job status
magnus kill <job-id>          # terminate a running job
magnus jobs                   # list recent jobs with IDs
```

`<job-id>` is the absolute job ID printed by `magnus run` (e.g. `abc123`). Negative indices also work as a shorthand (`-1` = most recent, `-2` = second most recent), but they are scoped to your account, not your session — if another agent or session submits a job under the same account, the indices shift. In multi-step pipelines, prefer capturing the job ID from `magnus run` output and passing it explicitly.

Blueprint results typically contain at minimum `success` (bool) and `message` (str).

## Error Handling

If `magnus run` returns a non-success result:

1. `magnus job result <job-id>` — structured error details
2. `magnus logs <job-id>` — full execution output
3. Fix and re-run

Jobs continue server-side even if your client disconnects — interrupting `magnus run` (Ctrl-C) or a network drop only detaches the local client, the job keeps running. **Do not re-submit** — instead, reconnect:

1. `magnus status <job-id>` (or `sleep 30 && magnus status <job-id>` to wait out a transient outage)
2. Once the job shows completed, `magnus job result <job-id>` to read `MAGNUS_RESULT`
3. Run the `magnus receive` command from `MAGNUS_ACTION` to download outputs

Use `magnus kill <job-id>` to terminate a job that was submitted with wrong parameters to free up cluster resources.
