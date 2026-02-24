# sdks/python/src/magnus/cli/commands.py
import io
import os
import sys
import json
import signal
import subprocess
import typer
import httpx
from typing import List, Optional, Any, Dict, Tuple, Literal
from pathlib import Path
from rich.console import Console
from rich.theme import Theme
from rich.table import Table
from rich.status import Status
from datetime import datetime
from importlib.metadata import version
from ruamel.yaml import YAML

__version__ = version("magnus-sdk")

from ..exceptions import MagnusError, ExecutionError
from ..actions import execute_action as run_action
from .. import (
    save_site,
    remove_site,
    set_current_site,
    launch_blueprint,
    run_blueprint,
    submit_job as api_submit_job,
    execute_job as api_execute_job,
    call_service,
    custody_file as api_custody_file,
    list_jobs as api_list_jobs,
    get_job as api_get_job,
    get_job_result as api_get_job_result,
    get_job_action as api_get_job_action,
    get_job_logs as api_get_job_logs,
    terminate_job as api_terminate_job,
    get_cluster_stats as api_get_cluster_stats,
    list_blueprints as api_list_blueprints,
    get_blueprint as api_get_blueprint,
    get_blueprint_schema as api_get_blueprint_schema,
    save_blueprint as api_save_blueprint,
    delete_blueprint as api_delete_blueprint,
    list_services as api_list_services,
)

# === UI Setup ===

custom_theme = Theme({
    "magnus.prefix": "blue",
    "magnus.error": "red bold",
    "magnus.success": "green",
})
console = Console(theme=custom_theme)

def print_msg(msg: str, end: str = "\n"):
    console.print(f"[magnus.prefix][Magnus][/magnus.prefix] {msg}", end=end, highlight=False)

def print_error(msg: str):
    console.print(f"[magnus.prefix][Magnus][/magnus.prefix] [magnus.error]Error:[/magnus.error] {msg}", highlight=False)


# === Output Format Helpers ===

OutputFormat = Literal["table", "yaml", "json"]

_yaml_dumper = YAML()
_yaml_dumper.default_flow_style = False


def _auto_format() -> OutputFormat:
    """自动检测输出格式：TTY 用表格，管道用 YAML"""
    return "table" if sys.stdout.isatty() else "yaml"


def _output_data(
    data: Any,
    fmt: OutputFormat,
):
    """统一输出数据"""
    if fmt == "yaml":
        stream = io.StringIO()
        _yaml_dumper.dump(data, stream)
        console.print(stream.getvalue(), end="")
    elif fmt == "json":
        console.print_json(data=data)


# === Signal-Safe Spinner ===

class SignalSafeSpinner:
    """
    Spinner that handles SIGTSTP (ctrl+z) gracefully.
    Stops spinner before suspend, restarts on resume.
    """

    def __init__(
        self,
        message: str,
        spinner: str = "dots",
    ):
        self._message = message
        self._spinner_name = spinner
        self._status: Optional[Status] = None
        self._old_sigtstp = None
        self._old_sigcont = None

    def __enter__(self) -> "SignalSafeSpinner":
        self._status = Status(self._message, console=console, spinner=self._spinner_name)
        self._status.start()

        if os.name != "nt":
            self._old_sigtstp = signal.signal(signal.SIGTSTP, self._handle_sigtstp)
            self._old_sigcont = signal.signal(signal.SIGCONT, self._handle_sigcont)

        return self

    def __exit__(self, *args: Any) -> None:
        if self._status:
            self._status.stop()
            self._status = None

        if os.name != "nt":
            if self._old_sigtstp is not None:
                signal.signal(signal.SIGTSTP, self._old_sigtstp)
            if self._old_sigcont is not None:
                signal.signal(signal.SIGCONT, self._old_sigcont)

    def _handle_sigtstp(self, signum: int, frame: Any) -> None:
        if self._status:
            self._status.stop()
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTSTP)

    def _handle_sigcont(self, signum: int, frame: Any) -> None:
        signal.signal(signal.SIGTSTP, self._handle_sigtstp)
        if self._status:
            self._status.start()


# === Job Index Resolution ===

