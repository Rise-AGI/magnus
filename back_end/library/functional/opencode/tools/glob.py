# back_end/library/functional/opencode/tools/glob.py
# Adapted from: opencode/packages/opencode/src/tool/glob.ts
# Migration strategy: 使用 Python 标准库 glob + pathlib 实现
# 简化：移除 ripgrep 依赖，使用 Python 原生实现
import os
import glob as glob_module
from pathlib import Path
from typing import Any, Dict, List

from .base import Tool, ToolResult, ToolContext, register_tool


DESCRIPTION = """Fast file pattern matching tool that works with any codebase size.

Usage:
- Supports glob patterns like "**/*.py" or "src/**/*.ts"
- Returns matching file paths sorted by modification time (most recent first)
- Use this tool when you need to find files by name patterns

Examples:
- Find all Python files: {"pattern": "**/*.py"}
- Find files in src: {"pattern": "src/**/*.ts", "path": "."}
"""

MAX_RESULTS = 100


class GlobTool(Tool):
    @property
    def name(self)-> str:
        return "glob"

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
                    "description": "The glob pattern to match files against (e.g., '**/*.py')",
                },
                "path": {
                    "type": "string",
                    "description": "The directory to search in. Defaults to workspace root.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, params: Dict[str, Any], ctx: ToolContext)-> ToolResult:
        pattern = params["pattern"]
        search_path = params.get("path", "")

        if search_path:
            search_path = ctx.resolve_host_path(search_path)
        else:
            search_path = ctx.workspace_path

        search_path = os.path.normpath(search_path)

        if not search_path.startswith(ctx.workspace_path):
            raise ValueError(f"Access denied: path is outside workspace")

        if not os.path.isdir(search_path):
            raise ValueError(f"Directory not found: {search_path}")

        full_pattern = os.path.join(search_path, pattern)
        matches = glob_module.glob(full_pattern, recursive=True)

        files_with_mtime: List[tuple] = []
        for match in matches:
            if os.path.isfile(match):
                try:
                    mtime = os.path.getmtime(match)
                    files_with_mtime.append((match, mtime))
                except OSError:
                    files_with_mtime.append((match, 0))

        files_with_mtime.sort(key=lambda x: x[1], reverse=True)

        truncated = len(files_with_mtime) > MAX_RESULTS
        result_files = files_with_mtime[:MAX_RESULTS]

        title = os.path.relpath(search_path, ctx.workspace_path) or "."

        if not result_files:
            return ToolResult(
                title = title,
                output = "No files found",
                metadata = {"count": 0, "truncated": False},
            )

        output_lines = [f for f, _ in result_files]
        if truncated:
            output_lines.append("")
            output_lines.append("(Results are truncated. Consider using a more specific path or pattern.)")

        return ToolResult(
            title = title,
            output = "\n".join(output_lines),
            metadata = {
                "count": len(result_files),
                "truncated": truncated,
            },
        )


register_tool(GlobTool())
