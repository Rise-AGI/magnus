# 卫家燊交付文档

本文档覆盖两个工作流：**人事页面聊天功能**和**鉴权中台对接**。

---

## 一、人事页面聊天功能

### 1.1 产品定义

在 People 页面为组织成员提供即时通讯能力：

- **P2P 私聊**：人与人、人与 Agent
- **群组聊天**：支持多人群组
- **Agent 下属**：Agent 用户暂时只读（已读不回），但接口层面需要预留 Agent 自动回复的扩展点
- **布局**：仿 Explorer 的 sidebar + main content 双栏模式

### 1.2 Explorer 布局架构参考

Explorer 的前端结构值得复用，下面是它的架构拆解。

#### 文件结构

```
front_end/src/app/(main)/explorer/
├── layout.tsx            # 双栏 shell：左 sidebar（会话列表）+ 右 {children}
├── page.tsx              # 落地页（无选中会话时展示，输入即创建会话）
└── [sessionId]/page.tsx  # 聊天页（消息流 + 输入框）
```

#### 布局 CSS 骨架

```
┌──────────────────────────────────────────────┐
│ (main) layout — 全局导航栏                      │
│ ┌──────────┬───────────────────────────────┐  │
│ │ Sidebar  │  {children}                   │  │
│ │ w-56     │  flex-1                       │  │
│ │ 会话列表  │  消息区 + 输入区               │  │
│ │ 无限滚动  │  max-w-3xl mx-auto           │  │
│ └──────────┴───────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

关键 CSS：外层 `flex h-full w-full overflow-hidden`，sidebar `w-56 flex-shrink-0 border-r flex flex-col`，内容区 `flex-1 flex flex-col min-h-0 min-w-0`。

#### 聊天页三段式

```
┌─────────────────────────────┐
│ 消息区 (flex-1, scrollable) │  ← 自动滚动（仅当用户在底部 100px 内）
│   用户消息靠右 / 对方靠左    │
│   pb-32 留出底部空间         │
├─────────────────────────────┤
│ 渐变遮罩 (h-24)             │  ← pointer-events-none
├─────────────────────────────┤
│ 输入区 (sticky bottom)      │  ← textarea 自动高度 + 发送按钮
│   max-w-3xl mx-auto         │
└─────────────────────────────┘
```

#### 通信模式

| 模式            | Explorer 做法                                                   | 聊天功能建议                                     |
| ------------- | ------------------------------------------------------------- | ------------------------------------------ |
| Sidebar ↔ 内容区 | `window.dispatchEvent(new Event("explorer-sessions-update"))` | 可复用同一模式，或改用 React context/zustand          |
| 实时消息          | `fetch` + `ReadableStream`（流式 LLM）                            | **WebSocket** 更适合双向聊天。流式 LLM 是单向推送，IM 是双向的 |
| 跨页数据传递        | `sessionStorage` 暂存 pending 消息                                | 不需要，聊天不存在"创建再跳转"的问题                        |

### 1.3 可复用组件清单

以下组件已存在于项目中，可直接使用：

| 组件                   | 路径                                      | 说明                             |
| -------------------- | --------------------------------------- | ------------------------------ |
| `RenderMarkdown`     | `components/ui/render-markdown.tsx`     | Markdown 渲染，含代码高亮、KaTeX、GFM 表格 |
| `ConfirmationDialog` | `components/ui/confirmation-dialog.tsx` | 通用确认弹窗                         |
| `CopyableText`       | `components/ui/copyable-text.tsx`       | 一键复制文本                         |
| `Drawer`             | `components/ui/drawer.tsx`              | 侧边抽屉（群组信息、成员列表等场景）             |
| `UserAvatar`         | `components/ui/user-avatar.tsx`         | 用户头像组件                         |
| `PaginationControls` | `components/ui/pagination-controls.tsx` | 分页控件                           |
| `client()`           | `lib/api.ts`                            | API 客户端，自动注入 token，处理 401/204  |

以下是 Explorer 内联定义但**值得提取为共享组件**的部分（合代码时机操作）：

| 组件                  | 当前位置                                                 | 聊天场景用途              |
| ------------------- | ---------------------------------------------------- | ------------------- |
| `ImagePreviewModal` | `explorer/[sessionId]/page.tsx`                      | 图片消息点击放大            |
| 自动高度 textarea       | `explorer/page.tsx` + `[sessionId]/page.tsx`（重复 3 次） | 聊天输入框               |
| `ThinkingBlock`     | `explorer/[sessionId]/page.tsx`                      | 未来 Agent 回复时的推理过程展示 |

### 1.4 建议数据模型

```python
# 以下为建议，家燊可自行调整