def _resolve_job_ref(ref: str) -> str:
    """
    解析 job 引用：
    - 负数索引：-1 = 最新，-2 = 第二新，...
    - 否则视为 job_id 原样返回
    """
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


_JOB_REF_CTX = {"ignore_unknown_options": True, "allow_extra_args": True}

def _extract_job_ref(ctx: typer.Context) -> str:
    """Extract job_ref from ctx.args (handles negative indices like -1 that Click misparses as options)."""
    if not ctx.args:
        print_error("Missing argument: JOB_REF (job index like -1, -2 or job ID)")
        raise typer.Exit(code=1)
    return ctx.args[0]


# === Argument Parsing Logic ===

# CLI control parameters have a known schema — convert explicitly, never guess.
_CLI_KEY_TYPES: Dict[str, type] = {
    "timeout": float,
    "poll_interval": float,
    "verbose": bool,
    "preference": bool,
    "execute_action": bool,
    "expire_minutes": int,
    "max_downloads": int,
}


def _coerce_cli_value(key: str, raw: str) -> Any:
    """Convert a raw CLI string to the expected type for a known key."""
    expected = _CLI_KEY_TYPES.get(key)
    if expected is bool:
        return raw.lower() not in ("false", "0", "no")
    if expected is float:
        return float(raw)
    if expected is int:
        return int(raw)
    return raw


def parse_cli_args(args: List[str]) -> Dict[str, Any]:
    """
    解析 CLI 自身的控制参数 (如 --timeout, --verbose)。
    只对 _CLI_KEY_TYPES 中已知的 key 做显式类型转换，未知 key 保持字符串。
    """
    params: Dict[str, Any] = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key.startswith("--"):
            key = key[2:].replace("-", "_")

            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                raw_value = args[i + 1]
                i += 2
            else:
                raw_value = "true"
                i += 1

            params[key] = _coerce_cli_value(key, raw_value)
        else:
            i += 1
    return params

