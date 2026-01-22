# sdks/python/src/magnus/cli/commands.py
import os
import sys
import json
import signal
import typer
from typing import List, Optional, Any, Dict, Tuple
from pathlib import Path
from rich.console import Console
from rich.theme import Theme
from rich.table import Table
from rich.status import Status
from datetime import datetime

__version__ = "0.1.9"

from .. import (
    MagnusError,
    submit_blueprint,
    run_blueprint,
    call_service,
    list_jobs as api_list_jobs,
    get_job as api_get_job,
    terminate_job as api_terminate_job,
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


# === Argument Parsing Logic ===

def parse_cli_args(args: List[str]) -> Dict[str, Any]:
    """
    [Smart Parser] 用于 Magnus CLI 自身的控制参数 (如 --timeout, --verbose)。
    采用积极类型推断策略 (Int/Float/Bool)，方便内部逻辑处理。
    """
    params = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key.startswith("--"):
            key = key[2:]
            key = key.replace("-", "_")
            
            # Check for value
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                value = args[i + 1]
                i += 2
            else:
                value = True  # Flag defaults to True
                i += 1
            
            # Type Inference
            if isinstance(value, str):
                lower_val = value.lower()
                if lower_val == "true": value = True
                elif lower_val == "false": value = False
                elif value.isdigit(): value = int(value)
                else:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
            params[key] = value
        else:
            i += 1
    return params

def parse_blueprint_args(args: List[str]) -> Dict[str, str]:
    """
    [Raw Parser] 用于传递给 Blueprints 的业务参数。
    原则：不猜测，不转换。所有值均保持为字符串，类型转换由后端/蓝图负责。
    Example:
      --count 2   -> {"count": "2"}
      --enable    -> {"enable": "true"}
    """
    params = {}
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
            params[key] = value
        else:
            i += 1
    return params

def partition_args(raw_args: List[str]) -> Tuple[Dict[str, Any], Dict[str, str]]:
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
    "preference": True,   # User Preference
    "verbose": False,     # Debug Mode
    "poll_interval": 2.0  # Polling Interval (Run only)
}

def apply_cli_defaults(parsed_cli_args: Dict[str, Any], command_type: str = "submit") -> Dict[str, Any]:
    config = DEFAULT_CLI_CONFIG.copy()
    
    # 特殊逻辑：Run 模式下若未指定 timeout，默认应为无限等待 (None)，而非 submit 的 10s
    if command_type == "run" and "timeout" not in parsed_cli_args:
        config["timeout"] = None
        
    config.update(parsed_cli_args)
    return config

# === CLI App Definition ===

def _version_callback(value: bool):
    if value:
        console.print(f"[bold blue]Magnus SDK[/bold blue] v{__version__}")
        console.print("[dim]PKU Plasma · Rise-AGI[/dim]")
        raise typer.Exit()


app = typer.Typer(
    name="magnus",
    help="Magnus CLI - Focus on your Blueprint.",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def main_callback(
    _version: bool = typer.Option(False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version"),
):
    pass

@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def submit(
    ctx: typer.Context,
    blueprint_id: str = typer.Argument(..., help="ID of the blueprint"),
):
    """
    Submit a blueprint job (Fire & Forget).
    All unrecognized arguments are passed to the blueprint as strings.
    """
    try:
        cli_args, bp_args = partition_args(ctx.args)
        cli_config = apply_cli_defaults(cli_args, command_type="submit")
        
        if cli_config["verbose"]:
            console.rule("[dim]DEBUG: Argument Partition[/dim]")
            console.print(f"[dim]CLI Config (Typed): {cli_config}[/dim]")
            console.print(f"[dim]Blueprint Args (String): {bp_args}[/dim]")
            console.rule()

        print_msg(f"Submitting blueprint [bold cyan]{blueprint_id}[/bold cyan]...")
        
        job_id = submit_blueprint(
            blueprint_id=blueprint_id,
            use_preference=cli_config["preference"],
            timeout=cli_config["timeout"],
            args=bp_args
        )

        print_msg(f"Job submitted. ID: [green]{job_id}[/green] (use [cyan]-1[/cyan] to reference)")
        
    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def run(
    ctx: typer.Context,
    blueprint_id: str = typer.Argument(..., help="ID of the blueprint"),
):
    """
    Execute a blueprint and wait for completion.
    """
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
                timeout=cli_config["timeout"],
                poll_interval=cli_config["poll_interval"],
                args=bp_args
            )
        
        console.print("")
        print_msg("Job finished.")
        console.rule("[bold green]MAGNUS RESULT[/bold green]")
        
        try:
            if isinstance(result, str):
                json_obj = json.loads(result)
                console.print_json(data=json_obj)
            else:
                console.print(result)
        except Exception:
            console.print(result)
        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        print_msg("Interrupted by user.")
        raise typer.Exit(code=130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


CLI_RESERVED_KEYS = {"timeout", "verbose"}


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
                if value == "true":
                    cli_config[key] = True
                elif value.isdigit():
                    cli_config[key] = int(value)
                else:
                    try:
                        cli_config[key] = float(value)
                    except ValueError:
                        cli_config[key] = value
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
):
    """
    List recent jobs. Index -1 = newest, -2 = second newest, ...
    """
    try:
        result = api_list_jobs(limit=limit, search=name)
        items = result.get("items", [])
        total = result.get("total", 0)

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
        print_msg("[dim]Use: magnus status -1, magnus kill -2 -f, ...[/dim]")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(name="status")
def job_status_cmd(
    job_ref: str = typer.Argument(..., help="Job index (-1, -2, ...) or job ID"),
):
    """
    Show detailed status of a job.

    Examples:
      magnus status -1          # newest job
      magnus status -2          # second newest
      magnus status abc123      # by job ID
    """
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
        console.print(f"  [bold]Started:[/bold] {_format_time(job.get('started_at'))}")
        console.print(f"  [bold]Ended:[/bold]   {_format_time(job.get('ended_at'))}")

        result = job.get("result")
        if result and result != ".magnus_result":
            console.print()
            console.rule("[bold green]Result[/bold green]")
            try:
                console.print_json(data=json.loads(result))
            except Exception:
                console.print(result)

        console.rule()

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(name="kill")
def kill_job_cmd(
    job_ref: str = typer.Argument(..., help="Job index (-1, -2, ...) or job ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """
    Terminate a running job.

    Examples:
      magnus kill -1            # kill newest job
      magnus kill -1 -f         # skip confirmation
      magnus kill abc123        # by job ID
    """
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


if __name__ == "__main__":
    app()