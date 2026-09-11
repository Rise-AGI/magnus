# sdks/python/src/magnus/cli/_jobs.py
"""`magnus job <verb>` sub-commands and the shared job helpers (job-ref
resolution, argument parsing, status/logs/result/kill/signal/metric handlers)
that the top-level job shortcuts delegate to."""
import json
import typer
from typing import List, Optional, Any, Dict, Tuple
from pathlib import Path
from rich.table import Table
from rich.markup import escape as rich_escape
from ..exceptions import MagnusError, ExecutionError
from ..actions import execute_action as run_action
from .. import (
    submit_job as api_submit_job,
    list_jobs as api_list_jobs,
    get_job as api_get_job,
    get_job_result as api_get_job_result,
    get_job_action as api_get_job_action,
    get_job_logs as api_get_job_logs,
    get_metric_streams as api_get_metric_streams,
    get_metric_points as api_get_metric_points,
    save_metric_chart as api_save_metric_chart,
    terminate_job as api_terminate_job,
    signal_job as api_signal_job,
)
from ._shared import (
    OutputFormat,
    SignalSafeSpinner,
    _CLI_KEY_TYPES,
    _JOB_REF_CTX,
    _auto_format,
    _coerce_cli_value,
    _extract_job_ref,
    _format_time,
    _get_yaml_dumper,
    _job_view_link_msg,
    _output_data,
    apply_cli_defaults,
    console,
    print_error,
    print_msg,
)


# job_id 由 secrets.token_hex(8) 生成，恒为 16 位十六进制。约 (10/16)^16 ≈ 0.05% 的
# 概率会摇出**全是数字**的 ID（如 3689620425515827），这类 ID 是合法的、必须原样发给
# 服务端；绝不能被下面的 int() 当成"索引/数字参数"吞掉。
_JOB_ID_HEX_LEN = 16
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _looks_like_job_id(ref: str) -> bool:
    """定长 16 位十六进制 token（含可能的全数字形态）即视为 job_id，据此与负索引区分。"""
    return len(ref) == _JOB_ID_HEX_LEN and all(character in _HEX_DIGITS for character in ref)


def _resolve_job_ref(ref: str) -> str:
    """
    解析 job 引用：
    - 负数索引：-1 = 最新，-2 = 第二新，...
    - 否则视为 job_id 原样返回

    先按 16-hex 形状把 job_id 短路认出来——否则一个恰好全数字的合法 job_id 会被 int()
    误判成索引，导致对它 kill/status/logs 静默走空或报"use negative index"的错话。
    """
    if _looks_like_job_id(ref):
        return ref

    try:
        idx = int(ref)
    except ValueError:
        return ref

    if idx >= 0:
        raise MagnusError(f"Use negative index (-1 = newest, -2 = second newest, ...). Got: {idx}")

    result = api_list_jobs(limit=100)
    items = result.get("items", [])

    if not items:
        raise MagnusError("No jobs found on server.")

    actual_idx = -idx - 1

    if actual_idx >= len(items):
        raise MagnusError(f"Index {idx} out of range. Only {len(items)} jobs available (-1 to -{len(items)}).")

    return items[actual_idx]["id"]


job_app = typer.Typer(
    name="job",
    help=(
        "Job operations: inspect status, read logs, fetch results, and terminate jobs.\n\n"
        "Subcommands:\n"
        "  list      List recent jobs\n"
        "  status    Show detailed job status\n"
        "  logs      Show job logs (paginated, ~200KB/page)\n"
        "  result    Show job result (JSON)\n"
        "  action    Show or execute the job's MAGNUS_ACTION script\n"
        "  kill      Terminate a running job\n"
        "  signal    Send SIGTERM to a running job (handler-aware code can converge to Success)\n"
        "  submit    Submit a job directly (fire & forget)\n"
        "  execute   Submit a job and wait for completion\n\n"
        "Jobs can be referenced by negative index: -1 = newest, -2 = second newest.\n"
        "Indices are shared across terminals and shift as new jobs arrive; use the job ID for stability.\n\n"
        "Top-level shortcuts: magnus jobs, magnus status, magnus logs, magnus kill, magnus signal.\n\n"
        "Examples:\n"
        "  magnus job list\n"
        "  magnus job status -1\n"
        "  magnus job logs -2 --page 0\n"
        "  magnus job kill -1 -f"
    ),
)
# Job submission parameter keys (used by submit/execute commands)
_JOB_PARAM_KEYS: Dict[str, type] = {
    "task_name": str,
    "repo_name": str,
    "branch": str,
    "commit_sha": str,
    "entry_command": str,
    "gpu_type": str,
    "gpu_count": int,
    "namespace": str,
    "job_type": str,
    "description": str,
    "container_image": str,
    "cpu_count": int,
    "memory_demand": str,
    "node_count": int,
    "tasks_per_node": int,
    "time_limit": int,
    "ephemeral_storage": str,
    "runner": str,
    "system_entry_command": str,
}