def parse_blueprint_args(args: List[str]) -> Dict[str, Any]:
    """
    [Raw Parser] 用于传递给 Blueprints 的业务参数。
    原则：不猜测，不转换。所有值均保持为字符串，类型转换由后端/蓝图负责。
    重复 key 自动收集为列表，用于 List[T] 参数。
    Example:
      --count 2              -> {"count": "2"}
      --enable               -> {"enable": "true"}
      --files a --files b    -> {"files": ["a", "b"]}
    """
    params: Dict[str, Any] = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key.startswith("--"):
            key = key[2:]
            key = key.replace("-", "_")

            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                value = args[i + 1] # Keep as raw string
                i += 2
            else:
                value = "true"      # Flag defaults to string "true"
                i += 1

            if key in params:
                existing = params[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    params[key] = [existing, value]
            else:
                params[key] = value
        else:
            i += 1
    return params

def partition_args(raw_args: List[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    根据 '--' 防波堤切分参数。
    Left Slice  -> CLI Args (Typed)
    Right Slice -> Blueprint Args (String)
    Default     -> All args belong to Blueprint
    """
    if "--" in raw_args:
        idx = raw_args.index("--")
        cli_slice = raw_args[:idx]
        bp_slice = raw_args[idx + 1:]
    else:
        cli_slice = []
        bp_slice = raw_args

    return parse_cli_args(cli_slice), parse_blueprint_args(bp_slice)

# === Configuration ===

DEFAULT_CLI_CONFIG = {
    "timeout": 10.0,      # HTTP Network Timeout
    "preference": False,  # User Preference
    "verbose": False,     # Debug Mode
    "poll_interval": 2.0, # Polling Interval (Run only)
    "execute_action": True,  # Auto-execute MAGNUS_ACTION (Run only)
    "expire_minutes": 60, # FileSecret TTL (minutes)
    "max_downloads": 1,   # FileSecret max download count
}

def apply_cli_defaults(parsed_cli_args: Dict[str, Any], command_type: str = "submit") -> Dict[str, Any]:
    config = DEFAULT_CLI_CONFIG.copy()

    # 特殊逻辑：Run 模式下若未指定 timeout，默认应为无限等待 (None)，而非 submit 的 10s
    if command_type == "run" and "timeout" not in parsed_cli_args:
        config["timeout"] = None

    config.update(parsed_cli_args)
    return config


# === CLI Options Epilog (for --help) ===
# Commands using allow_extra_args bypass Typer's option registration,
# so we document the options manually in the docstring.

_LAUNCH_OPTIONS_EPILOG = """
CLI Options (before --):
  --timeout FLOAT          HTTP timeout in seconds (default: 10)
  --expire-minutes INT     FileSecret TTL in minutes (default: 60)
  --max-downloads INT      FileSecret max download count (default: 1)
  --preference BOOL        Merge user preference params (default: false)
  --verbose                Print argument routing debug info

Blueprint arguments (after --) are passed to the blueprint function.
Without --, all arguments are routed to the blueprint; CLI options
use default values.
""".strip()

_RUN_OPTIONS_EPILOG = """
CLI Options (before --):
  --timeout FLOAT          Max wait time in seconds (default: infinite)
  --poll-interval FLOAT    Poll interval in seconds (default: 2)
  --execute-action BOOL    Auto-execute MAGNUS_ACTION (default: true)
  --expire-minutes INT     FileSecret TTL in minutes (default: 60)
  --max-downloads INT      FileSecret max download count (default: 1)
  --preference BOOL        Merge user preference params (default: false)
  --verbose                Print argument routing debug info

Blueprint arguments (after --) are passed to the blueprint function.
Without --, all arguments are routed to the blueprint; CLI options
use default values.
""".strip()


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


# === Sub-command groups ===

blueprint_app = typer.Typer(
    name="blueprint",
    help=(
        "Blueprint operations: create, inspect, run, and manage reusable job templates.\n\n"
        "Subcommands:\n"
        "  list      List available blueprints\n"
        "  get       Show blueprint details and code\n"
        "  schema    Show parameter schema (what arguments to pass)\n"
        "  save      Create or update a blueprint from a .py file\n"
        "  delete    Delete a blueprint\n"
        "  launch    Submit a blueprint job (fire & forget)\n"
        "  run       Submit and wait for completion\n\n"
        "Lifecycle: save → schema → launch/run → iterate.\n"
        "Top-level shortcuts: magnus list, magnus launch, magnus run.\n\n"
        "Examples:\n"
        "  magnus blueprint list\n"
        "  magnus blueprint schema my-bp\n"
        "  magnus blueprint run my-bp -- --epochs 10\n"
        "  magnus blueprint save my-bp -t 'My BP' -c bp.py"
    ),
)
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
        "  submit    Submit a job directly (fire & forget)\n"
        "  execute   Submit a job and wait for completion\n\n"
        "Jobs can be referenced by negative index: -1 = newest, -2 = second newest.\n"
        "Top-level shortcuts: magnus jobs, magnus status, magnus logs, magnus kill.\n\n"
        "Examples:\n"
        "  magnus job list\n"
        "  magnus job status -1\n"
        "  magnus job logs -2 --page 0\n"
        "  magnus job kill -1 -f"
    ),
)
app.add_typer(blueprint_app)
app.add_typer(job_app)


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
    console.print(f"  [bold]Current:[/bold]  {effective_name}")
    console.print(f"  [bold]Address:[/bold]  {effective_address}")

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
            console.print(f"    {name}  {sites[name]['address']}{marker}")

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


def _verify_connection(address: str, token: str) -> bool:
    """Verify connectivity by calling GET /api/auth/my-token."""
    try:
        resp = httpx.get(
            f"{address}/api/auth/my-token",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


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

        address = address.rstrip("/")

        with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Verifying connection..."):
            ok = _verify_connection(address, token)

        if ok:
            print_msg("[green]Connection verified.[/green]")
        else:
            print_msg("[yellow]Warning:[/yellow] Could not verify connection. Saving anyway.")

        config_path = save_site(site, address, token, set_current=True)
        print_msg(f"Saved [bold]{site}[/bold] to [cyan]{config_path}[/cyan]")
        _warn_env_overrides()
        return

    # --- Quick switch for existing site ---
    if site and site in sites:
        existing = sites[site]
        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Verifying {site}..."):
            ok = _verify_connection(existing["address"], existing["token"])
        if ok:
            set_current_site(site)
            print_msg(f"[green]Switched to [bold]{site}[/bold][/green] ({existing['address']})")
        else:
            print_msg(f"[yellow]Warning:[/yellow] Could not verify {site}. Switched anyway.")
            set_current_site(site)
        _warn_env_overrides()
        return

    # --- Interactive login ---
    console.print()

    # Site name
    if site:
        name = site
        print_msg(f"Creating new site [bold]{name}[/bold]...")
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
    address = input().strip() or DEFAULT_ADDRESS
    address = address.rstrip("/")

    print_msg("Token: ", end="")
    token = input().strip()
    if not token:
        print_error("Token is required.")
        raise typer.Exit(code=1)

    console.print()
    with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Verifying connection..."):
        ok = _verify_connection(address, token)

    if ok:
        print_msg("[green]Connection verified.[/green]")
    else:
        print_msg("[yellow]Warning:[/yellow] Could not verify connection. Saving anyway.")

    config_path = save_site(name, address, token, set_current=True)
    print_msg(f"Saved [bold]{name}[/bold] to [cyan]{config_path}[/cyan]")
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
    print_msg(f"Removed site [bold]{site}[/bold].")
    if was_current:
        print_msg(f"Switched to [cyan]{new_current}[/cyan].")
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
    "Examples:\n"
    "  magnus run my-bp\n"
    "  magnus run my-bp -- --epochs 10\n"
    "  magnus run my-bp -- --learning-rate 0.001 --batch-size 32\n\n"
    f"{_RUN_OPTIONS_EPILOG}"
)


CLI_RESERVED_KEYS = {"timeout", "verbose", "execute_action"}

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


def parse_call_args(args: List[str]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    解析 call 命令的参数。
    - 有 '--' 防波堤：左边 CLI 参数，右边 payload
    - 无防波堤：timeout/verbose 归 CLI，其余归 payload
    """
    if "--" in args:
        idx = args.index("--")
        cli_slice = args[:idx]
        payload_slice = args[idx + 1:]
        return parse_cli_args(cli_slice), parse_blueprint_args(payload_slice)

    cli_config: Dict[str, Any] = {}
    payload: Dict[str, str] = {}

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

            if key in CLI_RESERVED_KEYS:
                cli_config[key] = _coerce_cli_value(key, value)
            else:
                payload[key] = value
        else:
            i += 1

    return cli_config, payload


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
            console.print(f"[dim]Timeout: {timeout}, Payload: {data}[/dim]")

        print_msg(f"Calling service [bold cyan]{service_id}[/bold cyan]...")

        response = call_service(
            service_id=service_id,
            payload=data,
            timeout=timeout
        )

        if isinstance(response, (dict, list)):
            console.print_json(data=response)
        else:
            console.print(response)

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
  --cpu-count INT           Number of CPUs
  --memory-demand TEXT      Memory limit (e.g. 16G)
  --ephemeral-storage TEXT  Disk limit (e.g. 10G)
  --container-image TEXT    Container image URI
  --runner TEXT             Runner name
  --namespace TEXT          Repository namespace
  --job-type TEXT           Job type (A1/A2/B1/B2)
  --description TEXT        Job description
  --system-entry-command TEXT  System-level setup script
""".strip()

_SUBMIT_OPTIONS_EPILOG = f"""{_JOB_PARAMS_DOC}

CLI options:
  --timeout FLOAT           HTTP timeout in seconds (default: 10)
  --verbose                 Print debug info"""

_EXECUTE_OPTIONS_EPILOG = f"""{_JOB_PARAMS_DOC}

CLI options:
  --timeout FLOAT           Max wait time in seconds (default: infinite)
  --poll-interval FLOAT     Poll interval in seconds (default: 2)
  --execute-action BOOL     Auto-execute MAGNUS_ACTION (default: true)
  --verbose                 Print debug info"""


def _validate_job_params(params: Dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_JOB_KEYS if k not in params]
    if missing:
        raise MagnusError(
            f"Missing required parameters: {', '.join('--' + k.replace('_', '-') for k in missing)}"
        )


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

execute_job_cmd.__doc__ = f"Submit a job and wait for completion.\n\n{_EXECUTE_OPTIONS_EPILOG}"


# === Job Management Commands ===

STATUS_COLORS = {
    "Pending": "yellow",
    "Running": "cyan",
    "Success": "green",
    "Failed": "red",
    "Terminated": "magenta",
}


def _format_time(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_str[:16]


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

    JOB_REF: Job index (-1, -2, ...) or job ID.

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

    JOB_REF: Job index (-1, -2, ...) or job ID.

    Examples:
      magnus kill -1
      magnus kill -1 -f
      magnus kill <job-id>
    """
    _do_kill_job(job_ref=_extract_job_ref(ctx), force=force)


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
        os.kill(ppid, signal.SIGHUP)
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

    JOB_REF: Job index (-1, -2, ...) or job ID.

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
                svc.get("id", "")[:20],
                (svc.get("name") or "-")[:25],
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
        print_msg(f"Saved to [cyan]{result_path}[/cyan]")
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


# =============================================================================
# Blueprint sub-commands: magnus blueprint <verb>
# =============================================================================

@blueprint_app.command(name="list")
def blueprint_list_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of blueprints to fetch"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search by title or ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List available blueprints.

    Displays a table of blueprints with ID, title, creator, and last update
    time. Pipe-friendly: outputs YAML when stdout is not a TTY.

    Examples:
      magnus blueprint list
      magnus blueprint list -l 20
      magnus blueprint list -s "physics"
      magnus blueprint list -f json
    """
    try:
        result = api_list_blueprints(limit=limit, search=search)
        items = result.get("items", [])
        total = result.get("total", 0)

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data({"total": total, "items": items}, fmt)
            return

        if not items:
            print_msg("No blueprints found.")
            return

        table = Table(title=f"Blueprints ({len(items)}/{total})", show_header=True, header_style="bold")
        table.add_column("ID", max_width=25)
        table.add_column("Title", max_width=30)
        table.add_column("Creator", width=15)
        table.add_column("Updated", width=12)

        for bp in items:
            user = bp.get("user") or {}
            table.add_row(
                bp.get("id", "")[:25],
                (bp.get("title") or "-")[:30],
                (user.get("name") or "-")[:15],
                _format_time(bp.get("updated_at")),
            )

        console.print(table)

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@blueprint_app.command(name="get")
def blueprint_get_cmd(
    blueprint_id: str = typer.Argument(..., help="Blueprint ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: yaml, json"),
    code_file: Optional[Path] = typer.Option(None, "--code-file", "-c", help="Export code to a .py file"),
):
    """
    Show blueprint details including code.

    Displays the blueprint's title, description, creator, last update time,
    and full source code. Use -c to export the code to a .py file for
    local editing.

    Examples:
      magnus blueprint get my-bp
      magnus blueprint get my-bp -c my_bp.py
      magnus blueprint get my-bp -f yaml
    """
    try:
        bp = api_get_blueprint(blueprint_id)

        if code_file is not None:
            code = bp.get("code", "")
            code_file.write_text(code, encoding="utf-8")
            print_msg(f"Code exported to [cyan]{code_file}[/cyan]")
            return

        fmt: OutputFormat = format if format in ("yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data(bp, fmt)
            return

        user = bp.get("user") or {}
        console.print()
        console.rule(f"[bold]Blueprint: {bp.get('id', 'N/A')}[/bold]")
        console.print(f"  [bold]Title:[/bold]       {bp.get('title', '-')}")
        console.print(f"  [bold]Description:[/bold] {bp.get('description', '-')}")
        console.print(f"  [bold]Creator:[/bold]     {user.get('name', '-')}")
        console.print(f"  [bold]Updated:[/bold]     {_format_time(bp.get('updated_at'))}")
        console.print()
        console.rule("[bold cyan]Code[/bold cyan]")
        console.print(bp.get("code", ""), markup=False, highlight=False)
        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@blueprint_app.command(name="schema")
def blueprint_schema_cmd(
    blueprint_id: str = typer.Argument(..., help="Blueprint ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: yaml, json"),
):
    """
    Show blueprint parameter schema.

    Outputs the JSON schema describing the blueprint's accepted parameters,
    including types, defaults, and constraints. Useful for understanding
    what arguments to pass to 'magnus run' or 'magnus launch'.

    Examples:
      magnus blueprint schema my-bp
      magnus blueprint schema my-bp -f yaml
    """
    try:
        schema = api_get_blueprint_schema(blueprint_id)
        fmt: OutputFormat = format if format in ("yaml", "json") else "json"
        _output_data(schema, fmt)

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@blueprint_app.command(name="save")
def blueprint_save_cmd(
    blueprint_id: str = typer.Argument(..., help="Blueprint ID"),
    title: str = typer.Option(..., "--title", "-t", help="Blueprint title"),
    description: str = typer.Option("", "--description", "--desc", "-d", help="Blueprint description"),
    code_file: Path = typer.Option(..., "--code-file", "-c", help="Path to Python source file"),
):
    """
    Create or update a blueprint (upsert).

    If the blueprint ID already exists, it is overwritten (update). Otherwise
    a new blueprint is created. The code is read from a local .py file.

    Examples:
      magnus blueprint save my-bp -t "My Blueprint" -c bp.py
      magnus blueprint save my-bp -t "Updated" -d "New desc" -c bp.py
    """
    if not code_file.exists():
        print_error(f"Code file not found: {code_file}")
        raise typer.Exit(code=1)

    try:
        code = code_file.read_text(encoding="utf-8")
        result = api_save_blueprint(
            blueprint_id=blueprint_id,
            title=title,
            description=description,
            code=code,
        )
        print_msg(f"Blueprint [bold cyan]{result.get('id', blueprint_id)}[/bold cyan] saved.")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@blueprint_app.command(name="delete")
def blueprint_delete_cmd(
    blueprint_id: str = typer.Argument(..., help="Blueprint ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """
    Delete a blueprint.

    Asks for confirmation unless --force is given. This action is
    irreversible.

    Examples:
      magnus blueprint delete my-bp
      magnus blueprint delete my-bp -f
    """
    try:
        if not force:
            confirm = typer.confirm(f"Delete blueprint {blueprint_id}?")
            if not confirm:
                print_msg("Cancelled.")
                return

        api_delete_blueprint(blueprint_id)
        print_msg(f"Blueprint [bold]{blueprint_id}[/bold] deleted.")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@blueprint_app.command(
    name="launch",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def blueprint_launch_cmd(
    ctx: typer.Context,
    blueprint_id: str = typer.Argument(..., help="ID of the blueprint"),
):
    """Launch a blueprint job (fire & forget)."""
    try:
        cli_args, bp_args = partition_args(ctx.args)
        cli_config = apply_cli_defaults(cli_args, command_type="submit")

        if cli_config["verbose"]:
            console.rule("[dim]DEBUG: Argument Partition[/dim]")
            console.print(f"[dim]CLI Config (Typed): {cli_config}[/dim]")
            console.print(f"[dim]Blueprint Args (String): {bp_args}[/dim]")
            console.rule()

        print_msg(f"Launching blueprint [bold cyan]{blueprint_id}[/bold cyan]...")

        job_id = launch_blueprint(
            blueprint_id=blueprint_id,
            use_preference=cli_config["preference"],
            expire_minutes=cli_config["expire_minutes"],
            max_downloads=cli_config["max_downloads"],
            timeout=cli_config["timeout"],
            args=bp_args,
        )

        print_msg(f"Job submitted. ID: [green]{job_id}[/green] (use [cyan]-1[/cyan] to reference)")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

blueprint_launch_cmd.__doc__ = (
    "Launch a blueprint job (fire & forget).\n\n"
    "Submits the job and returns immediately with the job ID. Does not\n"
    "wait for completion — use 'magnus blueprint run' to wait.\n\n"
    "Examples:\n"
    "  magnus blueprint launch my-bp\n"
    "  magnus blueprint launch my-bp -- --epochs 10\n\n"
    f"{_LAUNCH_OPTIONS_EPILOG}"
)


@blueprint_app.command(
    name="run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def blueprint_run_cmd(
    ctx: typer.Context,
    blueprint_id: str = typer.Argument(..., help="ID of the blueprint"),
):
    """Execute a blueprint and wait for completion."""
    try:
        cli_args, bp_args = partition_args(ctx.args)
        cli_config = apply_cli_defaults(cli_args, command_type="run")

        if cli_config["verbose"]:
            console.rule("[dim]DEBUG: Argument Partition[/dim]")
            console.print(f"[dim]CLI Config (Typed): {cli_config}[/dim]")
            console.print(f"[dim]Blueprint Args (String): {bp_args}[/dim]")
            console.rule()

        print_msg(f"Running blueprint [bold cyan]{blueprint_id}[/bold cyan]...")

        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Waiting for job completion..."):
            result = run_blueprint(
                blueprint_id=blueprint_id,
                use_preference=cli_config["preference"],
                expire_minutes=cli_config["expire_minutes"],
                max_downloads=cli_config["max_downloads"],
                timeout=cli_config["timeout"],
                poll_interval=cli_config["poll_interval"],
                execute_action=False,
                args=bp_args,
            )

        console.print("")
        print_msg("Job finished.")

        from .. import default_client
        _display_job_result(result, default_client.last_job_id, cli_config["execute_action"])

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        print_msg("Interrupted by user.")
        raise typer.Exit(code=130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

blueprint_run_cmd.__doc__ = (
    "Execute a blueprint and wait for completion.\n\n"
    "Submits the job, polls until it finishes, then displays the result\n"
    "and auto-executes any MAGNUS_ACTION script (disable with\n"
    "--execute-action false).\n\n"
    "Examples:\n"
    "  magnus blueprint run my-bp\n"
    "  magnus blueprint run my-bp -- --epochs 10\n\n"
    f"{_RUN_OPTIONS_EPILOG}"
)


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
                job.get("id", ""),
                (job.get("task_name") or "-")[:30],
                f"[{status_color}]{status}[/{status_color}]",
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
        console.rule(f"[bold]Job: {job.get('id', 'N/A')}[/bold]")
        console.print(f"  [bold]Task:[/bold]    {job.get('task_name', '-')}")
        console.print(f"  [bold]Status:[/bold]  [{status_color}]{status}[/{status_color}]")
        console.print(f"  [bold]GPU:[/bold]     {job.get('gpu_count', 0)}")
        console.print(f"  [bold]Type:[/bold]    {job.get('job_type', '-')}")
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

    JOB_REF: Job index (-1, -2, ...) or job ID.

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

    JOB_REF: Job index (-1, -2, ...) or job ID.

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

    JOB_REF: Job index (-1, -2, ...) or job ID.

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

    JOB_REF: Job index (-1, -2, ...) or job ID.

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
        print_msg(f"Job [bold]{resolved_id}[/bold] terminated. Status: [magenta]{new_status}[/magenta]")

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

    JOB_REF: Job index (-1, -2, ...) or job ID.

    Examples:
      magnus job kill -1
      magnus job kill -1 -f
      magnus job kill <job-id>
    """
    _do_kill_job(job_ref=_extract_job_ref(ctx), force=force)


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

        print_msg(f"Submitting job [bold cyan]{job_params['task_name']}[/bold cyan]...")

        job_id = api_submit_job(
            timeout=cli_config["timeout"],
            **job_params,
        )

        print_msg(f"Job submitted. ID: [green]{job_id}[/green] (use [cyan]-1[/cyan] to reference)")

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

        print_msg(f"Executing job [bold cyan]{job_params['task_name']}[/bold cyan]...")

        with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Waiting for job completion..."):
            result = api_execute_job(
                timeout=cli_config["timeout"],
                poll_interval=cli_config["poll_interval"],
                execute_action=False,
                **job_params,
            )

        console.print("")
        print_msg("Job finished.")

        from .. import default_client
        _display_job_result(result, default_client.last_job_id, cli_config["execute_action"])

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        print_msg("Interrupted by user.")
        raise typer.Exit(code=130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)

job_execute_subcmd.__doc__ = f"Submit a job and wait for completion.\n\n{_EXECUTE_OPTIONS_EPILOG}"


if __name__ == "__main__":
    app()