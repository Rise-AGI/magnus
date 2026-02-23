# back_end/library/functional/opencode/tools/read.py
# Adapted from: opencode/packages/opencode/src/tool/read.ts
# Migration strategy: 保留核心功能（行号显示、大文件截断、二进制检测）
# 简化：移除 LSP 集成、InstructionPrompt、图片/PDF 处理（后续可扩展）
import os
from pathlib import Path
from typing import Any, Dict

from .base import Tool, ToolResult, ToolContext, register_tool


DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_BYTES = 50 * 1024

DESCRIPTION = """Reads a file from the workspace.

Usage:
- The file_path parameter can be absolute or relative to the workspace
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files)
- Any lines longer than 2000 characters will be truncated
- Results are returned with line numbers starting at 1
- This tool cannot read binary files

Example:
- Read entire file: {"file_path": "src/main.py"}
- Read with offset: {"file_path": "src/main.py", "offset": 100, "limit": 50}
"""

BINARY_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar",
    ".war", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".bin", ".dat", ".obj", ".o", ".a",
    ".lib", ".wasm", ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif",
    ".bmp", ".ico", ".pdf", ".mp3", ".mp4", ".avi", ".mov", ".wav",
}


def is_binary_file(filepath: str)-> bool:
    ext = Path(filepath).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True

    try:
        with open(filepath, "rb") as f:
            chunk = f.read(4096)
            if not chunk:
                return False
            if b"\x00" in chunk:
                return True
            non_printable = sum(1 for b in chunk if b < 9 or (13 < b < 32))
            return non_printable / len(chunk) > 0.3
    except Exception:
        return False


class ReadTool(Tool):
    @property
    def name(self)-> str:
        return "read"

    @property
    def description(self)-> str:
        return DESCRIPTION

    @property
    def parameters(self)-> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to read (absolute or relative to workspace)",
                },
                "offset": {
                    "type": "integer",
                    "description": "The line number to start reading from (0-based)",
                },
                "limit": {
                    "type": "integer",
                    "description": f"The number of lines to read (defaults to {DEFAULT_READ_LIMIT})",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, params: Dict[str, Any], ctx: ToolContext)-> ToolResult:
        file_path = params["file_path"]
        offset = params.get("offset", 0)
        limit = params.get("limit", DEFAULT_READ_LIMIT)

        file_path = ctx.resolve_host_path(file_path)
        file_path = os.path.normpath(file_path)
        title = os.path.relpath(file_path, ctx.workspace_path)

        if not file_path.startswith(ctx.workspace_path):
            raise ValueError(f"Access denied: path is outside workspace")

        if not os.path.exists(file_path):
            parent_dir = os.path.dirname(file_path)
            base_name = os.path.basename(file_path)

            suggestions = []
            if os.path.isdir(parent_dir):
                for entry in os.listdir(parent_dir)[:20]:
                    if base_name.lower() in entry.lower() or entry.lower() in base_name.lower():
                        suggestions.append(os.path.join(parent_dir, entry))

            if suggestions:
                raise FileNotFoundError(
                    f"File not found: {file_path}\n\nDid you mean one of these?\n" +
                    "\n".join(suggestions[:3])
                )
            raise FileNotFoundError(f"File not found: {file_path}")

        if is_binary_file(file_path):
            raise ValueError(f"Cannot read binary file: {file_path}")

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        raw_lines = []
        total_bytes = 0
        truncated_by_bytes = False

        for i in range(offset, min(len(all_lines), offset + limit)):
            line = all_lines[i].rstrip("\n\r")
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + "..."

            line_bytes = len(line.encode("utf-8")) + (1 if raw_lines else 0)
            if total_bytes + line_bytes > MAX_BYTES:
                truncated_by_bytes = True
                break

            raw_lines.append(line)
            total_bytes += line_bytes

        content_lines = [
            f"{str(i + offset + 1).zfill(5)}| {line}"
            for i, line in enumerate(raw_lines)
        ]

        output = "<file>\n"
        output += "\n".join(content_lines)

        total_lines = len(all_lines)
        last_read_line = offset + len(raw_lines)
        has_more_lines = total_lines > last_read_line
        truncated = has_more_lines or truncated_by_bytes

        if truncated_by_bytes:
            output += f"\n\n(Output truncated at {MAX_BYTES} bytes. Use 'offset' parameter to read beyond line {last_read_line})"
        elif has_more_lines:
            output += f"\n\n(File has more lines. Use 'offset' parameter to read beyond line {last_read_line})"
        else:
            output += f"\n\n(End of file - total {total_lines} lines)"

        output += "\n</file>"

        return ToolResult(
            title = title,
            output = output,
            metadata = {
                "preview": "\n".join(raw_lines[:20]),
                "truncated": truncated,
                "total_lines": total_lines,
                "lines_read": len(raw_lines),
            },
        )


register_tool(ReadTool())