class Conversation(Base):
    """会话（P2P 或群组）"""
    __tablename__ = "conversations"
    id: Mapped[str]                         # hex ID
    type: Mapped[str]                       # "p2p" | "group"
    name: Mapped[str | None]                # 群组名称，P2P 为 null
    avatar_url: Mapped[str | None]          # 群组头像
    created_by: Mapped[str]                 # FK -> users.id
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]            # 最新消息时间，用于排序

class ConversationMember(Base):
    """会话成员"""
    __tablename__ = "conversation_members"
    id: Mapped[int]
    conversation_id: Mapped[str]            # FK -> conversations.id
    user_id: Mapped[str]                    # FK -> users.id
    role: Mapped[str]                       # "owner" | "member"
    last_read_at: Mapped[datetime | None]   # 已读水位线
    joined_at: Mapped[datetime]

class Message(Base):
    """消息"""
    __tablename__ = "messages"
    id: Mapped[str]                         # hex ID
    conversation_id: Mapped[str]            # FK -> conversations.id
    sender_id: Mapped[str]                  # FK -> users.id
    content: Mapped[str]                    # 消息正文（Markdown）
    message_type: Mapped[str]               # "text" | "image" | "file" | "system"
    created_at: Mapped[datetime]
```

### 1.5 Agent 扩展预留

当前 Agent 用户（`user_type = "agent"`）只需作为会话成员存在，不产生回复。扩展点：

```python
# 未来在 Message 创建后触发 hook
# 路由层伪码：
async def send_message(...):
    msg = create_message(...)
    # 扩展点：通知会话中的 Agent 成员
    for member in conversation.members:
        if member.user.user_type == "agent" and member.user.webhook_url:
            background_tasks.add_task(notify_agent, member.user, msg)
    return msg
```

建议在 `User` 模型上预留 `webhook_url: Mapped[str | None]` 字段（当前可为 null），未来 Agent 通过 webhook 接收消息并回复。

### 1.6 建议路由结构

遵循项目 API 约定（`/api/{resource}` + `/api/{resource}/{id}/{action}`）：

```
POST   /api/conversations                          # 创建会话
GET    /api/conversations                          # 列出我的会话
GET    /api/conversations/{id}                     # 会话详情（含成员）
DELETE /api/conversations/{id}                     # 删除会话（204）
POST   /api/conversations/{id}/members             # 添加成员
DELETE /api/conversations/{id}/members/{user_id}   # 移除成员（204）
GET    /api/conversations/{id}/messages            # 消息列表（分页，按时间倒序）
POST   /api/conversations/{id}/messages            # 发送消息
POST   /api/conversations/{id}/read                # 标记已读（更新 last_read_at）
```

前端路由建议：

```
/people/chat                    # 聊天落地页
/people/chat/{conversationId}   # 具体会话
```

或者作为独立顶级页面 `/chat`，取决于产品决策。

### 1.7 与 Explorer 的关系

Explorer 是 AI 对话（单用户 ↔ LLM），聊天是 IM（多用户 ↔ 多用户）。二者共享：

- 布局模式（sidebar + content 双栏）
- 消息渲染（Markdown、图片预览）
- 输入组件（自动高度 textarea）

**建议合代码时**从 Explorer 提取以下共享组件到 `components/chat/`：

- `ChatInput`：textarea + 发送按钮 + 附件 + paste 处理
- `MessageBubble`：消息气泡（左/右对齐）
- `ImagePreviewModal`：图片灯箱

提取后 Explorer 同步改为引用共享组件，实现双向升级。

---

## 二、鉴权中台对接

### 2.1 当前飞书耦合全景

Magnus 当前与飞书 OAuth 2.0 深度绑定。以下是全部触点：

#### 后端（Python / FastAPI）

| 层级           | 文件                                   | 耦合内容                                                                         |
| ------------ | ------------------------------------ | ---------------------------------------------------------------------------- |
| **核心鉴权**     | `server/routers/auth.py`             | `POST /api/auth/feishu/login`：接收飞书授权码 → 换取用户信息 → upsert 用户 → 签发 JWT          |
| **飞书 SDK**   | `library/functional/feishu_tools.py` | `FeishuClient` 类：tenant token 获取、OAuth code 换 user info、按 open_id 查询用户资料     |
| **单例**       | `server/_feishu_client.py`           | 读取配置实例化 `FeishuClient`，导出 `feishu_client` 单例                                 |
| **配置校验**     | `server/_magnus_config.py`           | 硬编码 `provider == "feishu"` 检查（68 行），解析 admins 列表为 `admin_open_ids: Set[str]` |
| **数据模型**     | `server/models.py`                   | `User.feishu_open_id`：唯一索引，nullable（Agent 用户无飞书身份）                           |
| **Admin 判定** | 8 个 router 文件                        | `current_user.feishu_open_id in admin_open_ids` 模式遍布全项目                      |
| **后台刷新**     | `server/main.py`                     | 定期调用飞书 API 刷新所有用户的 name/avatar_url                                           |
| **数据库迁移**    | `python_scripts/migrate_database.py` | `feishu_open_id` 列的 nullable 迁移                                              |

#### 前端（TypeScript / Next.js）

| 文件                           | 耦合内容                                                                                   |
| ---------------------------- | -------------------------------------------------------------------------------------- |
| `next.config.mjs`            | 注入 `NEXT_PUBLIC_FEISHU_APP_ID`                                                         |
| `lib/config.ts`              | 导出 `FEISHU_APP_ID` 常量                                                                  |
| `context/auth-context.tsx`   | 构造飞书 OAuth URL `https://open.feishu.cn/open-apis/authen/v1/authorize?app_id=...`，重定向用户 |
| `app/auth/callback/page.tsx` | 将飞书回调 code POST 到 `/api/auth/feishu/login`                                             |
| `types/auth.ts`              | `User` 接口含 `feishu_open_id: string`                                                    |
| 6 个表格组件                      | fallback 用户对象中 `feishu_open_id: ""` 占位                                                 |
| i18n                         | "Sign in with Feishu" 等文案                                                              |