def _parse_job_args(args: List[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Parse job CLI args into (cli_config, job_params)."""
    cli_config: Dict[str, Any] = {}
    job_params: Dict[str, Any] = {}

    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")

            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                value = args[i + 1]
                i += 2
            else:
                value = "true"
                i += 1

            if key in _CLI_KEY_TYPES:
                cli_config[key] = _coerce_cli_value(key, value)
            elif key in _JOB_PARAM_KEYS:
                expected_type = _JOB_PARAM_KEYS[key]
                job_params[key] = expected_type(value) if expected_type is not str else value
            else:
                job_params[key] = value
        else:
            i += 1

    return cli_config, job_params


def _display_job_result(
    result: Optional[str],
    job_id: Optional[str],
    execute_action: bool,
):
    """Shared result/action display for run (blueprint) and execute (job)."""
    has_result = result is not None
    has_action = False
    action_text: Optional[str] = None

    if job_id:
        try:
            action_raw = api_get_job_action(job_id)
            if action_raw:
                action_text = action_raw.strip()
                has_action = bool(action_text)
        except Exception:
            pass

    if not has_result and not has_action:
        print_msg("[dim]No result or action returned.[/dim]")
        return

    if has_result:
        console.rule("[bold green]MAGNUS RESULT[/bold green]")
        try:
            assert isinstance(result, str)
            json_obj = json.loads(result)
            console.print_json(data=json_obj)
        except Exception:
            console.print(result, markup=False, highlight=False)
        console.rule()

    if has_action:
        assert action_text is not None
        if execute_action:
            print_msg("Executing action...")
            try:
                run_action(action_text)
            except ExecutionError as e:
                print_error(str(e))
                raise typer.Exit(code=1)
        else:
            console.rule("[bold yellow]MAGNUS ACTION[/bold yellow]")
            console.print(action_text, markup=False, highlight=False)
            console.rule()


_REQUIRED_JOB_KEYS = ["task_name", "repo_name", "branch", "commit_sha", "entry_command"]

_JOB_PARAMS_DOC = """
Required parameters:
  --task-name TEXT          Job display name
  --repo-name TEXT          Repository name
  --branch TEXT             Git branch
  --commit-sha TEXT         Git commit SHA
  --entry-command TEXT      Command to execute

Optional parameters:
  --gpu-type TEXT           GPU model (e.g. a100, rtx5090)
  --gpu-count INT           Number of GPUs
  --cpu-count INT           Number of CPUs (per rank in multi-node jobs)
  --memory-demand TEXT      Memory limit (e.g. 16G)
  --node-count INT          Nodes for a multi-node MPI job (needs a site with multi-node enabled; default 1)
  --tasks-per-node INT      MPI ranks per node (default 1)
  --time-limit INT          Max wall-clock minutes (SLURM --time; omit = partition default)
  --ephemeral-storage TEXT  Disk limit (e.g. 10G)
  --container-image TEXT    Container image URI (default: cluster config;
                              e.g. docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime)
  --runner TEXT             Runner name
  --namespace TEXT          Repository namespace
  --job-type TEXT           Job type (A1/A2/B1/B2)
  --description TEXT        Job description
  --system-entry-command TEXT  System-level setup script
""".strip()

_SUBMIT_OPTIONS_EPILOG = f"""{_JOB_PARAMS_DOC}

CLI options:
  --timeout FLOAT           HTTP timeout in seconds (default: 10)
  --verbose                 Print debug info

Cached images: 'magnus image list'. Refresh: 'magnus refresh <image_id>'."""

_EXECUTE_OPTIONS_EPILOG = f"""{_JOB_PARAMS_DOC}

CLI options:
  --timeout FLOAT           Max wait time in seconds (default: infinite)
  --poll-interval FLOAT     Poll interval in seconds (default: 2)
  --execute-action BOOL     Auto-execute MAGNUS_ACTION (default: true)
  --verbose                 Print debug info

Cached images: 'magnus image list'. Refresh: 'magnus refresh <image_id>'."""


def _validate_job_params(params: Dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_JOB_KEYS if k not in params]
    if missing:
        raise MagnusError(
            f"Missing required parameters: {', '.join('--' + k.replace('_', '-') for k in missing)}"
        )


STATUS_COLORS = {
    "Pending": "yellow",
    "Running": "cyan",
    "Success": "green",
    "Failed": "red",
    "Terminated": "magenta",
}


# =============================================================================
# Job sub-commands: magnus job <verb>
# =============================================================================

@job_app.command(name="list")
def job_list_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of jobs to fetch"),
    name: Optional[str] = typer.Option(None, "--name", "-n", "--search", "-s", help="Search by task name or job ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List recent jobs.

    Displays a table of recent jobs with index, ID, task name, status, GPU
    count, and creation time. The index column (-1, -2, ...) can be used
    in place of job IDs in other commands. Pipe-friendly: outputs YAML
    when stdout is not a TTY.

    Examples:
      magnus job list
      magnus job list -l 20
      magnus job list -s "training"
      magnus job list -f json
    """
    try:
        result = api_list_jobs(limit=limit, search=name)
        items = result.get("items", [])
        total = result.get("total", 0)

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data({"total": total, "items": items}, fmt)
            return

        if not items:
            print_msg("No jobs found.")
            return

        table = Table(title=f"Jobs ({len(items)}/{total})", show_header=True, header_style="bold")
        table.add_column("Idx", style="dim", width=4)
        table.add_column("Job ID")
        table.add_column("Task", max_width=30)
        table.add_column("Status", width=10)
        table.add_column("GPU", width=4)
        table.add_column("Created", width=12)

        for idx, job in enumerate(items):
            status = job.get("status", "Unknown")
            status_color = STATUS_COLORS.get(status, "white")

            table.add_row(
                str(-(idx + 1)),
                rich_escape(job.get("id", "")),
                rich_escape((job.get("task_name") or "-")[:30]),
                f"[{status_color}]{rich_escape(status)}[/{status_color}]",
                str(job.get("gpu_count", 0)),
                _format_time(job.get("created_at")),
            )

        console.print(table)
        print_msg("[dim]Use: magnus job status -1, magnus job kill -2 -f, ...[/dim]")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


def _do_job_status(job_ref: str) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        job = api_get_job(resolved_id)

        status = job.get("status", "Unknown")
        status_color = STATUS_COLORS.get(status, "white")

        console.print()
        console.rule(f"[bold]Job: {rich_escape(job.get('id', 'N/A'))}[/bold]")
        console.print(f"  [bold]Task:[/bold]    {rich_escape(job.get('task_name', '-'))}")
        console.print(f"  [bold]Status:[/bold]  [{status_color}]{rich_escape(status)}[/{status_color}]")
        console.print(f"  [bold]GPU:[/bold]     {job.get('gpu_count', 0)}")
        console.print(f"  [bold]Type:[/bold]    {rich_escape(str(job.get('job_type', '-')))}")
        console.print(f"  [bold]Created:[/bold] {_format_time(job.get('created_at'))}")
        console.print(f"  [bold]Started:[/bold] {_format_time(job.get('start_time'))}")

        result = job.get("result")
        if result and result != ".magnus_result":
            console.print()
            console.rule("[bold green]Result[/bold green]")
            try:
                console.print_json(data=json.loads(result))
            except Exception:
                console.print(result, markup=False, highlight=False)

        action = job.get("action")
        if action and action != ".magnus_action":
            console.print()
            console.rule("[bold yellow]Action[/bold yellow]")
            console.print(action, markup=False, highlight=False)

        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@job_app.command(name="status", context_settings=_JOB_REF_CTX)
def job_status_subcmd(ctx: typer.Context):
    """
    Show detailed status of a job.

    Displays task name, status, GPU count, job type, timestamps, and any
    result or action attached to the job.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus job status -1
      magnus job status <job-id>
    """
    _do_job_status(job_ref=_extract_job_ref(ctx))


def _do_job_logs(job_ref: str, page: int = -1) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        result = api_get_job_logs(resolved_id, page=page)

        logs = result.get("logs", "")
        current_page = result.get("page", 0)
        total_pages = result.get("total_pages", 1)

        console.rule(f"[bold]Job Logs: {resolved_id}[/bold] (Page {current_page + 1}/{total_pages})")
        console.print(logs.replace("\r", "\n"), end="", markup=False, highlight=False)
        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@job_app.command(name="logs", context_settings=_JOB_REF_CTX)
def job_logs_subcmd(
    ctx: typer.Context,
    page: int = typer.Option(-1, "--page", "-p", help="Log page number (-1 for last)"),
):
    """
    Show logs for a job.

    Logs are paginated in ~200KB pages. Defaults to the last page (--page -1).
    Use --page 0 for the first page.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus job logs -1
      magnus job logs -2 --page 0
      magnus job logs <job-id>
    """
    _do_job_logs(job_ref=_extract_job_ref(ctx), page=page)


def _do_job_result(job_ref: str) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        result = api_get_job_result(resolved_id)

        if result is None:
            print_msg("[dim]No result available.[/dim]")
            return

        console.rule(f"[bold green]Result: {resolved_id}[/bold green]")
        try:
            console.print_json(data=json.loads(result))
        except Exception:
            console.print(result, markup=False, highlight=False)
        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@job_app.command(name="result", context_settings=_JOB_REF_CTX)
def job_result_cmd(ctx: typer.Context):
    """
    Show result of a completed job.

    Displays the MAGNUS_RESULT value set by the job. If the result is valid
    JSON, it is pretty-printed; otherwise it is shown as plain text.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus job result -1
      magnus job result <job-id>
    """
    _do_job_result(job_ref=_extract_job_ref(ctx))


def _do_job_action(job_ref: str, execute: bool = False) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        action = api_get_job_action(resolved_id)

        if not action:
            print_msg("[dim]No action available.[/dim]")
            return

        action_text = action.strip()
        if execute:
            print_msg("Executing action...")
            try:
                run_action(action_text)
            except ExecutionError as e:
                print_error(str(e))
                raise typer.Exit(code=1)
        else:
            console.rule(f"[bold yellow]Action: {resolved_id}[/bold yellow]")
            console.print(action_text, markup=False, highlight=False)
            console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@job_app.command(name="action", context_settings=_JOB_REF_CTX)
def job_action_cmd(
    ctx: typer.Context,
    execute: bool = typer.Option(False, "--execute", "-e", help="Execute the action script"),
):
    """
    Show action of a completed job.

    Displays the MAGNUS_ACTION script set by the job. This is a shell
    command that the job wants executed on the client side (e.g., downloading
    files). Use -e to execute it immediately.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus job action -1
      magnus job action -1 -e
      magnus job action <job-id>
    """
    _do_job_action(job_ref=_extract_job_ref(ctx), execute=execute)


def _do_kill_job(job_ref: str, force: bool = False) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)

        if not force:
            confirm = typer.confirm(f"Terminate job {resolved_id}?")
            if not confirm:
                print_msg("Cancelled.")
                return

        result = api_terminate_job(resolved_id)
        new_status = result.get("status", "Unknown")
        print_msg(f"Job [bold]{rich_escape(str(resolved_id))}[/bold] terminated. Status: [magenta]{rich_escape(str(new_status))}[/magenta]")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@job_app.command(name="kill", context_settings=_JOB_REF_CTX)
def job_kill_subcmd(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """
    Terminate a running job.

    Asks for confirmation unless --force is given. Only running or pending
    jobs can be terminated.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus job kill -1
      magnus job kill -1 -f
      magnus job kill <job-id>
    """
    _do_kill_job(job_ref=_extract_job_ref(ctx), force=force)


def _do_signal_job(job_ref: str) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        api_signal_job(resolved_id)
        print_msg(f"SIGTERM sent to job [bold]{rich_escape(str(resolved_id))}[/bold]. Waiting for the process to respond.")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@job_app.command(name="signal", context_settings=_JOB_REF_CTX)
def job_signal_subcmd(ctx: typer.Context):
    """
    Send SIGTERM to a running job without forcibly terminating it.

    The job status is not changed at the magnus side; the user process actually
    receives SIGTERM. With a handler installed, the user code can run its own
    teardown (saving intermediate results / checkpoints, releasing GPU memory,
    closing connections, flushing buffers, etc.); writing $MAGNUS_RESULT and
    calling sys.exit(0) from the handler lets the regular sync loop converge
    the job to Success. Without a handler SIGTERM is a no-op — the user-script
    bash inherits SIG_IGN and propagates it via POSIX exec, so the job keeps
    running; use 'kill' to force-terminate.

    Only Running jobs can be signaled.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus job signal -1
      magnus job signal <job-id>
    """
    _do_signal_job(job_ref=_extract_job_ref(ctx))


# === Metric subcommand ===

_METRIC_DATA_SUFFIXES = {".csv", ".json", ".yaml", ".yml"}
_METRIC_IMAGE_SUFFIXES = {".png"}


def _parse_labels_filter(labels_str: Optional[str]) -> Optional[Dict[str, str]]:
    if not labels_str:
        return None
    try:
        parsed = json.loads(labels_str)
    except json.JSONDecodeError as e:
        raise MagnusError(f"--labels must be valid JSON object: {e}")
    if not isinstance(parsed, dict):
        raise MagnusError("--labels must be a JSON object, e.g. '{\"device\":\"cuda:0\"}'")
    return {str(k): str(v) for k, v in parsed.items()}


def _flatten_metric_point(point: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a metric point's labels for CSV-style output."""
    out: Dict[str, Any] = {
        "step": point.get("step"),
        "value": point.get("value"),
        "time_unix_ms": point.get("time_unix_ms"),
    }
    for k, v in (point.get("labels") or {}).items():
        out[f"label_{k}"] = v
    return out


def _write_points_csv(points: List[Dict[str, Any]], output: Path) -> None:
    import csv
    flat = [_flatten_metric_point(p) for p in points]
    fieldnames: List[str] = ["step", "value", "time_unix_ms"]
    seen = set(fieldnames)
    for row in flat:
        for k in row.keys():
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in flat:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _write_points_json(points: List[Dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"points": points}, indent=2), encoding="utf-8")


def _write_points_yaml(points: List[Dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        _get_yaml_dumper().dump({"points": points}, f)


def _do_job_metric_streams(job_ref: str, fmt: OutputFormat) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        streams = api_get_metric_streams(resolved_id)
    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

    if not streams:
        print_msg("[dim]No metric streams available for this job.[/dim]")
        return

    if fmt == "table":
        table = Table(title=f"Metric streams: {resolved_id}")
        table.add_column("name", style="cyan")
        table.add_column("kind")
        table.add_column("unit")
        table.add_column("step_domain")
        table.add_column("labels")
        table.add_column("points", justify="right")
        for s in streams:
            labels = s.get("labels") or {}
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items())) or "-"
            table.add_row(
                rich_escape(str(s.get("name", ""))),
                rich_escape(str(s.get("kind", ""))),
                rich_escape(str(s.get("unit") or "-")),
                rich_escape(str(s.get("step_domain") or "-")),
                rich_escape(label_str),
                str(s.get("point_count", 0)),
            )
        console.print(table)
    else:
        _output_data(streams, fmt)


def _do_job_metric_points(
    job_ref: str,
    name: str,
    labels: Optional[str],
    step_domain: Optional[str],
    max_points: int,
    output: Optional[Path],
    fmt: OutputFormat,
) -> None:
    try:
        resolved_id = _resolve_job_ref(job_ref)
        labels_dict = _parse_labels_filter(labels)
    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    if output is not None:
        suffix = output.suffix.lower()
        if suffix in _METRIC_IMAGE_SUFFIXES:
            try:
                api_save_metric_chart(
                    resolved_id, name, output,
                    labels=labels_dict, step_domain=step_domain,
                    max_points=max_points,
                )
                print_msg(f"Saved chart to [green]{rich_escape(str(output))}[/green]")
            except MagnusError as e:
                print_error(str(e))
                raise typer.Exit(code=1)
            except Exception as e:
                print_error(f"Unexpected error: {e}")
                raise typer.Exit(code=1)
            return

        if suffix not in _METRIC_DATA_SUFFIXES:
            print_error(
                f"Unsupported output suffix '{suffix}'. "
                f"Use one of: .csv, .json, .yaml, .yml, .png"
            )
            raise typer.Exit(code=1)

    try:
        points = api_get_metric_points(
            resolved_id, name,
            labels=labels_dict, step_domain=step_domain,
            max_points=max_points,
        )
    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

    if output is not None:
        suffix = output.suffix.lower()
        try:
            if suffix == ".csv":
                _write_points_csv(points, output)
            elif suffix == ".json":
                _write_points_json(points, output)
            elif suffix in (".yaml", ".yml"):
                _write_points_yaml(points, output)
        except OSError as e:
            print_error(f"Failed to write {output}: {e}")
            raise typer.Exit(code=1)
        print_msg(f"Wrote {len(points)} points to [green]{rich_escape(str(output))}[/green]")
        return

    _output_data({"name": name, "points": points}, fmt)


def _extract_metric_args(
    ctx: typer.Context,
) -> Tuple[str, Optional[str], Optional[Path], Optional[str], Optional[str], int, Optional[str]]:
    """Parse `magnus job metric <ref> [name]` plus options out of ctx.args.

    Negative indices like `-1` look like options to Click, so the standard
    `_JOB_REF_CTX` (ignore_unknown_options + allow_extra_args) routes them into
    ctx.args. We then walk ctx.args ourselves, collecting positional values
    and matching known flags `-o/--output`, `--labels`, `--step-domain`,
    `--max-points`, `-f/--format`. Both `-o` and `--output` aliases work.
    """
    args = list(ctx.args)
    positional: List[str] = []
    output: Optional[str] = None
    labels: Optional[str] = None
    step_domain: Optional[str] = None
    max_points: int = 2000
    fmt: Optional[str] = None

    def _take_value(flag: str, idx: int) -> Tuple[str, int]:
        if idx + 1 >= len(args):
            print_error(f"Missing value for {flag}")
            raise typer.Exit(code=1)
        return args[idx + 1], idx + 2

    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-o", "--output"):
            output, i = _take_value(tok, i)
        elif tok.startswith("--output="):
            output = tok.split("=", 1)[1]
            i += 1
        elif tok.startswith("-o="):
            output = tok.split("=", 1)[1]
            i += 1
        elif tok == "--labels":
            labels, i = _take_value(tok, i)
        elif tok.startswith("--labels="):
            labels = tok.split("=", 1)[1]
            i += 1
        elif tok == "--step-domain":
            step_domain, i = _take_value(tok, i)
        elif tok.startswith("--step-domain="):
            step_domain = tok.split("=", 1)[1]
            i += 1
        elif tok == "--max-points":
            mp, i = _take_value(tok, i)
            try:
                max_points = int(mp)
            except ValueError:
                print_error(f"--max-points must be an integer, got: {mp}")
                raise typer.Exit(code=1)
        elif tok.startswith("--max-points="):
            try:
                max_points = int(tok.split("=", 1)[1])
            except ValueError:
                print_error(f"--max-points must be an integer, got: {tok}")
                raise typer.Exit(code=1)
            i += 1
        elif tok in ("-f", "--format"):
            fmt, i = _take_value(tok, i)
        elif tok.startswith("--format="):
            fmt = tok.split("=", 1)[1]
            i += 1
        else:
            positional.append(tok)
            i += 1

    if not positional:
        print_error("Missing argument: JOB_REF (job index like -1, -2 or job ID)")
        raise typer.Exit(code=1)

    job_ref = positional[0]
    metric_name = positional[1] if len(positional) > 1 else None

    output_path: Optional[Path] = Path(output) if output else None
    return job_ref, metric_name, output_path, labels, step_domain, max_points, fmt


@job_app.command(name="metric", context_settings=_JOB_REF_CTX)
def job_metric_subcmd(ctx: typer.Context):
    """
    List metric streams or fetch metric points / chart for a job.

    Without METRIC_NAME, lists all available metric streams for the job.
    With METRIC_NAME, prints data points to stdout (YAML by default).

    Use -o / --output to write to file:
      *.csv / *.json / *.yaml -> raw data
      *.png                   -> server-rendered chart

    Other options:
      --labels      JSON object filter, e.g. '{"global_rank":"0"}'
      --step-domain Filter by step_domain
      --max-points  Server-side downsample target (1..10000), default 2000
      -f / --format Output format for stdout: yaml, json, table

    JOB_REF: Job index (-1, -2, ...) or job ID.

    Examples:
      magnus job metric -1
      magnus job metric -1 train.loss
      magnus job metric -1 train.loss --labels '{"global_rank":"0"}'
      magnus job metric -1 train.loss -o loss.csv
      magnus job metric -1 train.loss -o loss.png
      magnus job metric -1 train.loss --output loss.png
    """
    job_ref, metric_name, output, labels, step_domain, max_points, fmt_opt = (
        _extract_metric_args(ctx)
    )
    fmt: OutputFormat = fmt_opt if fmt_opt in ("table", "yaml", "json") else _auto_format()  # type: ignore[assignment]

    if metric_name is None:
        _do_job_metric_streams(job_ref, fmt)
        return

    _do_job_metric_points(
        job_ref=job_ref,
        name=metric_name,
        labels=labels,
        step_domain=step_domain,
        max_points=max_points,
        output=output,
        fmt=fmt,
    )


@job_app.command(
    name="submit",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def job_submit_subcmd(ctx: typer.Context):
    """Submit a job directly (fire & forget)."""
    try:
        cli_config, job_params = _parse_job_args(ctx.args)
        cli_config = apply_cli_defaults(cli_config, command_type="submit")
        _validate_job_params(job_params)

        print_msg(f"Submitting job [bold cyan]{rich_escape(str(job_params['task_name']))}[/bold cyan]...")

        job_id = api_submit_job(
            timeout=cli_config["timeout"],
            **job_params,
        )

        print_msg(f"Job submitted. ID: [green]{job_id}[/green] (use [cyan]-1[/cyan] to reference)")
        print_msg(_job_view_link_msg(job_id))

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

job_submit_subcmd.__doc__ = f"Submit a job directly (fire & forget).\n\n{_SUBMIT_OPTIONS_EPILOG}"


@job_app.command(
    name="execute",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def job_execute_subcmd(ctx: typer.Context):
    """Submit a job and wait for completion."""
    try:
        cli_config, job_params = _parse_job_args(ctx.args)
        cli_config = apply_cli_defaults(cli_config, command_type="run")
        _validate_job_params(job_params)

        print_msg(f"Executing job [bold cyan]{rich_escape(str(job_params['task_name']))}[/bold cyan]...")

        from .. import default_client

        job_id = api_submit_job(**job_params)

        print_msg(f"Job submitted. ID: [green]{job_id}[/green]")

        with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Waiting for job completion..."):
            result = default_client._poll_job_completion(
                job_id,
                timeout=cli_config["timeout"],
                poll_interval=cli_config["poll_interval"],
                execute_action_flag=False,
            )

        console.print("")
        print_msg("Job finished.")

        _display_job_result(result, default_client.last_job_id, cli_config["execute_action"])
        print_msg(_job_view_link_msg(job_id))

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        print_msg("Interrupted by user.")
        raise typer.Exit(code=130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

job_execute_subcmd.__doc__ = (
    "Submit a job and wait for completion.\n\n"
    "Jobs run server-side. If your client disconnects (Ctrl-C, network\n"
    "drop), the job keeps running — do not re-submit. Reconnect with\n"
    "'magnus status <job-id>' and 'magnus job result <job-id>'.\n\n"
    f"{_EXECUTE_OPTIONS_EPILOG}"
)
