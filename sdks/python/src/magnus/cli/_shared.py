# sdks/python/src/magnus/cli/_shared.py
"""Cross-cutting CLI helpers shared by every command module: console and
output plumbing, the signal-safe spinner, generic CLI argument parsing, and
job-reference context helpers."""
import io
import os
import sys
import signal
import typer
from typing import List, Optional, Any, Dict, Tuple, Literal
from datetime import datetime
from rich.console import Console
from rich.theme import Theme
from rich.status import Status
from rich.markup import escape as rich_escape


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
    # 错误消息槽里逐个把传入文本 escape：异常字符串、文件路径、
    # _format_schema_hint 的 "Optional[str]" 形式都会流到这里，绝不能让
    # Rich 把里面的 "[xxx]" 当 style tag 吞掉。前缀的 [magnus.prefix] /
    # [magnus.error] 是受控样式，仍走 markup。
    err_console.print(f"[magnus.prefix][Magnus][/magnus.prefix] [magnus.error]Error:[/magnus.error] {rich_escape(msg or 'Unknown error')}", highlight=False)


def _job_view_link_msg(job_id: str)-> str:
    """构造 'View: <link>' 文本。显示文本 escape；URL 作为 link attribute
    不会被 markup parse（IPv6 字面量含 ']' 的边角 case 会让 tag 破裂，但
    site address 几乎都是 hostname 或 IPv4，不影响主线）。"""
    from .. import default_client
    url = f"{default_client.address}/jobs/{job_id}"
    return f"View: [link={url}]{rich_escape(url)}[/link]"


# === Output Format Helpers ===

OutputFormat = Literal["table", "yaml", "json"]

_yaml_dumper = None


def _get_yaml_dumper():
    """Lazily build the ruamel.yaml dumper. Only `--format yaml` output needs
    it, so importing this CLI module must not hard-depend on ruamel.yaml —
    otherwise a broken/missing ruamel install in the user's env takes down every
    command (receive/submit/login/...), not just yaml output."""
    global _yaml_dumper
    if _yaml_dumper is None:
        from ruamel.yaml import YAML
        _yaml_dumper = YAML()
        _yaml_dumper.default_flow_style = False
    return _yaml_dumper


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
        _get_yaml_dumper().dump(data, stream)
        # markup=False/highlight=False: 数据 dump 是机读文本，
        # 不能让 Rich 把字段里的 "[str, ...]" 当成 style tag 吞掉
        console.print(stream.getvalue(), end="", markup=False, highlight=False)
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
            # SIGTSTP / SIGCONT POSIX-only；运行时由 os.name 守门
            self._old_sigtstp = signal.signal(signal.SIGTSTP, self._handle_sigtstp)  # type: ignore[attr-defined]
            self._old_sigcont = signal.signal(signal.SIGCONT, self._handle_sigcont)  # type: ignore[attr-defined]

        return self

    def __exit__(self, *args: Any) -> None:
        if self._status:
            self._status.stop()
            self._status = None

        if os.name != "nt":
            if self._old_sigtstp is not None:
                signal.signal(signal.SIGTSTP, self._old_sigtstp)  # type: ignore[attr-defined]
            if self._old_sigcont is not None:
                signal.signal(signal.SIGCONT, self._old_sigcont)  # type: ignore[attr-defined]

    def _handle_sigtstp(self, signum: int, frame: Any) -> None:
        if self._status:
            self._status.stop()
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)  # type: ignore[attr-defined]
        os.kill(os.getpid(), signal.SIGTSTP)  # type: ignore[attr-defined]

    def _handle_sigcont(self, signum: int, frame: Any) -> None:
        signal.signal(signal.SIGTSTP, self._handle_sigtstp)  # type: ignore[attr-defined]
        if self._status:
            self._status.start()


# === Job Index Resolution ===

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

CLI_RESERVED_KEYS = {"timeout", "verbose", "execute_action"}

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


def _format_time(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # 服务端发的是带偏移量的时刻，换算到本机时区再显示；否则 UTC 会被原样当成
        # 墙上时间示人。老服务端可能仍发不带偏移量的裸时间，那种情况无从判断时区，
        # 只能原样显示。
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_str[:16]
