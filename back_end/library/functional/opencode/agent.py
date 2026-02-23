# back_end/library/functional/opencode/agent.py
# Adapted from: opencode/packages/opencode/src/session/processor.ts, opencode/packages/opencode/src/session/llm.ts
# Migration strategy: 将 OpenCode 的 Agentic Loop 转换为 Python async generator 模式
# 核心功能：流式响应、Tool calling 循环、多轮对话、上下文管理
# 简化：移除权限系统、doom loop 检测（后续可扩展）
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncGenerator

from openai import OpenAI

from .tools.base import ToolResult, ToolContext, ToolRegistry, ContainerConfig, default_registry


logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Agent 配置"""
    api_key: str
    base_url: str
    model_name: str
    max_steps: int = 50
    enable_thinking: bool = True


@dataclass
class StreamEvent:
    """流式事件基类"""
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TextDeltaEvent(StreamEvent):
    """文本增量事件"""
    type: str = "text_delta"
    text: str = ""


@dataclass
class ThinkingDeltaEvent(StreamEvent):
    """思考增量事件"""
    type: str = "thinking_delta"
    text: str = ""


@dataclass
class ToolCallEvent(StreamEvent):
    """Tool 调用事件"""
    type: str = "tool_call"
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolResultEvent(StreamEvent):
    """Tool 结果事件"""
    type: str = "tool_result"
    tool_name: str = ""
    call_id: str = ""
    result: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorEvent(StreamEvent):
    """错误事件"""
    type: str = "error"
    error: str = ""


@dataclass
class DoneEvent(StreamEvent):
    """完成事件"""
    type: str = "done"
    finish_reason: str = ""


class Agent:
    """
    Agentic Loop 核心类

    参考 OpenCode 的 SessionProcessor 和 LLM 模块实现
    支持流式响应、Tool calling、多轮对话
    """

    def __init__(
        self,
        config: AgentConfig,
        tool_registry: Optional[ToolRegistry] = None,
    )-> None:
        self.config = config
        self.tool_registry = tool_registry or default_registry
        self.client = OpenAI(
            api_key = config.api_key,
            base_url = config.base_url,
        )

    def _build_tools(self)-> List[Dict[str, Any]]:
        """构建 OpenAI tools 参数"""
        return self.tool_registry.to_openai_tools()

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        ctx: ToolContext,
    )-> ToolResult:
        """执行单个 Tool"""
        tool = self.tool_registry.get(tool_name)
        if not tool:
            return ToolResult(
                title = tool_name,
                output = f"Error: Unknown tool '{tool_name}'",
                metadata = {"error": True},
            )

        try:
            return await tool.execute(tool_input, ctx)
        except Exception as e:
            logger.error(f"Tool execution error: {tool_name}", exc_info=True)
            return ToolResult(
                title = tool_name,
                output = f"Error: {str(e)}",
                metadata = {"error": True},
            )

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        message_id: str,
        workspace_path: str,
        system_prompt: Optional[str] = None,
        container: Optional[ContainerConfig] = None,
    )-> AsyncGenerator[StreamEvent, None]:
        """
        流式执行 Agent

        参考 OpenCode 的 SessionProcessor.process() 实现
        支持多轮 Tool calling 直到 LLM 返回最终响应
        """
        tools = self._build_tools()
        ctx = ToolContext(
            session_id = session_id,
            message_id = message_id,
            workspace_path = workspace_path,
            container = container,
            messages = messages,
        )

        current_messages = messages.copy()
        if system_prompt:
            current_messages = [{"role": "system", "content": system_prompt}] + current_messages

        step = 0

        while step < self.config.max_steps:
            step += 1

            try:
                extra_body: Dict[str, Any] = {}
                if self.config.enable_thinking:
                    extra_body["enable_thinking"] = True

                create_params: Dict[str, Any] = {
                    "model": self.config.model_name,
                    "messages": current_messages,
                    "stream": True,
                }
                if tools:
                    create_params["tools"] = tools
                if extra_body:
                    create_params["extra_body"] = extra_body

                stream = self.client.chat.completions.create(**create_params)

                full_thinking = ""
                full_response = ""
                tool_calls: Dict[int, Dict[str, Any]] = {}
                in_thinking = False
                thinking_started_this_round = False
                finish_reason = None

                for chunk in stream:
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta
                    finish_reason = choice.finish_reason

                    reasoning_content = getattr(delta, "reasoning_content", None)
                    if reasoning_content:
                        if not in_thinking:
                            in_thinking = True
                            if not thinking_started_this_round:
                                thinking_started_this_round = True
                                yield ThinkingDeltaEvent(text="<think>")
                        full_thinking += reasoning_content
                        yield ThinkingDeltaEvent(text=reasoning_content)

                    if delta.content:
                        if in_thinking:
                            in_thinking = False
                            yield ThinkingDeltaEvent(text="</think>")
                        full_response += delta.content
                        yield TextDeltaEvent(text=delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls:
                                tool_calls[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.id:
                                tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls[idx]["arguments"] += tc.function.arguments

                # 这一轮结束，如果还在 thinking 状态，关闭它
                if in_thinking:
                    yield ThinkingDeltaEvent(text="</think>")

                if not tool_calls:
                    yield DoneEvent(finish_reason=finish_reason or "stop")
                    return

                assistant_message: Dict[str, Any] = {"role": "assistant", "content": full_response or None}
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in tool_calls.values()
                    ]
                current_messages.append(assistant_message)

                for tc in tool_calls.values():
                    tool_name = tc["name"]
                    call_id = tc["id"]

                    try:
                        tool_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_input = {}

                    yield ToolCallEvent(
                        tool_name = tool_name,
                        tool_input = tool_input,
                        call_id = call_id,
                    )

                    result = await self._execute_tool(tool_name, tool_input, ctx)

                    yield ToolResultEvent(
                        tool_name = tool_name,
                        call_id = call_id,
                        result = result.output,
                        metadata = result.metadata,
                    )

                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result.output,
                    })

            except Exception as e:
                logger.error("Agent stream error", exc_info=True)
                yield ErrorEvent(error=str(e))
                return

        yield ErrorEvent(error=f"Max steps ({self.config.max_steps}) exceeded")


class NaiveContextManager:
    """
    Naive 上下文管理器

    参考 OpenCode 的 SessionCompaction 实现
    简化版本：仅做消息数量限制，不做 token 计算和智能压缩
    """

    def __init__(self, max_messages: int = 50)-> None:
        self.max_messages = max_messages

    def truncate(self, messages: List[Dict[str, Any]])-> List[Dict[str, Any]]:
        """截断消息列表，保留最近的消息"""
        if len(messages) <= self.max_messages:
            return messages

        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        keep_count = self.max_messages - len(system_messages)
        if keep_count <= 0:
            return system_messages[:self.max_messages]

        return system_messages + non_system[-keep_count:]
