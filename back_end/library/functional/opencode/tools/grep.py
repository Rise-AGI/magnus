# back_end/library/functional/opencode/tools/grep.py
# Adapted from: opencode/packages/opencode/src/tool/grep.ts
# Migration strategy: 使用 Python re 模块实现正则搜索
# 简化：移除 ripgrep 依赖，使用 Python 原生实现（性能可接受）
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .base import Tool, ToolResult, ToolContext, register_tool


DESCRIPTION = """Search for patterns in file contents using regex.

Usage:
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with include parameter (e.g., "*.py", "*.{ts,tsx}")
- Returns matches sorted by file modification time (most recent first)

Examples:
- Search for pattern: {"pattern": "def main"}
- Search in specific files: {"pattern": "import", "include": "*.py"}
"""

MAX_RESULTS = 100
MAX_LINE_LENGTH = 2000

BINARY_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar",
    ".war", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".bin", ".dat", ".obj", ".o", ".a",
    ".lib", ".wasm", ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif",
    ".bmp", ".ico", ".pdf", ".mp3", ".mp4", ".avi", ".mov", ".wav",
}


def matches_glob(filename: str, pattern: str)-> bool:
    import fnmatch
    if "{" in pattern and "}" in pattern:
        start = pattern.index("{")
        end = pattern.index("}")
        prefix = pattern[:start]
        suffix = pattern[end + 1:]
        options = pattern[start + 1:end].split(",")
        return any(fnmatch.fnmatch(filename, prefix + opt + suffix) for opt in options)
    return fnmatch.fnmatch(filename, pattern)


class GrepTool(Tool):
    @property
    def name(self)-> str:
        return "grep"

    @property
    def description(self)-> str:
        return DESCRIPTION

    @property
    def parameters(self)-> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for in file contents",
                },
                "path": {
                    "type": "string",
                    "description": "The directory to search in. Defaults to workspace root.",
                },
                "include": {
                    "type": "string",
                    "description": "File pattern to include in the search (e.g., '*.py', '*.{ts,tsx}')",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, params: Dict[str, Any], ctx: ToolContext)-> ToolResult:
        pattern = params["pattern"]
        search_path = params.get("path", "")
        include = params.get("include")

        if search_path:
            search_path = ctx.resolve_host_path(search_path)
        else:
            search_path = ctx.workspace_path

        search_path = os.path.normpath(search_path)

        if not search_path.startswith(ctx.workspace_path):
            raise ValueError(f"Access denied: path is outside workspace")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        matches: List[Tuple[str, float, int, str]] = []

        for root, _, files in os.walk(search_path):
            for filename in files:
                if Path(filename).suffix.lower() in BINARY_EXTENSIONS:
                    continue

                if include and not matches_glob(filename, include):
                    continue

                filepath = os.path.join(root, filename)

                try:
                    mtime = os.path.getmtime(filepath)
                except OSError:
                    mtime = 0

                try:
                    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                line_text = line.rstrip("\n\r")
                                if len(line_text) > MAX_LINE_LENGTH:
                                    line_text = line_text[:MAX_LINE_LENGTH] + "..."
                                matches.append((filepath, mtime, line_num, line_text))

                                if len(matches) >= MAX_RESULTS * 2:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue

            if len(matches) >= MAX_RESULTS * 2:
                break

        matches.sort(key=lambda x: x[1], reverse=True)

        truncated = len(matches) > MAX_RESULTS
        final_matches = matches[:MAX_RESULTS]

        if not final_matches:
            return ToolResult(
                title = pattern,
                output = "No files found",
                metadata = {"matches": 0, "truncated": False},
            )

        output_lines = [f"Found {len(final_matches)} matches"]
        current_file = ""

        for filepath, _, line_num, line_text in final_matches:
            if current_file != filepath:
                if current_file:
                    output_lines.append("")
                current_file = filepath
                output_lines.append(f"{filepath}:")
            output_lines.append(f"  Line {line_num}: {line_text}")

        if truncated:
            output_lines.append("")
            output_lines.append("(Results are truncated. Consider using a more specific path or pattern.)")

        return ToolResult(
            title = pattern,
            output = "\n".join(output_lines),
            metadata = {
                "matches": len(final_matches),
                "truncated": truncated,
            },
        )


register_tool(GrepTool())
