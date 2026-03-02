# CLAUDE.md - Magnus 项目 AI 协作指南

## 项目概述

Magnus 是 Rise-AGI 的科学计算与机器学习基础设施平台，集成智能调度、资源监控、用户认证等企业级功能。

**技术栈**：
- 后端：FastAPI + SQLAlchemy + PyJWT (Python ≥3.14)
- 前端：Next.js 14 + React 18 + TypeScript 5+ + Tailwind CSS
- 调度：SLURM 集群 + 自研四级优先级调度器
- 认证：飞书 OAuth 2.0 + JWT
- 智能体：OpenAI 兼容 API + 受限 builtins 沙箱

**发展方向**：
- 可扩展性：Skill 插件架构，支持动态加载能力
- 容器化：Docker 镜像标准化，K8s 编排就绪
- Explorer 智能化：多模态理解、工具调用、会话记忆

---

## 开发原则

### 1. 敏捷优先
用户完全信任你，直接动手，快速迭代。不要过度思考、不要反复确认。

### 2. Fast Fail
不要用 try-except 包裹不该包裹的代码。让错误尽早暴露。

### 3. 组件复用
前端已有 UI 组件优先复用，查看 `components/ui/` 目录。

### 4. 增量修改
使用 Edit 工具增量修改文件，不要用 Write 全量重写。

---

## Python 编码规范

### 风格要点

1. **少用注释**：只在用户不了解的 API、关键步骤或需要备忘的地方加注释
2. **不加 docstring**：除非用户主动要求
3. **变量命名**：见名知意，用完整英语单词；库遵循社区缩略语（`np`, `pd`）
4. **脚本入口**：脚本必须用 `if __name__ == "__main__": main()`
5. **类型标注**：使用 `from typing import Any, Dict, List, Optional`（旧版风格）
6. **类型范围**：函数签名必须标注；栈上可推断类型不必标注
7. **类型安全**：必须通过 pylance 审查，用 `assert x is not None` 消除警告

### 格式要点

```python
# 函数签名：入参一行一个，返回箭头左边无空格
def create_job(
    task_name: str,
    gpu_count: int,
    priority: JobType,
) -> models.Job:
    ...

# 多行容器：最后元素也加逗号
items = [
    "first",
    "second",
    "third",
]
```

---

## 架构约定

### 目录哲学

```
back_end/
├── library/           # 项目无关，跨项目可复用（禁止出现 Magnus 字样）
│   ├── fundamental/   # 基础工具（无脑调用）
│   └── functional/    # 功能模块（需理解后使用）
└── server/            # Magnus 特定代码
    ├── routers/       # API 路由（每个资源一个文件）
    └── _*.py          # 内部管理器（单例模式）
```

### 管理器单例模式

核心逻辑封装在 `_*_manager.py` 中，模块级单例实例化：

```python
# _scheduler.py
class MagnusScheduler:
    def __init__(self): ...
    async def start_background_loop(self): ...

scheduler = MagnusScheduler()  # 单例
```

使用方：`from .._scheduler import scheduler`

### 生命周期管理

后台任务通过 FastAPI lifespan 管理：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scheduler.start_background_loop())
    yield
    task.cancel()
```

### 配置加载

- 单一配置源：`configs/magnus_config.yaml`
- 后端直接读取 YAML
- 前端通过 `NEXT_PUBLIC_*` 环境变量注入
- 开发模式自动端口 +2，路径后缀 `-develop`

**配置读取原则**：
- **必须**用 `magnus_config[key]` 直接访问，禁止 `.get()` 带默认值
- Quick fail 好于模模糊糊地运行不休
- 必要的配置项在 `_magnus_config.py` 加载时验证存在性和类型
- **修改配置结构时**，必须同步更新 `configs/magnus_config.yaml` 和 `configs/magnus_config.yaml.example`，同时更新 `_magnus_config.py`

---

## Explorer 智能体架构

Explorer 是 Magnus 的 AI 对话界面，支持多模态理解和工具调用。

### 核心模式

```python
# 流式响应：线程 + 队列 + SSE
def _stream_generator(session_id: str):
    while True:
        chunk = chunks_dict[session_id].get(timeout=30)
        if chunk is None: break
        yield chunk

# 思考块：LLM 推理过程
extra_body = {"enable_thinking": True}  # 返回 <think>...</think>

# 视觉模型：上下文感知
messages_for_vlm = messages[-6:]  # 最近 6 条消息提供上下文
```

### Skill 系统

Skill 是多文件知识包，每个 Skill 包含 `SKILL.md`（必须）和任意附件文件。通过 Web 编辑器或 SDK/CLI 进行 CRUD 管理。Owner 和 Admin 权限控制。

### 镜像管理

容器镜像缓存的完整生命周期管理。拉取/刷新在后台异步执行（返回 202），刷新期间旧镜像保持可用（原子替换）。服务器重启后自动恢复中断的拉取状态。

---

## API 约定

### 路由命名

```
/api/{resource}           # 列表: GET, 创建: POST
/api/{resource}/{id}      # 详情: GET, 更新: PATCH, 删除: DELETE
/api/{resource}/{id}/{action}  # 动作: POST
```

### 响应格式

```python
# 分页列表
{"total": int, "items": [...]}

# 错误响应
{"detail": "error message"}
```

### 认证

- Bearer token 在 Authorization header
- `get_current_user` 依赖注入验证
- TTL 缓存优化（60s, 1000 条）

---

## 国际化 (i18n)

```tsx
import { useLanguage } from "@/context/language-context";

const { t } = useLanguage();
t("jobs.table.task")           // 简单键
t("jobForm.default", { v: 64 }) // 插值
```

---

## 核心业务概念

### 四级优先级调度
- **A1/A2**：高优先级，不可被抢占
- **B1/B2**：低优先级，可被 A 类抢占

### Blueprint 系统
Python 函数签名 → 前端表单。`Annotated` 类型注解定义 UI 元数据。

**Preference 签名哈希**：用户的蓝图参数偏好通过 `BlueprintParamSchema` 的 `model_dump()` → JSON → SHA256 关联到蓝图签名。给 `BlueprintParamSchema` 增删字段会改变哈希，导致所有用户的已缓存偏好失效。修改此 Schema 前须评估影响。

### 弹性服务
支持 scale-to-zero，空闲超时自动缩容。

---

## 常用命令

```bash
# 后端
cd back_end && uv sync && uv run -m server.main

# 前端
cd front_end && npm install && npm run dev

# 类型检查
cd front_end && npx tsc --noEmit
```

---

## Git 提交规范

```
[module] type: short description

- detail 1
- detail 2
```

**module**：改动涉及的模块，如 `security`, `explorer`, `i18n`, `config`, `jobs`, `blueprints`

**type**：
- `feat`：新功能
- `fix`：修复

**示例**：
```
[security] feat: sandbox blueprint exec, fix cache leak & port race
[i18n] fix: let's get more i18nal
[explorer] feat: add session sharing
```

---

## 协作红线

1. **禁止一切非本地持久化**：不要 git push、不要 git commit（除非用户明确要求）、不要写远程数据库、不要调外部 API 产生副作用。所有变更仅停留在本地工作区，由用户自行 review 后决定是否持久化
2. **library 纯净**：library 里不能有 Magnus 相关逻辑
3. **不管 SDK 版本号**：版本号由用户掌管
4. **沙箱执行**：用户代码（Blueprint）在受限 builtins 环境运行，仅允许 `typing` 模块导入
