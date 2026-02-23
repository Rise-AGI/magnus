# Magnus OpenCode Integration

本目录记录 Magnus Explorer 升级过程中从 OpenCode 项目迁移的代码和设计决策。

## 概述

Magnus Explorer 的智能体升级深度集成了 [OpenCode](https://github.com/anomalyco/opencode)（MIT 开源）的核心能力：

- **Agentic Loop**：Tool calling 循环、流式响应、多轮对话
- **Tool 系统**：read、glob、grep、bash 等工具
- **Context Engineering**：上下文管理和截断

## 文件映射

| Magnus 文件 | OpenCode 源文件 | 说明 |
|------------|----------------|------|
| `library/functional/opencode/agent.py` | `session/processor.ts`, `session/llm.ts` | Agentic Loop 核心 |
| `library/functional/opencode/tools/base.py` | `tool/tool.ts` | Tool 基类和注册表 |
| `library/functional/opencode/tools/read.py` | `tool/read.ts` | 文件读取工具 |
| `library/functional/opencode/tools/glob.py` | `tool/glob.ts` | 文件模式匹配工具 |
| `library/functional/opencode/tools/grep.py` | `tool/grep.ts` | 内容搜索工具 |
| `library/functional/opencode/tools/bash.py` | `tool/bash.ts` | 命令执行工具 |

## 迁移策略

### 1. 去芜存菁

**保留**：
- Agentic Loop 核心逻辑
- Tool 定义和执行框架
- 流式响应处理
- 输出截断机制

**移除**：
- TUI (Terminal UI)
- Electron 桌面应用
- LSP 集成
- 多 Provider 支持（仅保留 Qwen API）
- 权限系统（简化为工作区限制）

### 2. Python 化

- TypeScript → Python dataclass + async/await
- Zod schema → JSON Schema dict
- Bun 文件 API → Python pathlib + open()
- ripgrep → Python re + os.walk()

### 3. Naive 实现

以下模块采用简化实现，命名显式包含 `naive`：

- `NaiveContextManager`：简单的消息数量限制，不做 token 计算

## API 端点

新增端点：`POST /api/explorer/sessions/{session_id}/chat`

返回 SSE 格式的流式响应：

```json
{"type": "text", "content": "..."}
{"type": "thinking", "content": "..."}
{"type": "tool_call", "tool": "read", "input": {...}}
{"type": "tool_result", "tool": "read", "result": "..."}
{"type": "done", "finish_reason": "stop"}
```

## 后续计划

1. **权限系统**：参考 OpenCode 的 `permission/next.ts` 实现细粒度权限控制
2. **Doom Loop 检测**：检测重复工具调用，避免无限循环
3. **智能上下文压缩**：参考 `session/compaction.ts` 实现 token 感知的压缩
4. **更多工具**：write、edit、task 等

## 致谢

感谢 [OpenCode](https://github.com/anomalyco/opencode) 项目提供的优秀开源实现。
