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
    submit_job as api_submit_job,
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
    list_skills as api_list_skills,
    get_skill as api_get_skill,
    save_skill as api_save_skill,
    delete_skill as api_delete_skill,
    list_images as api_list_images,
    pull_image as api_pull_image,
    refresh_image as api_refresh_image,
    remove_image as api_remove_image,
)

# === UI Setup ===

custom_theme = Theme({
    "magnus.prefix": "blue",
    "magnus.error": "red bold",
    "magnus.success": "green",
})
console = Console(theme=custom_theme)
err_console = Console(theme=custom_theme, stderr=True)

def print_msg(msg: str, end: str = "\n"):
    err_console.print(f"[magnus.prefix][Magnus][/magnus.prefix] {msg}", end=end, highlight=False)

def print_error(msg: str):
    err_console.print(f"[magnus.prefix][Magnus][/magnus.prefix] [magnus.error]Error:[/magnus.error] {msg or 'Unknown error'}", highlight=False)


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


def _show_blueprint_help(blueprint_id: str) -> None:
    """Fetch blueprint schema from server and display parameter help."""
    try:
        schema = api_get_blueprint_schema(blueprint_id)
    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    bp = None
    try:
        bp = api_get_blueprint(blueprint_id)
    except Exception:
        pass

    console.print()
    title = bp.get("title", blueprint_id) if bp else blueprint_id
    desc = bp.get("description", "") if bp else ""
    console.print(f"[bold cyan]{title}[/bold cyan]  [dim]({blueprint_id})[/dim]")
    if desc:
        console.print(f"[dim]{desc}[/dim]")
    console.print()

    if not schema:
        console.print("[dim]This blueprint takes no parameters.[/dim]")
        raise typer.Exit(0)

    console.print("[bold]Parameters:[/bold]")
    for param in schema:
        key = param.get("key", "?")
        ptype = param.get("type", "unknown")
        default = param.get("default")
        desc_text = param.get("description", "")
        is_optional = param.get("is_optional", False)
        is_list = param.get("is_list", False)

        options = param.get("options") or []
        if ptype == "select" and options:
            values = [str(o["value"]) for o in options]
            type_label = " | ".join(f'"{v}"' for v in values)
        else:
            type_label = ptype
        if is_list:
            type_label = f"List[{type_label}]"
        if is_optional:
            type_label = f"Optional[{type_label}]"

        flag = f"--{key.replace('_', '-')}"
        if default is not None:
            header = f"  [green]{flag}[/green] [dim]{type_label}[/dim]  [dim](default: {default!r})[/dim]"
        else:
            header = f"  [green]{flag}[/green] [dim]{type_label}[/dim]"
        console.print(header)

        if desc_text:
            console.print(f"      [dim]{desc_text}[/dim]")
        if ptype == "select" and options:
            for o in options:
                opt_desc = o.get("description")
                if opt_desc:
                    console.print(f"      [dim]{o['value']}: {opt_desc}[/dim]")

    console.print()
    console.print("[dim]Usage: magnus run {id} -- {params}[/dim]".format(id=blueprint_id, params=" ".join(f"--{p['key'].replace('_', '-')} VALUE" for p in schema if not p.get("is_optional"))))
    raise typer.Exit(0)


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

Per-blueprint help:
  magnus launch <blueprint-id> --help    Show parameters for this blueprint
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