#### 配置

```yaml
# configs/magnus_config.yaml
server:
  auth:
    provider: feishu                # 当前硬编码只接受 "feishu"
    feishu_client:
      app_id: cli_xxx
      app_secret: xxx
      admins:                       # 飞书 open_id 列表
        - ou_xxx
      refresh_interval: 3600
```

### 2.2 对鉴权中台的普适性期待

Magnus 对外部鉴权系统的需求可以抽象为以下接口契约。**鉴权中台只需满足这些，Magnus 侧的适配由我们自己做。**

#### 必须提供

| 能力                                    | 说明                                         | 当前飞书等价物                                                     |
| ------------------------------------- | ------------------------------------------ | ----------------------------------------------------------- |
| **OAuth 2.0 Authorization Code Flow** | 标准 OAuth：重定向 → 用户授权 → 回调带 code → 后端换 token | 飞书 OAuth `/authen/v1/authorize`                             |
| **Code → User Info 接口**               | 后端用 authorization code 换取用户基本信息            | `POST /authen/v1/access_token` → `GET /authen/v1/user_info` |
| **用户唯一标识符**                           | 每个用户一个不可变的唯一 ID（类似飞书 open_id）              | `open_id` / `union_id`                                      |
| **用户基本信息**                            | 至少包含：`name`（显示名）、`avatar_url`（头像 URL）      | 飞书用户资料                                                      |

#### 强烈建议

| 能力              | 说明                      | 当前飞书等价物                           |
| --------------- | ----------------------- | --------------------------------- |
| **按 ID 查询用户信息** | 后端定期刷新用户名/头像（用户改名后自动同步） | `GET /contact/v3/users/{open_id}` |
| **用户邮箱**        | 首次登录时获取 email           | 飞书用户资料中的 `email` 字段               |

#### 可选但有用

| 能力             | 说明                                      |
| -------------- | --------------------------------------- |
| **用户组/角色**     | 中台侧管理管理员角色，而非 Magnus 配置文件硬编码 open_id 列表 |
| **Webhook 通知** | 用户信息变更时主动推送，取代 Magnus 的定时轮询刷新           |

### 2.3 Magnus 侧迁移工作量评估

按影响程度分层，以下是 Magnus 侧需要做的改动：

#### 高影响（核心鉴权链路）

| 改动                                        | 工作量 | 说明                                                                              |
| ----------------------------------------- | --- | ------------------------------------------------------------------------------- |
| `auth.py` 登录端点                            | 中   | 将 `POST /api/auth/feishu/login` 改为通用 `POST /api/auth/login`，内部按 `provider` 配置分发 |
| `_feishu_client.py` → `_auth_provider.py` | 中   | 抽象为 provider 接口，飞书和中台各实现一个                                                      |
| `_magnus_config.py`                       | 小   | 去掉 `provider == "feishu"` 硬编码，改为配置驱动                                            |
| `models.py`                               | 小   | `feishu_open_id` → `external_id`（rename + migration）                            |
| `main.py` 后台刷新                            | 小   | 改为调用 provider 接口而非直接调飞书                                                         |

