# sdks/python/src/magnus/cli/_images.py
"""`magnus image <verb>` sub-commands."""
import typer
from typing import Optional
from rich.table import Table
from rich.markup import escape as rich_escape
from ..exceptions import MagnusError
from .. import (
    list_images as api_list_images,
    pull_image as api_pull_image,
    refresh_image as api_refresh_image,
    remove_image as api_remove_image,
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
                rich_escape((img.get("uri") or "-")[:55]),
                rich_escape((user.get("name") or "-")[:12]),
                _format_size(img.get("size_bytes", 0)),
                f"[{status_color}]{rich_escape(status)}[/{status_color}]",
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