Per-blueprint help:
  magnus run <blueprint-id> --help       Show parameters for this blueprint
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
        "Indices are shared across terminals and shift as new jobs arrive; use the job ID for stability.\n\n"
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
skill_app = typer.Typer(
    name="skill",
    help=(
        "Skill operations: create, inspect, and manage reusable knowledge packs.\n\n"
        "A skill is a named collection of files (SKILL.md + optional code/data)\n"
        "that can be loaded by the Explorer agent to extend its capabilities.\n\n"
        "Subcommands:\n"
        "  list      List available skills\n"
        "  get       Show skill details and files\n"
        "  save      Create or update a skill from a local directory\n"
        "  delete    Delete a skill\n\n"
        "Lifecycle: create a directory with SKILL.md → save → iterate.\n"
        "Top-level shortcut: magnus skills.\n\n"
        "Examples:\n"
        "  magnus skill list\n"
        "  magnus skill get my-skill\n"
        "  magnus skill save my-skill -t 'My Skill' ./skill_dir\n"
        "  magnus skill delete my-skill"
    ),
)
app.add_typer(skill_app)
image_app = typer.Typer(
    name="image",
    help=(
        "Image cache operations: list, pull, refresh, and remove cached container images.\n\n"
        "When you push a new version to an existing tag, use 'refresh' to update\n"
        "the local cache. Use 'pull' to add a new image to the cache.\n\n"
        "Subcommands:\n"
        "  list      List cached images with sizes and owners\n"
        "  pull      Pull a new image into the cache\n"
        "  refresh   Re-pull a cached image to update it\n"
        "  remove    Remove a cached image from the cluster\n\n"
        "Top-level shortcut: magnus refresh."
    ),
)
app.add_typer(image_app)


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
        print_msg(f"Saved [bold]{site}[/bold] to [cyan]{config_path}[/cyan]")
        _warn_env_overrides()
        return

    # --- Quick switch for existing site ---
    if site and site in sites:
        existing = sites[site]
        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Verifying {site}..."):
            ok, _ = _verify_connection(existing["address"], existing["token"])
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
    "Jobs run server-side. If your client disconnects (Ctrl-C, network\n"
    "drop), the job keeps running — do not re-submit. Reconnect with\n"
    "'magnus status <job-id>' and 'magnus job result <job-id>'.\n\n"
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
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Export as YAML blueprint file"),
):
    """
    Show blueprint details including code.

    Use -o to export as a YAML blueprint file (title/description/code),
    or -c to export code only to a .py file.

    Examples:
      magnus blueprint get my-bp
      magnus blueprint get my-bp -o blueprint.yaml
      magnus blueprint get my-bp -c my_bp.py
      magnus blueprint get my-bp -f yaml
    """
    try:
        bp = api_get_blueprint(blueprint_id)

        if output is not None:
            from ..client import serialize_blueprint_yaml
            yaml_str = serialize_blueprint_yaml(
                title=bp.get("title", ""),
                description=bp.get("description", ""),
                code=bp.get("code", ""),
            )
            output.write_text(yaml_str, encoding="utf-8")
            print_msg(f"Blueprint exported to [cyan]{output}[/cyan]")
            return

        if code_file is not None:
            code = bp.get("code", "")
            code_file.write_text(code, encoding="utf-8")
            print_msg(f"Code exported to [cyan]{code_file}[/cyan]")
            return

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

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
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Blueprint title"),
    description: str = typer.Option("", "--description", "--desc", "-d", help="Blueprint description"),
    code_file: Optional[Path] = typer.Option(None, "--code-file", "-c", help="Path to Python source file"),
    file: Optional[Path] = typer.Option(None, "--file", help="Path to YAML blueprint file (title/description/code)"),
):
    """
    Create or update a blueprint (upsert).

    Two modes:

    1. Code-file mode: pass --code-file (-c) with a .py file and --title (-t).
    2. YAML mode: pass --file with a .yaml file containing title, description, code.
       --title and --description can override YAML values.

    Import lines are automatically stripped before upload.

    Examples:
      magnus blueprint save my-bp -t "My Blueprint" -c bp.py
      magnus blueprint save my-bp --file blueprint.yaml
      magnus blueprint save my-bp --file bp.yaml -t "Override Title"
    """
    from ..client import parse_blueprint_yaml

    if file is not None and code_file is not None:
        print_error("Cannot use both --file and --code-file")
        raise typer.Exit(code=1)

    if file is not None:
        if not file.exists():
            print_error(f"File not found: {file}")
            raise typer.Exit(code=1)
        meta = parse_blueprint_yaml(file)
        code = meta.get("code", "")
        final_title = title if title is not None else meta.get("title", "")
        final_description = description or meta.get("description", "")
    elif code_file is not None:
        if not code_file.exists():
            print_error(f"Code file not found: {code_file}")
            raise typer.Exit(code=1)
        if title is None:
            print_error("--title (-t) is required when using --code-file")
            raise typer.Exit(code=1)
        code = code_file.read_text(encoding="utf-8")
        final_title = title
        final_description = description
    else:
        print_error("Either --file or --code-file (-c) is required")
        raise typer.Exit(code=1)

    if not final_title:
        print_error("Title is required (via --title or YAML title field)")
        raise typer.Exit(code=1)

    try:
        result = api_save_blueprint(
            blueprint_id=blueprint_id,
            title=final_title,
            description=final_description,
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
    if "--help" in ctx.args or "-h" in ctx.args:
        _show_blueprint_help(blueprint_id)
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
        from .. import default_client
        print_msg(f"View: [link={default_client.address}/jobs/{job_id}]{default_client.address}/jobs/{job_id}[/link]")

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
    if "--help" in ctx.args or "-h" in ctx.args:
        _show_blueprint_help(blueprint_id)
    try:
        cli_args, bp_args = partition_args(ctx.args)
        cli_config = apply_cli_defaults(cli_args, command_type="run")

        if cli_config["verbose"]:
            console.rule("[dim]DEBUG: Argument Partition[/dim]")
            console.print(f"[dim]CLI Config (Typed): {cli_config}[/dim]")
            console.print(f"[dim]Blueprint Args (String): {bp_args}[/dim]")
            console.rule()

        print_msg(f"Running blueprint [bold cyan]{blueprint_id}[/bold cyan]...")

        from .. import default_client

        job_id = launch_blueprint(
            blueprint_id=blueprint_id,
            use_preference=cli_config["preference"],
            expire_minutes=cli_config["expire_minutes"],
            max_downloads=cli_config["max_downloads"],
            args=bp_args,
        )

        print_msg(f"Job submitted. ID: [green]{job_id}[/green]")

        with SignalSafeSpinner(f"[magnus.prefix][Magnus][/magnus.prefix] Waiting for job completion..."):
            result = default_client._poll_job_completion(
                job_id,
                timeout=cli_config["timeout"],
                poll_interval=cli_config["poll_interval"],
                execute_action_flag=False,
            )

        console.print("")
        print_msg("Job finished.")

        _display_job_result(result, default_client.last_job_id, cli_config["execute_action"])
        print_msg(f"View: [link={default_client.address}/jobs/{job_id}]{default_client.address}/jobs/{job_id}[/link]")

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
    "Jobs run server-side. If your client disconnects (Ctrl-C, network\n"
    "drop), the job keeps running — do not re-submit. Reconnect with\n"
    "'magnus status <job-id>' and 'magnus job result <job-id>'.\n\n"
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

    JOB_REF: Job index (-1, -2, ...) or job ID. Indices are shared across terminals; prefer ID.

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
        from .. import default_client
        print_msg(f"View: [link={default_client.address}/jobs/{job_id}]{default_client.address}/jobs/{job_id}[/link]")

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
        print_msg(f"View: [link={default_client.address}/jobs/{job_id}]{default_client.address}/jobs/{job_id}[/link]")

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


# =============================================================================
# Skill sub-commands: magnus skill <verb>
# =============================================================================


def _collect_skill_files(source: Path) -> Tuple[List[Dict[str, str]], List[Path]]:
    """Read files from a directory. Returns (text_files, binary_paths).
    Binary image files (png/jpg/jpeg/webp/gif) are collected separately for resource upload.
    """
    _RESOURCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    text_files: List[Dict[str, str]] = []
    binary_paths: List[Path] = []

    if source.is_file():
        ext = source.suffix.lower()
        if ext in _RESOURCE_EXTENSIONS:
            binary_paths.append(source)
        else:
            text_files.append({
                "path": source.name,
                "content": source.read_text(encoding="utf-8"),
            })
        return text_files, binary_paths

    for p in sorted(source.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(source))
        ext = p.suffix.lower()
        if ext in _RESOURCE_EXTENSIONS:
            binary_paths.append(p)
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print_error(f"Skipping binary file: {rel}")
            continue
        text_files.append({"path": rel, "content": content})
    return text_files, binary_paths


@skill_app.command(name="list")
def skill_list_cmd(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of skills to fetch"),
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Search by title or ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List available skills.

    Displays a table of skills with ID, title, creator, file count, and last
    update time. Pipe-friendly: outputs YAML when stdout is not a TTY.

    Examples:
      magnus skill list
      magnus skill list -l 20
      magnus skill list -s "coding"
      magnus skill list -f json
    """
    try:
        result = api_list_skills(limit=limit, search=search)
        items = result.get("items", [])
        total = result.get("total", 0)

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data({"total": total, "items": items}, fmt)
            return

        if not items:
            print_msg("No skills found.")
            return

        table = Table(title=f"Skills ({len(items)}/{total})", show_header=True, header_style="bold")
        table.add_column("ID", max_width=25)
        table.add_column("Title", max_width=30)
        table.add_column("Creator", width=15)
        table.add_column("Files", width=5)
        table.add_column("Updated", width=12)

        for sk in items:
            user = sk.get("user") or {}
            files = sk.get("files") or []
            table.add_row(
                sk.get("id", "")[:25],
                (sk.get("title") or "-")[:30],
                (user.get("name") or "-")[:15],
                str(len(files)),
                _format_time(sk.get("updated_at")),
            )

        console.print(table)

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@skill_app.command(name="get")
def skill_get_cmd(
    skill_id: str = typer.Argument(..., help="Skill ID"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: yaml, json"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Export files to a local directory"),
):
    """
    Show skill details and files.

    Displays the skill's title, description, creator, and file listing with
    sizes. Use -o to export all files to a local directory for editing.

    Examples:
      magnus skill get my-skill
      magnus skill get my-skill -o ./my_skill/
      magnus skill get my-skill -f yaml
    """
    try:
        sk = api_get_skill(skill_id)

        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            resolved_root = output_dir.resolve()
            files = sk.get("files") or []
            written = 0
            for f in files:
                fp = (output_dir / f["path"]).resolve()
                if not fp.is_relative_to(resolved_root):
                    print_error(f"Skipping suspicious path: {f['path']}")
                    continue
                fp.parent.mkdir(parents=True, exist_ok=True)
                if f.get("is_binary"):
                    from .. import default_client
                    default_client.download_skill_resource(skill_id, f["path"], fp)
                else:
                    fp.write_text(f["content"], encoding="utf-8")
                written += 1
            print_msg(f"Exported {written} file(s) to [cyan]{output_dir}[/cyan]")
            return

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data(sk, fmt)
            return

        user = sk.get("user") or {}
        files = sk.get("files") or []
        console.print()
        console.rule(f"[bold]Skill: {sk.get('id', 'N/A')}[/bold]")
        console.print(f"  [bold]Title:[/bold]       {sk.get('title', '-')}")
        console.print(f"  [bold]Description:[/bold] {sk.get('description', '-')}")
        console.print(f"  [bold]Creator:[/bold]     {user.get('name', '-')}")
        console.print(f"  [bold]Updated:[/bold]     {_format_time(sk.get('updated_at'))}")
        console.print()
        console.rule("[bold cyan]Files[/bold cyan]")
        for f in files:
            size = len(f.get("content", "").encode("utf-8"))
            if size < 1024:
                size_str = f"{size} B"
            else:
                size_str = f"{size / 1024:.1f} KB"
            console.print(f"  {f['path']}  [dim]({size_str})[/dim]")
        if not files:
            console.print("  [dim](no files)[/dim]")
        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@skill_app.command(name="save")
def skill_save_cmd(
    skill_id: str = typer.Argument(..., help="Skill ID"),
    source: Path = typer.Argument(..., help="Directory or file to upload"),
    title: str = typer.Option(..., "--title", "-t", help="Skill title"),
    description: str = typer.Option("", "--description", "--desc", "-d", help="Skill description"),
):
    """
    Create or update a skill from a local directory (upsert).

    Reads all files from SOURCE and uploads them. A SKILL.md file is
    required — it describes what the skill does and how to use it.

    Text file size is capped at 512 KB. Image resources (png, jpg, jpeg,
    webp, gif) are uploaded separately, up to 32 MB each.

    Examples:
      magnus skill save my-skill ./my_skill/ -t "My Skill"
      magnus skill save my-skill ./my_skill/ -t "Updated" -d "New desc"
      magnus skill save my-skill SKILL.md -t "Minimal Skill"
    """
    if not source.exists():
        print_error(f"Source not found: {source}")
        raise typer.Exit(code=1)

    text_files, binary_paths = _collect_skill_files(source)
    if not text_files and not binary_paths:
        print_error("No files found in source.")
        raise typer.Exit(code=1)

    has_skill_md = any(f["path"] == "SKILL.md" for f in text_files)
    if not has_skill_md:
        print_error("SKILL.md is required. Create a SKILL.md file describing your skill.")
        raise typer.Exit(code=1)

    try:
        result = api_save_skill(
            skill_id=skill_id,
            title=title,
            description=description,
            files=text_files,
        )
        file_count = len(text_files)

        # Upload binary resources
        if binary_paths:
            from .. import default_client
            for bp in binary_paths:
                rel = str(bp.relative_to(source)) if source.is_dir() else bp.name
                default_client.upload_skill_resource(skill_id, bp)
                file_count += 1
                print_msg(f"  Uploaded resource: [cyan]{rel}[/cyan]")

        print_msg(
            f"Skill [bold cyan]{result.get('id', skill_id)}[/bold cyan] saved "
            f"({file_count} file(s))."
        )

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@skill_app.command(name="delete")
def skill_delete_cmd(
    skill_id: str = typer.Argument(..., help="Skill ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """
    Delete a skill.

    Asks for confirmation unless --force is given. This action is
    irreversible.

    Examples:
      magnus skill delete my-skill
      magnus skill delete my-skill -f
    """
    try:
        if not force:
            confirm = typer.confirm(f"Delete skill {skill_id}?")
            if not confirm:
                print_msg("Cancelled.")
                return

        api_delete_skill(skill_id)
        print_msg(f"Skill [bold]{skill_id}[/bold] deleted.")

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


# =============================================================================
# Image sub-commands: magnus image <verb>
# =============================================================================


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


IMAGE_STATUS_COLORS = {
    "cached": "green",
    "pulling": "cyan",
    "refreshing": "yellow",
    "unregistered": "dim",
    "missing": "red",
}


@image_app.command(name="list")
def image_list_cmd(
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Filter by URI"),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: table, yaml, json"),
):
    """
    List cached container images.

    Shows all images in the cluster cache, including their URI, owner,
    size, and status. Unregistered images (present on disk but not in DB)
    are also shown.

    Examples:
      magnus image list
      magnus image list -s pytorch
      magnus image list -f json
    """
    try:
        result = api_list_images(search=search)
        items = result.get("items", [])
        total = result.get("total", 0)

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data({"total": total, "items": items}, fmt)
            return

        if not items:
            print_msg("No cached images found.")
            return

        table = Table(title=f"Images ({len(items)}/{total})", show_header=True, header_style="bold")
        table.add_column("ID", width=5)
        table.add_column("URI", max_width=55)
        table.add_column("Owner", width=12)
        table.add_column("Size", width=10)
        table.add_column("Status", width=14)
        table.add_column("Updated", width=12)

        for img in items:
            status = img.get("status", "unknown")
            status_color = IMAGE_STATUS_COLORS.get(status, "white")
            user = img.get("user") or {}
            img_id = img.get("id")
            id_str = str(img_id) if img_id is not None else "-"

            table.add_row(
                id_str,
                (img.get("uri") or "-")[:55],
                (user.get("name") or "-")[:12],
                _format_size(img.get("size_bytes", 0)),
                f"[{status_color}]{status}[/{status_color}]",
                _format_time(img.get("updated_at")),
            )

        console.print(table)

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@image_app.command(name="pull")
def image_pull_cmd(
    uri: str = typer.Argument(..., help="Container image URI (e.g. docker://pytorch/pytorch:latest)"),
):
    """
    Pull a new container image into the cluster cache.

    Registers the image in the database and triggers a pull. This can
    take several minutes for large images.

    Examples:
      magnus image pull docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
      magnus image pull docker://nvcr.io/nvidia/pytorch:24.01-py3
    """
    try:
        result = api_pull_image(uri=uri, timeout=30.0)
        img_id = result.get("id")
        print_msg(f"Pull started. Image ID: [green]{img_id}[/green]")
        print_msg("Track progress: [bold]magnus image list[/bold]")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@image_app.command(name="refresh")
def image_refresh_cmd(
    image_id: int = typer.Argument(..., help="Image ID (from 'magnus image list')"),
):
    """
    Re-pull a cached image to update it.

    Re-pulls to a temp file and atomically replaces the old one, so the
    existing image stays available during the refresh. Use this when
    you've pushed a new version to an existing tag.

    Examples:
      magnus image refresh 3
    """
    try:
        result = api_refresh_image(image_id=image_id, timeout=30.0)
        print_msg(f"Refresh started for image [bold cyan]{image_id}[/bold cyan].")
        print_msg("Track progress: [bold]magnus image list[/bold]")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@image_app.command(name="remove")
def image_remove_cmd(
    image_id: int = typer.Argument(..., help="Image ID (from 'magnus image list')"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """
    Remove a cached container image.

    Deletes both the SIF file and the database record. Only the image
    owner or an admin can remove an image.

    Examples:
      magnus image remove 3
      magnus image remove 3 -f
    """
    try:
        if not force:
            confirm = typer.confirm(f"Remove cached image {image_id}?")
            if not confirm:
                print_msg("Cancelled.")
                return

        api_remove_image(image_id=image_id)
        print_msg(f"Image [bold]{image_id}[/bold] removed.")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


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


# === Local Mode ===

local_app = typer.Typer(
    name="local",
    help=(
        "Local execution mode: run Magnus jobs in Docker containers on your machine.\n\n"
        "Subcommands:\n"
        "  start     Start a local Magnus server\n"
        "  stop      Stop the local Magnus server\n"
        "  status    Show local server status\n\n"
        "This spins up a full Magnus backend + frontend using Docker instead of SLURM.\n"
        "All magnus commands (run, logs, status, etc.) work identically.\n\n"
        "Examples:\n"
        "  magnus local start\n"
        "  magnus local stop"
    ),
)
app.add_typer(local_app)

LOCAL_MAGNUS_DIR = Path.home() / ".magnus"
LOCAL_CONFIG_PATH = LOCAL_MAGNUS_DIR / "local_config.yaml"
LOCAL_DATA_DIR = LOCAL_MAGNUS_DIR / "data"
LOCAL_BACKEND_LOG = LOCAL_MAGNUS_DIR / "backend.log"
LOCAL_FRONTEND_LOG = LOCAL_MAGNUS_DIR / "frontend.log"
LOCAL_PREVIOUS_SITE_FILE = LOCAL_MAGNUS_DIR / "local_previous_site"
LOCAL_SITE_NAME = "local"
LOCAL_BACK_END_PORT = 8017
LOCAL_FRONT_END_PORT = 3011
LOCAL_OPERATION_TIMEOUT = 600  # 10 minutes; generous for slow networks / cold starts


def _generate_local_config() -> Path:
    """Generate a minimal magnus_config.yaml for local mode."""
    import secrets as _secrets
    import getpass
    # Path.as_posix() — 避免 Windows 反斜杠在 YAML 中被解释为转义字符
    root_posix = LOCAL_DATA_DIR.as_posix()
    config_content = f"""\
# Auto-generated by `magnus local start`
# Do not edit manually — regenerated on each start.

client:
  jobs:
    poll_interval: 2

server:
  address: http://127.0.0.1
  front_end_port: {LOCAL_FRONT_END_PORT}
  back_end_port: {LOCAL_BACK_END_PORT}
  root: {root_posix}
  database:
    pool_size: 4
    max_overflow: 8
    pool_timeout: 10
    pool_recycle: 3600
  auth:
    provider: local
    jwt_signer:
      secret_key: {_secrets.token_hex(32)}
      algorithm: HS256
      expire_minutes: 10080
  scheduler:
    heartbeat_interval: 2
    snapshot_interval: 300
  service_proxy:
    max_concurrency: 64
  file_custody:
    max_size: 10G
    max_file_size: 2G
    max_processes: 16
    default_ttl_minutes: 60
    max_ttl_minutes: 1440

execution:
  backend: local
  container_runtime: docker
  resource_cache:
    container_cache_size: 80G
    repo_cache_size: 20G

cluster:
  name: Local
  gpus: []
  max_cpu_count: 128
  max_memory_demand: 256G
  default_cpu_count: 4
  default_memory_demand: 4G
  default_runner: {getpass.getuser()}
  default_container_image: docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
  default_ephemeral_storage: 10G
  default_system_entry_command: ""
"""
    LOCAL_MAGNUS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(config_content, encoding="utf-8")
    return LOCAL_CONFIG_PATH


def _find_magnus_project_root() -> Optional[Path]:
    """
    Locate the Magnus project root directory.
    Strategy: look relative to the SDK installation, then in ~/.magnus/repository.
    """
    # Strategy 1: SDK installed from source (editable install)
    # __file__ = sdks/python/src/magnus/cli/commands.py
    # .parents[5] = magnus project root
    project_root = Path(__file__).resolve().parents[5]
    if (project_root / "back_end" / "server" / "main.py").exists():
        return project_root

    # Strategy 2: Downloaded to ~/.magnus/repository
    repo_root = LOCAL_MAGNUS_DIR / "repository"
    if (repo_root / "back_end" / "server" / "main.py").exists():
        return repo_root

    return None


def _kill_port(port: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                try:
                    pid = int(parts[-1])
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                    return True
                except (ValueError, IndexError):
                    continue
    else:
        # lsof works on both macOS and Linux (fuser -k syntax differs across platforms)
        try:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        except FileNotFoundError:
            return False  # lsof not installed on this system
        pids = result.stdout.strip()
        if pids:
            subprocess.run(["kill", "-9"] + pids.split(), capture_output=True)
            return True
    return False


def _check_port_available(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _popen_detached(cmd: List[str], **kwargs) -> subprocess.Popen:
    """Start a detached subprocess, handling cross-platform differences."""
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


@local_app.command(name="start")
def local_start():
    """
    Start a local Magnus server with Docker backend.

    This generates a local config, starts the backend + frontend,
    and registers it as the current SDK site. All subsequent magnus commands
    (run, logs, status, etc.) will use this local server.

    Prerequisites: Docker and git must be installed. Node.js is optional (for Web UI).

    Examples:
      magnus local start
    """
    import shutil as _shutil
    import time

    # ── 依赖检查：一次性报告所有结果 ──
    REQUIRED_TOOLS = [
        ("docker", "Container runtime for running jobs",
         "Install Docker Desktop: https://docs.docker.com/get-docker/"),
        ("git", "Version control for repository checkout",
         "Install Git: https://git-scm.com/downloads"),
        ("uv", "Python package manager for the Magnus backend",
         "Install uv: https://docs.astral.sh/uv/getting-started/installation/"),
    ]

    all_ok = True
    print_msg("Checking dependencies...")
    for cmd, purpose, install_hint in REQUIRED_TOOLS:
        if _shutil.which(cmd) is None:
            print_msg(f"  [red]MISSING[/red]  {cmd} — {purpose}")
            print_msg(f"          {install_hint}")
            all_ok = False
        else:
            print_msg(f"  [green]OK[/green]      {cmd}")

    # Docker 特殊检查：二进制存在但 daemon 可能没启动
    if _shutil.which("docker") is not None:
        try:
            subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print_msg(f"  [red]MISSING[/red]  docker daemon — Docker is installed but not running")
            print_msg(f"          Start Docker Desktop, then try again.")
            all_ok = False

    if not all_ok:
        print_msg("")
        print_msg("Please install the missing dependencies and run 'magnus local start' again.")
        raise typer.Exit(1)

    OPTIONAL_TOOLS = [
        ("node", "JavaScript runtime for the Magnus Web UI"),
        ("ffmpeg", "Audio transcription in Explorer"),
    ]
    for cmd, purpose in OPTIONAL_TOOLS:
        if _shutil.which(cmd) is None:
            print_msg(f"  [yellow]OPTIONAL[/yellow]  {cmd} — {purpose}")

    # 端口冲突检测
    ports_ok = True
    for port, role in [(LOCAL_BACK_END_PORT, "backend"), (LOCAL_FRONT_END_PORT, "frontend")]:
        if not _check_port_available(port):
            print_msg(f"  [red]OCCUPIED[/red]  port {port} ({role})")
            ports_ok = False
    if not ports_ok:
        print_msg("")
        print_msg("Free the occupied port(s) and retry.")
        raise typer.Exit(1)

    print_msg("")

    # Find project root (auto-clone if not found)
    project_root = _find_magnus_project_root()
    if project_root is None:
        repo_target = LOCAL_MAGNUS_DIR / "repository"
        print_msg("Magnus project not found. Cloning into ~/.magnus/repository ...")
        clone_result = subprocess.run(
            ["git", "clone", "https://github.com/rise-agi/magnus.git", str(repo_target)],
            capture_output=True,
            text=True,
            timeout=LOCAL_OPERATION_TIMEOUT,
        )
        if clone_result.returncode != 0:
            print_error(f"git clone failed:\n{clone_result.stderr}")
            raise typer.Exit(1)
        project_root = repo_target
        if not (project_root / "back_end" / "server" / "main.py").exists():
            print_error(f"Cloned repository at {repo_target} does not contain a valid Magnus project.")
            raise typer.Exit(1)
        print_msg(f"Cloned Magnus to {repo_target}")

    back_end_path = project_root / "back_end"
    front_end_path = project_root / "front_end"

    # Generate config
    config_path = _generate_local_config()
    print_msg(f"Config generated: {config_path}")

    # Save previous SDK site for restore on stop
    from ..config import _load_config
    sdk_config = _load_config()
    previous_site = sdk_config.get("current")
    if previous_site and previous_site != LOCAL_SITE_NAME:
        LOCAL_PREVIOUS_SITE_FILE.write_text(previous_site, encoding="utf-8")

    # Start backend server (uv run 使用后端自己的 venv，而非 SDK 的 Python)
    # uv sync — 先在前台安装依赖，让用户看到进度（类似 npm install）
    if not (back_end_path / ".venv").exists():
        print_msg("Installing backend dependencies...")
        sync_result = subprocess.run(
            ["uv", "sync"],
            cwd=str(back_end_path),
            timeout=LOCAL_OPERATION_TIMEOUT,
        )
        if sync_result.returncode != 0:
            print_error("uv sync failed (see output above)")
            raise typer.Exit(1)

    print_msg("Starting backend server...")
    # Redirect to a log file instead of PIPE — a PIPE with no reader would
    # fill its buffer and block the detached process indefinitely.
    log_handle = open(LOCAL_BACKEND_LOG, "w")
    backend_proc = _popen_detached(
        ["uv", "run", "-m", "server.main", "--deliver", "--config", str(config_path)],
        cwd=str(back_end_path),
        stdout=log_handle,
        stderr=log_handle,
    )
    log_handle.close()  # child inherited the fd; parent can release

    # Wait for backend to be fully ready (lifespan complete, API serving)
    health_url = f"http://127.0.0.1:{LOCAL_BACK_END_PORT}/health"
    ready_deadline = time.time() + LOCAL_OPERATION_TIMEOUT
    backend_ready = False
    with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Waiting for backend..."):
        while time.time() < ready_deadline:
            if backend_proc.poll() is not None:
                break
            try:
                r = httpx.get(health_url, timeout=2)
                if r.status_code == 200:
                    backend_ready = True
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            time.sleep(0.5)

    if backend_proc.poll() is not None and not backend_ready:
        stderr_output = LOCAL_BACKEND_LOG.read_text(encoding="utf-8", errors="replace").strip()
        error_detail = f":\n{stderr_output}" if stderr_output else f" (check {LOCAL_BACKEND_LOG})"
        print_error(f"Backend failed to start{error_detail}")
        raise typer.Exit(1)

    if not backend_ready:
        # Kill the detached backend to prevent port leak on next start
        try:
            backend_proc.terminate()
            backend_proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                backend_proc.kill()
            except OSError:
                pass
        _kill_port(LOCAL_BACK_END_PORT)
        print_error(f"Backend did not become ready within {LOCAL_OPERATION_TIMEOUT}s (check {LOCAL_BACKEND_LOG})")
        raise typer.Exit(1)

    print_msg(f"Backend started at http://127.0.0.1:{LOCAL_BACK_END_PORT}")

    # Start frontend
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
    frontend_started = False

    if front_end_path.exists() and (front_end_path / "package.json").exists():
        frontend_env = os.environ.copy()
        frontend_env["MAGNUS_CONFIG_PATH"] = str(config_path)
        frontend_env["MAGNUS_DELIVER"] = "TRUE"

        # npm install — 输出直接流到终端，让用户看到进度
        if not (front_end_path / "node_modules").exists():
            print_msg("Installing frontend dependencies...")
            install_result = subprocess.run(
                [npm_cmd, "install"],
                cwd=str(front_end_path),
                timeout=LOCAL_OPERATION_TIMEOUT,
            )
            if install_result.returncode != 0:
                print_error("npm install failed (see output above)")
                raise typer.Exit(1)

        # next build — 只在 .next 不存在时执行
        next_dir = front_end_path / ".next"
        if not next_dir.exists():
            print_msg("Building frontend (first time)...")
            build_result = subprocess.run(
                [npx_cmd, "next", "build"],
                cwd=str(front_end_path),
                env=frontend_env,
                timeout=LOCAL_OPERATION_TIMEOUT,
            )
            if build_result.returncode != 0:
                print_error("next build failed (see output above)")
                raise typer.Exit(1)

        # npm run start → server.mjs (Next.js + WS proxy)，生产模式，日志写文件
        print_msg("Starting frontend...")
        fe_log = open(LOCAL_FRONTEND_LOG, "w")
        frontend_proc = _popen_detached(
            [npm_cmd, "run", "start", "--", "-p", str(LOCAL_FRONT_END_PORT)],
            cwd=str(front_end_path),
            stdout=fe_log,
            stderr=fe_log,
            env=frontend_env,
        )
        fe_log.close()
        fe_deadline = time.time() + LOCAL_OPERATION_TIMEOUT
        with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Waiting for frontend..."):
            while time.time() < fe_deadline:
                if frontend_proc.poll() is not None:
                    break
                try:
                    httpx.get(f"http://127.0.0.1:{LOCAL_FRONT_END_PORT}", timeout=2)
                    frontend_started = True
                    break
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                time.sleep(0.5)
        if frontend_started:
            print_msg(f"Frontend started at http://127.0.0.1:{LOCAL_FRONT_END_PORT}")
        else:
            fe_err = LOCAL_FRONTEND_LOG.read_text(encoding="utf-8", errors="replace").strip()
            print_msg(f"Warning: Frontend failed to start within {LOCAL_OPERATION_TIMEOUT}s (check {LOCAL_FRONTEND_LOG})")
            if fe_err:
                print_msg(fe_err[:500])

    # Register as SDK site
    local_address = f"http://127.0.0.1:{LOCAL_BACK_END_PORT}"
    save_site(LOCAL_SITE_NAME, local_address, "local")
    print_msg(f"Site '{LOCAL_SITE_NAME}' registered and set as current.")

    # Register bundled blueprints
    from ..bundled.register import register_bundled_blueprints, register_bundled_skills
    with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Registering bundled blueprints..."):
        registered = register_bundled_blueprints(local_address, "local")
    if registered:
        print_msg(f"Registered {len(registered)} bundled blueprint(s):")
        for bp_id, bp_title in registered:
            print_msg(f"  - {bp_title} ({bp_id})")

    # Register bundled skills
    with SignalSafeSpinner("[magnus.prefix][Magnus][/magnus.prefix] Registering bundled skills..."):
        registered_skills = register_bundled_skills(local_address, "local")
    if registered_skills:
        print_msg(f"Registered {len(registered_skills)} bundled skill(s):")
        for sk_id, sk_title in registered_skills:
            print_msg(f"  - {sk_title} ({sk_id})")

    print_msg("")
    if frontend_started:
        print_msg(f"Open http://127.0.0.1:{LOCAL_FRONT_END_PORT} in your browser, or use the CLI:")
    else:
        print_msg("Use the CLI:")
    print_msg("  magnus --help            # see all available commands")
    print_msg("  magnus local stop        # stop the server")


@local_app.command(name="stop")
def local_stop():
    """
    Stop the local Magnus server and frontend.

    Examples:
      magnus local stop
    """
    import time
    stopped_any = False

    for port, role in [(LOCAL_FRONT_END_PORT, "Frontend"), (LOCAL_BACK_END_PORT, "Backend")]:
        if not _check_port_available(port):
            _kill_port(port)
            time.sleep(0.5)
            if _check_port_available(port):
                print_msg(f"{role} stopped (port {port})")
            else:
                print_msg(f"[yellow]Warning[/yellow]: Failed to stop {role} on port {port}. Kill it manually.")
            stopped_any = True

    if not stopped_any:
        print_msg("No local server running.")

    # Restore previous SDK site
    if LOCAL_PREVIOUS_SITE_FILE.exists():
        previous_site = LOCAL_PREVIOUS_SITE_FILE.read_text(encoding="utf-8").strip()
        if previous_site:
            from ..config import set_current_site
            set_current_site(previous_site)
            print_msg(f"Restored SDK site to '{previous_site}'.")
        LOCAL_PREVIOUS_SITE_FILE.unlink(missing_ok=True)


@local_app.command(name="status")
def local_status():
    """
    Show local server status.

    Examples:
      magnus local status
    """
    any_alive = False

    if not _check_port_available(LOCAL_BACK_END_PORT):
        print_msg(f"Backend running at http://127.0.0.1:{LOCAL_BACK_END_PORT}")
        any_alive = True

    if not _check_port_available(LOCAL_FRONT_END_PORT):
        print_msg(f"Frontend running at http://127.0.0.1:{LOCAL_FRONT_END_PORT}")
        any_alive = True

    if not any_alive:
        print_msg("No local server running.")


if __name__ == "__main__":
    app()