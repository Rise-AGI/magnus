# back_end/library/functional/opencode/tools/bash.py
# Adapted from: opencode/packages/opencode/src/tool/bash.ts
# Migration strategy: 使用 Python subprocess 实现命令执行
# 支持通过 apptainer 容器执行，workspace 挂载到 /magnus
import os
import asyncio
from typing import Any, Dict

from .base import Tool, ToolResult, ToolContext, register_tool


DESCRIPTION = """Execute a bash command in the workspace.

Usage:
- Commands are executed in the /magnus directory inside a container
- Timeout defaults to 120 seconds (max 600 seconds)
- Output is captured and returned
- Use this for running scripts, checking files, etc.

Examples:
- List files: {"command": "ls -la", "description": "List files in current directory"}
- Check path: {"command": "pwd", "description": "Print working directory"}
"""

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
MAX_OUTPUT_LENGTH = 50000


class BashTool(Tool):
    @property
    def name(self)-> str:
        return "bash"

    @property
    def description(self)-> str:
        return DESCRIPTION

    @property
    def parameters(self)-> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Optional timeout in seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what this command does (5-10 words)",
                },
            },
            "required": ["command", "description"],
        }

    async def execute(self, params: Dict[str, Any], ctx: ToolContext)-> ToolResult:
        command = params["command"]
        timeout = min(params.get("timeout", DEFAULT_TIMEOUT), MAX_TIMEOUT)
        description = params.get("description", command[:50])

        timed_out = False
        output = ""

        if ctx.container:
            full_command = [
                "apptainer", "exec",
                "--bind", f"{ctx.container.host_workspace}:{ctx.container.container_workspace}",
                "--pwd", ctx.container.container_workspace,
                ctx.container.sif_path,
                "bash", "--norc", "--noprofile", "-c", command,
            ]
        else:
            full_command = ["bash", "--norc", "--noprofile", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_command,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.STDOUT,
                env = {**os.environ, "TERM": "dumb"},
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(),
                    timeout = timeout,
                )
                output = stdout.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                try:
                    stdout, _ = await proc.communicate()
                    output = stdout.decode("utf-8", errors="replace")
                except Exception:
                    pass

            exit_code = proc.returncode

        except Exception as e:
            output = f"Error executing command: {e}"
            exit_code = -1

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + "\n\n... (output truncated)"

        metadata_lines = []
        if timed_out:
            metadata_lines.append(f"Command timed out after {timeout} seconds")

        if metadata_lines:
            output += "\n\n<bash_metadata>\n" + "\n".join(metadata_lines) + "\n</bash_metadata>"

        return ToolResult(
            title = description,
            output = output,
            metadata = {
                "exit_code": exit_code,
                "timed_out": timed_out,
                "description": description,
            },
        )


register_tool(BashTool())
