# back_end/library/functional/opencode/tools/base.py
# Adapted from: opencode/packages/opencode/src/tool/tool.ts
# Migration strategy: 将 OpenCode 的 Tool 基类转换为 Python dataclass + Protocol 模式
# 保留核心概念：Tool 定义、执行上下文、结果结构、输出截断
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod
import os
import json


@dataclass
class ToolResult:
    """Tool 执行结果"""
    title: str
    output: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContainerConfig:
    """容器执行配置"""
    sif_path: str
    host_workspace: str
    container_workspace: str = "/magnus"


@dataclass
class ToolContext:
    """Tool 执行上下文"""
    session_id: str
    message_id: str
    workspace_path: str  # host 上的工作区路径
    container: Optional[ContainerConfig] = None
    abort_signal: Optional[Any] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def resolve_host_path(self, path: str)-> str:
        """
        将 LLM 给出的路径解析为 host 上的真实路径

        LLM 通过 bash 工具看到的是容器内路径（如 /magnus/...），
        但 read/glob/grep 直接在 host 上操作。此方法做路径翻译。
        """
        if self.container and path.startswith(self.container.container_workspace):
            suffix = path[len(self.container.container_workspace):]
            if suffix.startswith("/"):
                suffix = suffix[1:]
            return os.path.join(self.container.host_workspace, suffix)

        if not os.path.isabs(path):
            return os.path.join(self.workspace_path, path)

        return path


class Tool(ABC):
    """
    Tool 基类

    参考 OpenCode 的 Tool.define() 模式，但使用 Python 类继承实现
    """

    @property
    @abstractmethod
    def name(self)-> str:
        """Tool 唯一标识"""
        pass

    @property
    @abstractmethod
    def description(self)-> str:
        """Tool 描述，用于 LLM 理解"""
        pass

    @property
    @abstractmethod
    def parameters(self)-> Dict[str, Any]:
        """Tool 参数 JSON Schema"""
        pass

    @abstractmethod
    async def execute(self, params: Dict[str, Any], ctx: ToolContext)-> ToolResult:
        """执行 Tool"""
        pass

    def to_openai_tool(self)-> Dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class ToolRegistry:
    """Tool 注册表"""

    def __init__(self)-> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool)-> None:
        self._tools[tool.name] = tool

    def get(self, name: str)-> Optional[Tool]:
        return self._tools.get(name)

    def list(self)-> List[Tool]:
        return list(self._tools.values())

    def to_openai_tools(self)-> List[Dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self._tools.values()]


# 全局 Tool 注册表
default_registry = ToolRegistry()


def register_tool(tool: Tool)-> Tool:
    """注册 Tool 到默认注册表"""
    default_registry.register(tool)
    return tool
