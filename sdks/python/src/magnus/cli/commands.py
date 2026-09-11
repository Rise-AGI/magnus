# sdks/python/src/magnus/cli/commands.py
"""Magnus CLI entry point: the root Typer app, its callback, the top-level
commands, and the shortcuts that delegate into the per-concern sub-apps."""
import os
import sys
import json
import signal
import subprocess
import typer
import httpx
from importlib.metadata import version, PackageNotFoundError
from typing import List, Optional, Any, Dict, Tuple
from pathlib import Path
from rich.table import Table
from rich.markup import escape as rich_escape

try:
    __version__ = version("magnus-sdk")
except PackageNotFoundError:
    # Robust to the platform's source-only SDK injection (no installed distribution),
    # same as magnus/__init__.py; keeps the CLI importable against injected source.
    __version__ = "0+source"
from ..exceptions import MagnusError
from .. import (
    save_site,
    remove_site,
    set_current_site,
    call_service,
    custody_file as api_custody_file,
    get_cluster_stats as api_get_cluster_stats,
    list_services as api_list_services,
)
from ._shared import (
    OutputFormat,
    SignalSafeSpinner,
    _JOB_REF_CTX,
    _auto_format,
    _extract_job_ref,
    _format_time,
    _output_data,
    console,
    parse_call_args,
    print_error,
    print_msg,
)
from ._jobs import (
    _EXECUTE_OPTIONS_EPILOG,
    _SUBMIT_OPTIONS_EPILOG,
    _do_job_logs,
    _do_job_status,
    _do_kill_job,
    _do_signal_job,
    job_app,
    job_execute_subcmd,
    job_list_cmd,
    job_submit_subcmd,
)
from ._blueprints import (
    _LAUNCH_OPTIONS_EPILOG,
    _RUN_OPTIONS_EPILOG,
    blueprint_app,
    blueprint_launch_cmd,
    blueprint_list_cmd,
    blueprint_run_cmd,
)
from ._skills import skill_app, skill_list_cmd
from ._images import image_app, image_refresh_cmd
from ._local import local_app


# === CLI App Definition ===

def _version_callback(value: bool):
    if value:
        console.print()
        console.print(f"  [bold blue]Magnus SDK[/bold blue] v{__version__}", highlight=False)
        console.print("  [italic dim]An agentic infrastructure automating scientific discoveries.[/italic dim]")
        console.print()
        console.print("  [bold #94070A]PKU Plasma · Rise-AGI[/bold #94070A]")
        console.print("  [dim]© PKU Plasma Lab. All rights reserved.[/dim]")
        console.print()
        raise typer.Exit()