#### 中影响（机械替换）

| 改动                        | 工作量 | 说明                                                                                                                          |
| ------------------------- | --- | --------------------------------------------------------------------------------------------------------------------------- |
| 8 个 router 的 admin 判定     | 小   | `current_user.feishu_open_id in admin_open_ids` → `current_user.external_id in admin_ids` 或 `current_user.is_admin`（全局搜索替换） |
| 前端 auth-context.tsx       | 小   | OAuth URL 改为从后端获取（`GET /api/auth/config` 返回 authorize URL），而非前端硬编码飞书地址                                                      |
| 前端 callback/page.tsx      | 小   | 端点路径从 `/api/auth/feishu/login` → `/api/auth/login`                                                                          |
| `types/auth.ts` + 6 个表格组件 | 小   | `feishu_open_id` → `external_id`，机械替换                                                                                       |

#### 低影响（收尾）

| 改动                                | 工作量 |
| --------------------------------- | --- |
| i18n "Sign in with Feishu" → 动态文案 | 极小  |
| `configs/magnus_config.yaml` 结构调整 | 极小  |
| README / SDK Guide 文档更新           | 极小  |
| 数据库迁移脚本（rename column）            | 极小  |

**总估计**：Magnus 侧全量迁移约 1-2 天工作量，大部分是机械替换。核心改动集中在 `auth.py` 和 `_auth_provider.py`。

### 2.4 建议的 Provider 抽象

```python
# 以下为建议的 provider 接口，供家燊参考中台的 API 设计

class AuthUserInfo:
    external_id: str        # 用户在中台的唯一 ID
    name: str               # 显示名
    avatar_url: str | None  # 头像 URL
    email: str | None       # 邮箱

class AuthProvider:
    """鉴权提供者接口"""

    def exchange_code(self, code: str) -> AuthUserInfo:
        """用 OAuth authorization code 换取用户信息"""
        ...

    def get_user_info(self, external_id: str) -> AuthUserInfo | None:
        """按 ID 查询用户最新信息（用于后台刷新）"""
        ...

    def get_authorize_url(self, redirect_uri: str) -> str:
        """生成 OAuth 授权页 URL"""
        ...
```

Magnus 侧根据配置 `auth.provider` 实例化对应实现：

```yaml
server:
  auth:
    provider: pku_physics    # "feishu" | "pku_physics" | ...
    admins:
      - "external_id_1"
      - "external_id_2"
    pku_physics:             # provider-specific config
      client_id: xxx
      client_secret: xxx
      base_url: https://auth.phy.pku.edu.cn
```

---

## 三、开发环境与约定速查

### 3.1 本地启动

```bash
# 后端
cd back_end && uv sync && uv run -m server.main

# 前端
cd front_end && npm install && npm run dev
```

开发模式端口自动 +2，数据目录后缀 `-develop`。

### 3.2 项目约定

- **API 路由**：`/api/{resource}` (GET 列表, POST 创建) / `{id}` (GET/PATCH/DELETE) / `{id}/{action}` (POST)
- **分页响应**：`{"total": int, "items": [...]}`
- **DELETE 返回 204 No Content**（无 body）
- **认证**：所有端点通过 `Depends(get_current_user)` 注入当前用户
- **后端文件**：router 一个资源一个文件放 `routers/`，管理器单例放 `server/_*.py`
- **前端组件**：`components/{feature}/` 按功能分目录，共享 UI 在 `components/ui/`
- **i18n**：`front_end/src/context/language-context.tsx` 中维护中英双语
- **Git 提交**：`[module] type: description`，如 `[chat] feat: add P2P messaging`

### 3.3 关键文件索引

| 用途              | 路径                                                       |
| --------------- | -------------------------------------------------------- |
| 数据模型            | `back_end/server/models.py`                              |
| Pydantic schema | `back_end/server/schemas.py`                             |
| 路由注册            | `back_end/server/routers/__init__.py`                    |
| 前端类型定义          | `front_end/src/types/`                                   |
| Explorer 布局参考   | `front_end/src/app/(main)/explorer/layout.tsx`           |
| Explorer 聊天参考   | `front_end/src/app/(main)/explorer/[sessionId]/page.tsx` |
| People 页面       | `front_end/src/app/(main)/people/page.tsx`               |
| People 组件       | `front_end/src/components/people/`                       |
| 配置模板            | `configs/magnus_config.yaml.example`                     |
| 数据库迁移           | `back_end/python_scripts/migrate_database.py`            |
| 编码规范            | `CLAUDE.md`                                              |
