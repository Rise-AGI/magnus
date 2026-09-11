# sdks/python/src/magnus/cli/_blueprints.py
"""`magnus blueprint <verb>` sub-commands plus blueprint launch/run helpers."""
import typer
from typing import Optional
from pathlib import Path
from rich.table import Table
from rich.markup import escape as rich_escape
from ..exceptions import MagnusError
from .. import (
    launch_blueprint,
    list_blueprints as api_list_blueprints,
    get_blueprint as api_get_blueprint,
    get_blueprint_schema as api_get_blueprint_schema,
    save_blueprint as api_save_blueprint,
    delete_blueprint as api_delete_blueprint,
)
from ._shared import (
    OutputFormat,
    SignalSafeSpinner,
    _auto_format,
    _format_time,
    _job_view_link_msg,
    _output_data,
    apply_cli_defaults,
    console,
    partition_args,
    print_error,
    print_msg,
)
from ._jobs import _display_job_result


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
    console.print(f"[bold cyan]{rich_escape(title)}[/bold cyan]  [dim]({rich_escape(blueprint_id)})[/dim]")
    if desc:
        console.print(f"[dim]{rich_escape(desc)}[/dim]")
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
        type_label_safe = rich_escape(type_label)
        if default is not None:
            header = f"  [green]{flag}[/green] [dim]{type_label_safe}[/dim]  [dim](default: {rich_escape(repr(default))})[/dim]"
        else:
            header = f"  [green]{flag}[/green] [dim]{type_label_safe}[/dim]"
        console.print(header)

        if desc_text:
            console.print(f"      [dim]{rich_escape(desc_text)}[/dim]")
        if ptype == "select" and options:
            for o in options:
                opt_desc = o.get("description")
                if opt_desc:
                    console.print(f"      [dim]{rich_escape(str(o['value']))}: {rich_escape(opt_desc)}[/dim]")

    console.print()
    console.print("[dim]Usage: magnus run {id} -- {params}[/dim]".format(id=blueprint_id, params=" ".join(f"--{p['key'].replace('_', '-')} VALUE" for p in schema if not p.get("is_optional"))))
    raise typer.Exit(0)


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
                rich_escape(bp.get("id", "")[:25]),
                rich_escape((bp.get("title") or "-")[:30]),
                rich_escape((user.get("name") or "-")[:15]),
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
            print_msg(f"Blueprint exported to [cyan]{rich_escape(str(output))}[/cyan]")
            return

        if code_file is not None:
            code = bp.get("code", "")
            code_file.write_text(code, encoding="utf-8")
            print_msg(f"Code exported to [cyan]{rich_escape(str(code_file))}[/cyan]")
            return

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data(bp, fmt)
            return

        user = bp.get("user") or {}
        console.print()
        console.rule(f"[bold]Blueprint: {rich_escape(bp.get('id', 'N/A'))}[/bold]")
        console.print(f"  [bold]Title:[/bold]       {rich_escape(bp.get('title', '-'))}")
        console.print(f"  [bold]Description:[/bold] {rich_escape(bp.get('description', '-'))}")
        console.print(f"  [bold]Creator:[/bold]     {rich_escape(user.get('name', '-'))}")
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
        print_msg(f"Blueprint [bold cyan]{rich_escape(result.get('id', blueprint_id))}[/bold cyan] saved.")

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
        print_msg(f"Blueprint [bold]{rich_escape(blueprint_id)}[/bold] deleted.")

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
            console.print(f"[dim]CLI Config (Typed): {rich_escape(repr(cli_config))}[/dim]")
            console.print(f"[dim]Blueprint Args (String): {rich_escape(repr(bp_args))}[/dim]")
            console.rule()

        print_msg(f"Launching blueprint [bold cyan]{rich_escape(blueprint_id)}[/bold cyan]...")

        job_id = launch_blueprint(
            blueprint_id=blueprint_id,
            use_preference=cli_config["preference"],
            expire_minutes=cli_config["expire_minutes"],
            max_downloads=cli_config["max_downloads"],
            timeout=cli_config["timeout"],
            args=bp_args,
        )

        print_msg(f"Job submitted. ID: [green]{job_id}[/green] (use [cyan]-1[/cyan] to reference)")
        print_msg(_job_view_link_msg(job_id))

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
            console.print(f"[dim]CLI Config (Typed): {rich_escape(repr(cli_config))}[/dim]")
            console.print(f"[dim]Blueprint Args (String): {rich_escape(repr(bp_args))}[/dim]")
            console.rule()

        print_msg(f"Running blueprint [bold cyan]{rich_escape(blueprint_id)}[/bold cyan]...")

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