app = typer.Typer(
    name="magnus",
    help=(
        "Magnus CLI — submit jobs, manage blueprints, and monitor your cluster.\n\n"
        "Quick start:\n"
        "  magnus login                  # authenticate\n"
        "  magnus list                   # browse blueprints\n"
        "  magnus run <blueprint_id>     # run a blueprint and wait for result\n"
        "  magnus jobs                   # check recent jobs\n\n"
        "Use 'magnus <command> -h' for details on any command.\n"
        "Use 'magnus blueprint -h' and 'magnus job -h' for grouped operations."
    ),
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def main_callback(
    _version: bool = typer.Option(False, "--version", "-v", "-V", callback=_version_callback, is_eager=True, help="Show version"),
):
    pass


@app.command(name="config")
def show_config():
    """
    Show current SDK configuration and all configured sites.

    Displays the active site name, server address, and any environment variable
    overrides (MAGNUS_ADDRESS, MAGNUS_TOKEN). Also lists all saved sites with
    an arrow marking the current one.

    Examples:
      magnus config
    """
    from ..config import _load_config, DEFAULT_ADDRESS, RESERVED_SITE_NAME

    config = _load_config()
    current = config.get("current")
    sites = config.get("sites", {})

    env_address = os.getenv("MAGNUS_ADDRESS")
    env_token = os.getenv("MAGNUS_TOKEN")

    # Resolution: env → config file current site → default
    if env_address:
        effective_name = "env"
        effective_address = env_address
    elif current and current in sites:
        effective_name = current
        effective_address = sites[current]["address"]
    else:
        effective_name = RESERVED_SITE_NAME
        effective_address = DEFAULT_ADDRESS

    console.print()
    console.print(f"  [bold]Current:[/bold]  {rich_escape(effective_name)}")
    console.print(f"  [bold]Address:[/bold]  {rich_escape(effective_address)}")

    if env_address or env_token:
        overrides = [k for k, v in [("MAGNUS_ADDRESS", env_address), ("MAGNUS_TOKEN", env_token)] if v]
        console.print(f"  [yellow]⚠ {', '.join(overrides)} set via env — overrides config file[/yellow]")

    if sites:
        is_env_override = bool(env_address)
        console.print()
        console.print("  [bold]Sites:[/bold]")
        for name in sorted(sites):
            if name == current:
                marker = " [dim cyan]← fallback[/dim cyan]" if is_env_override else " [cyan]←[/cyan]"
            else:
                marker = ""
            console.print(f"    {rich_escape(name)}  {rich_escape(sites[name]['address'])}{marker}")

    console.print(f"\n  [dim]Default:  {DEFAULT_ADDRESS}[/dim]")
    console.print()


@app.command(name="print")
def print_cmd(
    message: Optional[str] = typer.Argument(None, help="Message to print. Reads from stdin if omitted."),
    no_newline: bool = typer.Option(False, "--no-newline", "-n", help="Do not append a trailing newline"),
    as_json: bool = typer.Option(False, "--json", help="Pretty-print the message as JSON"),
):
    """
    Cross-platform print. Guarantees consistent UTF-8 output on Linux/macOS/Windows.
    Serves as the standard I/O primitive inside Magnus Action scripts.

    Examples:
      magnus print "Hello World"
      magnus print --no-newline "progress: "
      magnus print --json '{"status": "ok", "count": 42}'
      echo '{"a":1}' | magnus print --json
    """
    text = message if message is not None else sys.stdin.read()

    if as_json:
        try:
            obj = json.loads(text)
            console.print_json(data=obj)
        except json.JSONDecodeError:
            print_error(f"Invalid JSON: {text[:120]}")
            raise typer.Exit(code=1)
        return

    end = "" if no_newline else "\n"
    sys.stdout.write(text + end)
    sys.stdout.flush()


# === Login ===


def _try_connect(address: str, token: str) -> bool:
    try:
        resp = httpx.get(
            f"{address}/api/auth/my-token",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _verify_connection(address: str, token: str) -> Tuple[bool, str]:
    """Verify connectivity, auto-detecting scheme if not provided.
    IP addresses try http first (LAN), domain names try https first.
    Returns (success, resolved_address).
    """
    from ..config import normalize_address, _looks_like_ip

    address = address.strip().rstrip("/")
    if address.startswith(("http://", "https://")):
        return (_try_connect(address, token), address)

    # 按优先级尝试两种协议
    if _looks_like_ip(address):
        candidates = [f"http://{address}", f"https://{address}"]
    else:
        candidates = [f"https://{address}", f"http://{address}"]

    for url in candidates:
        if _try_connect(url, token):
            return (True, url)

    return (False, candidates[0])


def _warn_env_overrides():
    """Warn if environment variables override config file settings."""
    overrides = [k for k in ["MAGNUS_ADDRESS", "MAGNUS_TOKEN"] if os.getenv(k)]
    if overrides:
        names = ", ".join(overrides)
        print_msg(
            f"[yellow]⚠ {names} is set via environment variable, which takes "
            f"precedence over config file. Consider removing it from your "
            f"shell profile to use site switching.[/yellow]"
        )


@app.command(name="login")
def login_cmd(
    site: Optional[str] = typer.Argument(None, help="Site name"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Server address"),
    token: Optional[str] = typer.Option(None, "--token", "-t", help="Trust token"),
):
    """
    Login to a Magnus site.

    Interactive mode (no flags): prompts for site name, address, and token.
    Non-interactive mode (site + --address + --token): saves directly, no prompts.
    Quick switch (site name only): switch to an existing site.
    Special: 'magnus login default' switches to the hardcoded default site.

    Examples:
      magnus login                                       # interactive
      magnus login prod                                  # switch to 'prod'
      magnus login default                               # switch to default
      magnus login prod -a http://host:8017 -t sk-xxx    # non-interactive
    """
    from ..config import _load_config, DEFAULT_ADDRESS, DEFAULT_TOKEN, RESERVED_SITE_NAME

    # --- Handle 'magnus login default' ---
    if site == RESERVED_SITE_NAME:
        set_current_site(None)
        print_msg(f"Switched to [cyan]{RESERVED_SITE_NAME}[/cyan] ({DEFAULT_ADDRESS})")
        _warn_env_overrides()
        return

    config = _load_config()
    sites = config.get("sites", {})

    # --- Non-interactive mode: any of --address or --token provided ---
    if address or token:
        if not address or not token or not site:
            print_error("Non-interactive login requires: magnus login <site> --address <url> --token <token>")
            raise typer.Exit(code=1)

        address = address.strip().rstrip("/")

        with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Verifying connection..."):
            ok, address = _verify_connection(address, token)

        if ok:
            print_msg("[green]Connection verified.[/green]")
        else:
            print_msg("[yellow]Warning:[/yellow] Could not verify connection. Saving anyway.")

        config_path = save_site(site, address, token, set_current=True)
        print_msg(f"Saved [bold]{rich_escape(site)}[/bold] to [cyan]{rich_escape(str(config_path))}[/cyan]")
        _warn_env_overrides()
        return

    # --- Quick switch for existing site ---
    if site and site in sites:
        existing = sites[site]
        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Verifying {site}..."):
            ok, _ = _verify_connection(existing["address"], existing["token"])
        if ok:
            set_current_site(site)
            print_msg(f"[green]Switched to [bold]{rich_escape(site)}[/bold][/green] ({rich_escape(existing['address'])})")
        else:
            print_msg(f"[yellow]Warning:[/yellow] Could not verify {rich_escape(site)}. Switched anyway.")
            set_current_site(site)
        _warn_env_overrides()
        return

    # --- Interactive login ---
    console.print()

    # Site name
    if site:
        name = site
        print_msg(f"Creating new site [bold]{rich_escape(name)}[/bold]...")
    else:
        print_msg("Site name: ", end="")
        name = input().strip()
        if not name:
            print_error("Site name is required.")
            raise typer.Exit(code=1)

    if name == RESERVED_SITE_NAME:
        print_error(f"'{RESERVED_SITE_NAME}' is reserved. It refers to the hardcoded default site.")
        raise typer.Exit(code=1)

    print_msg(f"Address [{DEFAULT_ADDRESS}]: ", end="")
    address = (input().strip() or DEFAULT_ADDRESS).strip().rstrip("/")

    print_msg("Token: ", end="")
    token = input().strip()
    if not token:
        print_error("Token is required.")
        raise typer.Exit(code=1)

    console.print()
    with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Verifying connection..."):
        ok, address = _verify_connection(address, token)

    if ok:
        print_msg("[green]Connection verified.[/green]")
    else:
        print_msg("[yellow]Warning:[/yellow] Could not verify connection. Saving anyway.")

    config_path = save_site(name, address, token, set_current=True)
    print_msg(f"Saved [bold]{rich_escape(name)}[/bold] to [cyan]{rich_escape(str(config_path))}[/cyan]")
    _warn_env_overrides()
    console.print()


@app.command(name="logout")
def logout_cmd(
    site: str = typer.Argument(..., help="Site name to remove"),
):
    """
    Remove a configured site.

    If the removed site is the current one, falls back to the alphabetically
    first remaining site, or 'default' if none remain.

    Examples:
      magnus logout dev
    """
    from ..config import _load_config, RESERVED_SITE_NAME

    if site == RESERVED_SITE_NAME:
        print_error(f"Cannot remove '{RESERVED_SITE_NAME}'. It is the hardcoded fallback.")
        raise typer.Exit(code=1)

    config = _load_config()
    if site not in config.get("sites", {}):
        print_error(f"Site '{site}' not found.")
        raise typer.Exit(code=1)

    was_current = config.get("current") == site
    new_current = remove_site(site)
    print_msg(f"Removed site [bold]{rich_escape(site)}[/bold].")
    if was_current:
        print_msg(f"Switched to [cyan]{rich_escape(new_current)}[/cyan].")
    console.print()


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def launch(
    ctx: typer.Context,
    blueprint_id: str = typer.Argument(..., help="ID of the blueprint"),
):
    """Launch a blueprint job (Fire & Forget)."""
    blueprint_launch_cmd(ctx, blueprint_id)

launch.__doc__ = (
    "Launch a blueprint job (Fire & Forget).\n\n"
    "Shortcut for 'magnus blueprint launch'. Submits the job and returns\n"
    "immediately with the job ID. Does not wait for completion — use\n"
    "'magnus run' if you need to wait.\n\n"
    "Examples:\n"
    "  magnus launch my-bp\n"
    "  magnus launch my-bp -- --epochs 10\n"
    "  magnus launch my-bp -- --learning-rate 0.001 --batch-size 32\n\n"
    f"{_LAUNCH_OPTIONS_EPILOG}"
)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: typer.Context,
    blueprint_id: str = typer.Argument(..., help="ID of the blueprint"),
):
    """Execute a blueprint and wait for completion."""
    blueprint_run_cmd(ctx, blueprint_id)

run.__doc__ = (
    "Execute a blueprint and wait for completion.\n\n"
    "Shortcut for 'magnus blueprint run'. Submits the job, polls until\n"
    "it finishes, then displays the result and auto-executes any\n"
    "MAGNUS_ACTION script (disable with --execute-action false).\n\n"
    "Jobs run server-side. If your client disconnects (Ctrl-C, network\n"
    "drop), the job keeps running — do not re-submit. Reconnect with\n"
    "'magnus status <job-id>' and 'magnus job result <job-id>'.\n\n"
    "Examples:\n"
    "  magnus run my-bp\n"
    "  magnus run my-bp -- --epochs 10\n"
    "  magnus run my-bp -- --learning-rate 0.001 --batch-size 32\n\n"
    f"{_RUN_OPTIONS_EPILOG}"
)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def call(
    ctx: typer.Context,
    service_id: str = typer.Argument(..., help="ID of the service"),
    source: Optional[str] = typer.Argument(None, help="Optional: '@file.json' or '-' for stdin"),
):
    """
    Call a managed service via RPC.

    Examples:
      magnus call my-service --prompt "hello" --max_tokens 100
      magnus call my-service @payload.json
      echo '{"x":1}' | magnus call my-service -
    """
    try:
        cli_config, payload_args = parse_call_args(ctx.args)

        timeout = cli_config.get("timeout", 60.0)
        verbose = cli_config.get("verbose", False)

        data: Dict[str, Any] = {}

        if source:
            if source == "-":
                if sys.stdin.isatty():
                    print_msg("Reading payload from stdin...")
                content = sys.stdin.read()
                data = json.loads(content) if content.strip() else {}

            elif source.startswith("@"):
                filepath = Path(source[1:])
                if not filepath.exists():
                    raise typer.BadParameter(f"Payload file not found: {filepath}")
                content = filepath.read_text(encoding="utf-8")
                data = json.loads(content)

            else:
                raise typer.BadParameter(f"Unknown source format: {source}. Use @file.json or -")

        if payload_args:
            data.update(payload_args)

        if verbose:
            console.print(f"[dim]Timeout: {timeout}, Payload: {rich_escape(repr(data))}[/dim]")

        print_msg(f"Calling service [bold cyan]{rich_escape(service_id)}[/bold cyan]...")

        response = call_service(
            service_id=service_id,
            payload=data,
            timeout=timeout
        )

        if isinstance(response, (dict, list)):
            console.print_json(data=response)
        else:
            console.print(response, markup=False, highlight=False)

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except json.JSONDecodeError as e:
        print_error(f"Invalid JSON: {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


# === Direct Job Submission Commands ===

@app.command(
    name="submit",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def submit_job_cmd(ctx: typer.Context):
    """Submit a job directly (Fire & Forget)."""
    job_submit_subcmd(ctx)

submit_job_cmd.__doc__ = f"Submit a job directly (Fire & Forget).\n\n{_SUBMIT_OPTIONS_EPILOG}"


@app.command(
    name="execute",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def execute_job_cmd(ctx: typer.Context):
    """Submit a job and wait for completion."""
    job_execute_subcmd(ctx)

execute_job_cmd.__doc__ = (
    "Submit a job and wait for completion.\n\n"
    "Jobs run server-side. If your client disconnects (Ctrl-C, network\n"
    "drop), the job keeps running — do not re-submit. Reconnect with\n"
    "'magnus status <job-id>' and 'magnus job result <job-id>'.\n\n"
    f"{_EXECUTE_OPTIONS_EPILOG}"
)


# === Job Management Commands ===

@app.command(name="jobs")
def list_jobs_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of jobs to fetch"),
    name: Optional[str] = typer.Option(None, "--name", "-n", "--search", "-s", help="Search by task name or job ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List recent jobs.

    Shortcut for 'magnus job list'. Displays a table of recent jobs with
    index, ID, task name, status, GPU count, and creation time.
    Use negative indices (-1, -2, ...) to reference jobs in other commands.
    Indices are shared across terminals and shift as new jobs arrive; use the job ID for stability.

    Examples:
      magnus jobs
      magnus jobs -l 20
      magnus jobs -s "training"
    """
    job_list_cmd(limit=limit, name=name, format=format)


@app.command(name="status", context_settings=_JOB_REF_CTX)
def job_status_cmd(
    ctx: typer.Context,
):
    """
    Show detailed status of a job.

    Shortcut for 'magnus job status'. Displays task name, status, GPU count,
    job type, timestamps, and any result or action attached to the job.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus status -1
      magnus status <job-id>
    """
    _do_job_status(job_ref=_extract_job_ref(ctx))


@app.command(name="kill", context_settings=_JOB_REF_CTX)
def kill_job_cmd(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """
    Terminate a running job.

    Shortcut for 'magnus job kill'. Asks for confirmation unless --force is
    given. Only running or pending jobs can be terminated.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus kill -1
      magnus kill -1 -f
      magnus kill <job-id>
    """
    _do_kill_job(job_ref=_extract_job_ref(ctx), force=force)


@app.command(name="signal", context_settings=_JOB_REF_CTX)
def signal_job_cmd(ctx: typer.Context):
    """
    Send SIGTERM to a running job without forcibly terminating it.

    Shortcut for 'magnus job signal'. SIGTERM is delivered to the user process:
    code with a SIGTERM handler can run its own teardown (NCCL teardown, CUDA
    cleanup, checkpointing) and converge the job to Success by writing
    $MAGNUS_RESULT and calling sys.exit(0); code without a handler treats
    SIGTERM as a no-op (the user-script bash inherits SIG_IGN and propagates
    it via POSIX exec), so the job keeps running — use 'kill' to force-kill.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus signal -1
      magnus signal <job-id>
    """
    _do_signal_job(job_ref=_extract_job_ref(ctx))


# === Session Management Commands ===

TARGET_DEBUG_JOB_NAME = "Magnus Debug"


def _get_debug_jobs(user: str) -> List[str]:
    """获取当前用户的所有 Magnus Debug 任务 ID（按时间倒序）"""
    try:
        result = subprocess.run(
            ["squeue", "-u", user, "-n", TARGET_DEBUG_JOB_NAME, "--sort=-i", "-h", "-o", "%i"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        return [jid.strip() for jid in result.stdout.strip().split("\n") if jid.strip()]
    except Exception:
        return []


def _srun_connect(job_id: str, message: str) -> int:
    """通过 srun 连接到指定任务"""
    welcome = f'echo -e "\\033[0;34m[Magnus]\\033[0m {message}";'
    cmd = ["srun", "--jobid", job_id, "--overlap", "--pty", "bash", "-c", f'{welcome} exec bash -l']

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=subprocess.PIPE,
        )
        _, stderr = proc.communicate()

        if stderr:
            filtered = "\n".join(
                line for line in stderr.decode().split("\n")
                if "Hangup" not in line and line.strip()
            )
            if filtered:
                sys.stderr.write(filtered + "\n")

        return proc.returncode
    except Exception as e:
        print_error(f"Failed to connect: {e}")
        return 1


@app.command(name="connect")
def connect_cmd(
    job_id: Optional[str] = typer.Argument(None, help="Job ID to connect (optional, auto-detect if omitted)"),
):
    """
    Connect to a running Magnus Debug session via srun.

    Examples:
      magnus connect           # auto-detect and connect to latest debug job
      magnus connect 12345     # connect to specific SLURM job
    """
    import shutil
    if not shutil.which("srun"):
        print_error("srun not found. This command requires a SLURM environment.")
        raise typer.Exit(code=1)

    slurm_job_id = os.environ.get("SLURM_JOB_ID")
    if slurm_job_id:
        print_msg(f"Already in a Magnus session (Job ID: {slurm_job_id}).")
        raise typer.Exit(code=0)

    if job_id is not None:
        if not job_id.isdigit():
            print_error("Invalid Job ID format. Must be numeric.")
            raise typer.Exit(code=1)

        ret = _srun_connect(job_id, "Connected.")
        raise typer.Exit(code=ret)

    current_user = os.environ.get("USER", "")
    if not current_user:
        print_error("Cannot determine current user.")
        raise typer.Exit(code=1)

    jobs = _get_debug_jobs(current_user)

    if not jobs:
        print_msg(f"No active '{TARGET_DEBUG_JOB_NAME}' sessions found.")
        console.print("         Please submit a debug job first.", highlight=False)
        raise typer.Exit(code=1)

    if len(jobs) == 1:
        ret = _srun_connect(jobs[0], "Connected.")
        raise typer.Exit(code=ret)

    target_id = jobs[0]
    other_ids = ", ".join(jobs[1:])
    message = f"Connected to latest ({target_id}). Other active: {other_ids}"
    ret = _srun_connect(target_id, message)
    raise typer.Exit(code=ret)


@app.command(name="disconnect")
def disconnect_cmd():
    """
    Disconnect from the current Magnus Debug session.

    This command sends SIGHUP to the parent process, terminating the srun session.
    Only works when inside a Magnus session (SLURM_JOB_ID is set).
    """
    slurm_job_id = os.environ.get("SLURM_JOB_ID")

    if not slurm_job_id:
        print_msg("Not in a Magnus session (no SLURM_JOB_ID). Nothing to disconnect.")
        raise typer.Exit(code=1)

    print_msg("Disconnected.")

    ppid = os.getppid()
    try:
        # SIGHUP POSIX-only；该路径仅在 SLURM session 里触发，运行时只走 Linux
        os.kill(ppid, signal.SIGHUP)  # type: ignore[attr-defined]
    except OSError as e:
        print_error(f"Failed to send SIGHUP: {e}")
        raise typer.Exit(code=1)


# === New Commands ===

@app.command(name="logs", context_settings=_JOB_REF_CTX)
def job_logs_cmd(
    ctx: typer.Context,
    page: int = typer.Option(-1, "--page", "-p", help="Log page number (-1 for last)"),
):
    """
    Show logs for a job.

    Shortcut for 'magnus job logs'. Logs are paginated in ~200KB pages.
    Defaults to the last page (--page -1). Use --page 0 for the first page.

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

    Examples:
      magnus logs -1
      magnus logs -1 --page 0
      magnus logs <job-id>
    """
    _do_job_logs(job_ref=_extract_job_ref(ctx), page=page)


@app.command(name="cluster")
def cluster_status_cmd(
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    Show cluster resource status.

    Displays GPU totals (total / free / used), GPU model, and counts of
    running and pending jobs. Pipe-friendly: outputs YAML when stdout is
    not a TTY.

    Examples:
      magnus cluster
      magnus cluster -f json
      magnus cluster -f yaml | yq '.resources.free'
    """
    try:
        result = api_get_cluster_stats()
        resources = result.get("resources", {})
        running_jobs = result.get("running_jobs", [])
        pending_jobs = result.get("pending_jobs", [])
        total_running = result.get("total_running", len(running_jobs))
        total_pending = result.get("total_pending", len(pending_jobs))

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data(result, fmt)
            return

        console.print()
        console.rule(f"[bold]{resources.get('node', 'Cluster')}[/bold]")
        console.print(f"  [bold]GPU Model:[/bold] {resources.get('gpu_model', '-')}")
        console.print(f"  [bold]Total:[/bold]     {resources.get('total', 0)}")
        console.print(f"  [bold]Free:[/bold]      [green]{resources.get('free', 0)}[/green]")
        console.print(f"  [bold]Used:[/bold]      [yellow]{resources.get('used', 0)}[/yellow]")
        console.print()
        console.print(f"  [bold]Running Jobs:[/bold] {total_running}")
        console.print(f"  [bold]Pending Jobs:[/bold] {total_pending}")
        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(name="list")
def list_blueprints_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of blueprints to fetch"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search by title or ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List available blueprints.

    Shortcut for 'magnus blueprint list'. Displays a table of blueprints
    with ID, title, creator, and last update time. For the full set of
    blueprint operations, see 'magnus blueprint -h'.

    Examples:
      magnus list
      magnus list -l 20
      magnus list -s "physics"
    """
    blueprint_list_cmd(limit=limit, search=search, format=format)


@app.command(name="services")
def list_services_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of services to fetch"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search by name or ID"),
    active: bool = typer.Option(False, "--active", "-a", help="Show only active services"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List managed services.

    Displays a table of services with ID, name, active status, GPU count,
    and last update time. Use --active to filter to running services only.
    To call a service, use 'magnus call <service-id>'.

    Examples:
      magnus services
      magnus services --active
      magnus services -l 20 -f json
    """
    try:
        result = api_list_services(limit=limit, search=search, active_only=active)
        items = result.get("items", [])
        total = result.get("total", 0)

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data({"total": total, "items": items}, fmt)
            return

        if not items:
            print_msg("No services found.")
            return

        table = Table(title=f"Services ({len(items)}/{total})", show_header=True, header_style="bold")
        table.add_column("ID", max_width=20)
        table.add_column("Name", max_width=25)
        table.add_column("Active", width=6)
        table.add_column("GPU", width=4)
        table.add_column("Updated", width=12)

        for svc in items:
            is_active = svc.get("is_active", False)
            active_str = "[green]✓[/green]" if is_active else "[dim]-[/dim]"
            table.add_row(
                rich_escape(svc.get("id", "")[:20]),
                rich_escape((svc.get("name") or "-")[:25]),
                active_str,
                str(svc.get("gpu_count", 0)),
                _format_time(svc.get("updated_at")),
            )

        console.print(table)

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(name="send")
def send_cmd(
    path: str = typer.Argument(..., help="File or folder to send"),
    expire_minutes: int = typer.Option(60, "--expire-minutes", "-t", help="Time-to-live in minutes"),
    max_downloads: Optional[int] = typer.Option(1, "--max-downloads", "-d", help="Max download count (default: 1)"),
):
    """
    Send a file or folder via Magnus server.

    Examples:
      magnus send data.csv
      magnus send ./my_folder
      magnus send data.csv --max-downloads 3
    """
    target = Path(path)
    if not target.exists():
        print_error(f"Path does not exist: {path}")
        raise typer.Exit(code=1)

    try:
        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Uploading..."):
            new_secret = api_custody_file(
                path=str(target.resolve()),
                expire_minutes=expire_minutes,
                max_downloads=max_downloads,
            )

        console.print()
        print_msg(f"On the other computer run:")
        console.print(f"    [cyan]magnus receive {new_secret}[/cyan]")
        console.print()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(name="receive")
def receive_cmd(
    secret: str = typer.Argument(..., help="File secret code"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Target path (rename/move after receive)"),
):
    """
    Receive a file or folder.

    Examples:
      magnus receive magnus-secret:7919-calm-boat-fire
      magnus receive 7919-calm-boat-fire
      magnus receive 7919-calm-boat-fire -o my_data.csv
    """
    from ..http_download import download_file as _download_file
    try:
        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Downloading..."):
            result_path = _download_file(secret, target_path=output)
        print_msg(f"Saved to [cyan]{rich_escape(str(result_path))}[/cyan]")
    except KeyboardInterrupt:
        pass
    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)


@app.command(name="custody")
def custody_cmd(
    path: str = typer.Argument(..., help="File or folder to custody via server relay"),
    expire_minutes: int = typer.Option(60, "--expire-minutes", "-t", help="Time-to-live in minutes for the custodied file"),
    max_downloads: Optional[int] = typer.Option(None, "--max-downloads", "-d", help="Max download count (default: unlimited)"),
):
    """
    Custody a file: upload to server for download by others.

    Returns a new file_secret that anyone with access can use to download.

    Examples:
      magnus custody results.csv
      magnus custody ./output_dir --expire-minutes 120
      magnus custody data.csv --max-downloads 5
    """
    target = Path(path)
    if not target.exists():
        print_error(f"Path does not exist: {path}")
        raise typer.Exit(code=1)

    try:
        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Uploading..."):
            new_secret = api_custody_file(
                path=str(target.resolve()),
                expire_minutes=expire_minutes,
                max_downloads=max_downloads,
            )

        console.print()
        print_msg(f"File custodied successfully. Expires in {expire_minutes} min.")
        console.print()
        print_msg(f"Download: [cyan]magnus receive {new_secret}[/cyan]")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


# Top-level shortcut: magnus skills
@app.command(name="skills")
def list_skills_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of skills to fetch"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search by title or ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List available skills.

    Shortcut for 'magnus skill list'. For the full set of skill operations,
    see 'magnus skill -h'.

    Examples:
      magnus skills
      magnus skills -l 20
      magnus skills -s "coding"
    """
    skill_list_cmd(limit=limit, search=search, format=format)


# Top-level shortcut: magnus refresh <image_id>
@app.command(name="refresh")
def refresh_cmd(
    image_id: int = typer.Argument(..., help="Image ID to refresh (from 'magnus image list')"),
):
    """
    Re-pull a cached image (shortcut for 'magnus image refresh').

    Examples:
      magnus refresh 3
    """
    image_refresh_cmd(image_id=image_id)


app.add_typer(blueprint_app)
app.add_typer(job_app)
app.add_typer(skill_app)
app.add_typer(image_app)
app.add_typer(local_app)


if __name__ == "__main__":
    app()
