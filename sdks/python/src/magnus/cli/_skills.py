# sdks/python/src/magnus/cli/_skills.py
"""`magnus skill <verb>` sub-commands."""
import typer
from typing import List, Optional, Dict, Tuple
from pathlib import Path
from rich.table import Table
from rich.markup import escape as rich_escape
from ..exceptions import MagnusError
from .. import (
    list_skills as api_list_skills,
    get_skill as api_get_skill,
    save_skill as api_save_skill,
    delete_skill as api_delete_skill,
)
from ._shared import (
    OutputFormat,
    _auto_format,
    _format_time,
    _output_data,
    console,
    print_error,
    print_msg,
)


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
        rel = p.relative_to(source).as_posix()
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
            # The list projection carries a lightweight file_count instead of the file
            # contents; fall back to len(files) for older servers that still ship files.
            file_count = sk.get("file_count")
            if file_count is None:
                file_count = len(sk.get("files") or [])
            table.add_row(
                rich_escape(sk.get("id", "")[:25]),
                rich_escape((sk.get("title") or "-")[:30]),
                rich_escape((user.get("name") or "-")[:15]),
                str(file_count),
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
            print_msg(f"Exported {written} file(s) to [cyan]{rich_escape(str(output_dir))}[/cyan]")
            return

        fmt: OutputFormat = format if format in ("table", "yaml", "json") else _auto_format()

        if fmt in ("yaml", "json"):
            _output_data(sk, fmt)
            return

        user = sk.get("user") or {}
        files = sk.get("files") or []
        console.print()
        console.rule(f"[bold]Skill: {rich_escape(sk.get('id', 'N/A'))}[/bold]")
        console.print(f"  [bold]Title:[/bold]       {rich_escape(sk.get('title', '-'))}")
        console.print(f"  [bold]Description:[/bold] {rich_escape(sk.get('description', '-'))}")
        console.print(f"  [bold]Creator:[/bold]     {rich_escape(user.get('name', '-'))}")
        console.print(f"  [bold]Updated:[/bold]     {_format_time(sk.get('updated_at'))}")
        console.print()
        console.rule("[bold cyan]Files[/bold cyan]")
        for f in files:
            size = len(f.get("content", "").encode("utf-8"))
            if size < 1024:
                size_str = f"{size} B"
            else:
                size_str = f"{size / 1024:.1f} KB"
            console.print(f"  {rich_escape(f['path'])}  [dim]({size_str})[/dim]")
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
                rel = bp.relative_to(source).as_posix() if source.is_dir() else bp.name
                default_client.upload_skill_resource(skill_id, bp)
                file_count += 1
                print_msg(f"  Uploaded resource: [cyan]{rich_escape(str(rel))}[/cyan]")

        print_msg(
            f"Skill [bold cyan]{rich_escape(result.get('id', skill_id))}[/bold cyan] saved "
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
        print_msg(f"Skill [bold]{rich_escape(skill_id)}[/bold] deleted.")

    except MagnusError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)
