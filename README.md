<!-- README.md -->
# Magnus - PKU Plasma & Rise-AGI 计算基础设施平台

![Magnus Logo](https://img.shields.io/badge/Magnus-Platform-blue)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-3178c6)
![Python](https://img.shields.io/badge/Python-3.14+-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-teal)
![Image](https://img.shields.io/badge/Image-Apptainer-2496ED?logo=docker&logoColor=white)
![SLURM](https://img.shields.io/badge/Scheduler-SLURM-bf202f)


**Magnus** 是 PKU Plasma 和 Rise-AGI 的科学计算与机器学习计算基础设施，集成了智能调度、资源监控、用户认证等企业级功能。

## 🚀 核心特性

### 🎯 智能调度系统
- **四级优先级调度**: A1/A2/B1/B2 四级任务优先级
- **抢占式调度**: A类任务可抢占B类任务资源
- **实时状态同步**: SLURM ↔ 数据库双向状态同步
- **心跳调度器**: 每2秒执行一次调度决策

### 🔧 SLURM 集群集成
- **严格环境检查**: 自动验证 SLURM 命令可用性
- **资源监控**: 实时查询集群空闲 GPU 资源
- **任务管理**: 完整的任务提交、查询、终止功能
- **容器化执行**: 基于 Apptainer 的隔离执行环境，支持自定义容器镜像

### 👥 企业级用户系统
- **飞书 OAuth 2.0 认证**: 支持飞书扫码登录
- **JWT 令牌管理**: 7天有效期的访问令牌
- **用户头像集成**: 显示用户飞书头像和基本信息
- **多用户支持**: 完整的用户管理和权限控制

### 📊 现代化 Web 界面
- **Next.js 14 + TypeScript**: 现代化的前端技术栈
- **Tailwind CSS**: 响应式设计和暗色主题
- **实时轮询**: 自动刷新任务状态
- **智能表单**: 支持任务克隆和配置复用

### 🎨 蓝图系统 (Blueprint System)
- **Python 函数即表单**: 编写 Python 函数自动生成前端表单
- **类型注解驱动**: 使用 Python 类型注解定义表单字段
- **动态参数解析**: 自动解析函数签名生成参数配置界面
- **代码即配置**: 将任务配置逻辑封装为可复用的 Python 代码

### ⚡ 弹性服务 (Elastic Services)
- **独立于调度器**: 服务作为独立自动机运行
- **按需弹性伸缩**: 根据流量自动 scale up/scale to zero
- **稳定 API 端点**: 对外提供稳定的 HTTP API 接口
- **端口动态分配**: 自动分配 MAGNUS_PORT 并转发到服务实例

### 🪄 Explorer 智能对话
- **多会话管理**: 支持创建、切换、删除多个独立对话会话
- **流式响应**: 实时显示 AI 回复，思考过程可视化
- **多模态理解**: 支持图片上传与 VLM 视觉理解
- **文件解析**: 支持 PDF、Word、TXT 等文档的文本提取
- **后台持久化**: 对话在后台持续运行，不受客户端断连影响
- **智能标题生成**: 使用小模型自动总结会话主题

## 📁 项目结构

```
Magnus-Platform/
├── configs/                                               # 配置文件目录
│   └── magnus_config.yaml                                 # 主配置文件
├── back_end/                                              # Python 后端服务
│   ├── server/                                            # FastAPI 服务器核心
│   │   ├── __init__.py
│   │   ├── _blueprint_manager.py                          # Blueprint 元开发核心
│   │   ├── _feishu_client.py                              # 飞书客户端
│   │   ├── _github_client.py                              # GitHub客户端
│   │   ├── _jwt_signer.py                                 # JWT签名器
│   │   ├── _magnus_config.py                              # 配置加载器
│   │   ├── _scheduler.py                                  # 核心调度器模块
│   │   ├── _service_manager.py                            # 弹性服务管理器
│   │   ├── _slurm_manager.py                              # SLURM集群管理器
│   │   ├── database.py                                    # 数据库连接
│   │   ├── main.py                                        # 应用入口
│   │   ├── models.py                                      # 数据库模型
│   │   ├── routers/                                       # API 路由定义
│   │   │   ├── __init__.py                                # API汇总
│   │   │   ├── auth.py                                    # 鉴权API
│   │   │   ├── blueprints.py                              # 蓝图API
│   │   │   ├── cluster.py                                 # 集群API
│   │   │   ├── github.py                                  # GitHub API
│   │   │   ├── jobs.py                                    # 任务API
│   │   │   ├── services.py                                # 服务API
│   │   │   └── explore.py                                 # Explorer对话API
│   │   └── schemas.py                                     # Pydantic 数据模型
│   ├── library/                                           # 核心库模块
│   │   ├── functional/                                    # 功能模块
│   │   │   ├── __init__.py
│   │   │   └── feishu_tools.py                            # 飞书工具
│   │   └── fundamental/                                   # 基础工具模块
│   │       ├── __init__.py
│   │       ├── externals.py
│   │       ├── github_tools.py                            # GitHub工具
│   │       ├── json_tools.py                              # JSON工具
│   │       ├── jwt_tools.py                               # JWT工具
│   │       ├── typing.py
│   │       └── yaml_tools.py                              # YAML工具
│   ├── python_scripts/                                    # 脚本目录
│   │   ├── blueprints/                                    # Blueprint 示例代码
│   │   │   ├── __init__.py
│   │   │   ├── magnus-debug.py                            # Magnus 调试任务蓝图
│   │   │   └── magnus-slurm.py                            # SLURM 任务蓝图
│   │   ├── magnus_debug.py                                # 调试脚本
│   │   ├── magnus_slurm.py                                # SLURM 任务脚本
│   │   ├── migrate_database.py                            # 数据库迁移脚本
│   │   └── tests/                                         # 测试工具
│   │       ├── test_github_tools.py
│   │       ├── test_magnus_basic.py
│   │       ├── test_rtx5090_nvlink.py                     # GPU互联测试
│   │       └── test_services/                             # 服务测试
│   │           ├── test_llm_inference.py                  # LLM推理测试
│   │           ├── test_mma_mcp.py                        # MMA-MCP测试
│   │           ├── test_vlm_inference.py                  # VLM推理测试
│   │           └── test_z_image.py                        # Z-Image 生图测试
│   ├── pyproject.toml                                     # Python 项目配置
│   ├── uv.lock                                            # UV依赖锁文件
│   └── .python-version                                    # Python版本指定
├── front_end/                                             # Next.js 前端应用
│   ├── src/app/                                           # Next.js App Router
│   │   ├── (main)/                                        # 主应用页面组
│   │   │   ├── blueprints/                                # Blueprint 管理页面
│   │   │   │   └── page.tsx
│   │   │   ├── cluster/                                   # 集群管理页面
│   │   │   │   └── page.tsx
│   │   │   ├── dashboard/                                 # 仪表板页面
│   │   │   │   └── page.tsx
│   │   │   ├── jobs/                                      # 任务管理页面
│   │   │   │   ├── [id]/                                  # 任务详情页面
│   │   │   │   │   └── page.tsx
│   │   │   │   └── page.tsx
│   │   │   ├── services/                                  # 弹性服务管理页面
│   │   │   │   └── page.tsx
│   │   │   ├── explore/                                   # Explorer 智能对话
│   │   │   │   ├── layout.tsx                             # Explorer 布局（会话列表）
│   │   │   │   ├── page.tsx                               # Explorer 首页
│   │   │   │   └── [sessionId]/                           # 会话详情页面
│   │   │   │       └── page.tsx
│   │   │   ├── tools/                                     # 工具页面
│   │   │   │   └── page.tsx
│   │   │   └── layout.tsx                                 # 主布局组件
│   │   ├── auth/                                          # 认证相关页面
│   │   │   └── callback/                                  # 飞书回调页面
│   │   │       └── page.tsx
│   │   ├── api/                                           # Next.js API Routes
│   │   │   └── logo/                                      # Logo API
│   │   │       └── route.ts
│   │   ├── globals.css                                    # 全局样式
│   │   ├── icon.png                                       # 应用图标
│   │   ├── layout.tsx                                     # 根布局组件
│   │   └── page.tsx                                       # 首页 (重定向到/dashboard)
│   ├── src/components/                                    # 可复用组件
│   │   ├── auth/                                          # 认证组件
│   │   │   └── login-required.tsx                         # 登录要求组件
│   │   ├── blueprints/                                    # Blueprint 相关组件
│   │   │   ├── blueprint-editor.tsx                       # Blueprint 编辑器
│   │   │   ├── blueprint-runner.tsx                       # Blueprint 运行器
│   │   │   └── blueprint-table.tsx                        # Blueprint 表格
│   │   ├── jobs/                                          # 任务相关组件
│   │   │   ├── job-drawer.tsx                             # 任务抽屉组件
│   │   │   ├── job-form.tsx                               # 任务表单组件
│   │   │   ├── job-priority-badge.tsx                     # 任务优先级徽章
│   │   │   ├── job-status-badge.tsx                       # 任务状态徽章
│   │   │   └── job-table.tsx                              # 任务表格
│   │   ├── services/                                      # 服务相关组件
│   │   │   ├── service-drawer.tsx                         # 服务抽屉组件
│   │   │   ├── service-form.tsx                           # 服务表单组件
│   │   │   └── service-table.tsx                          # 服务表格
│   │   ├── layout/                                        # 布局组件
│   │   │   ├── header.tsx                                 # 页面头部
│   │   │   ├── notifications-popover.tsx                  # 通知弹窗
│   │   │   └── sidebar.tsx                                # 侧边栏
│   │   └── ui/                                            # UI基础组件
│   │       ├── confirmation-dialog.tsx                    # 确认对话框
│   │       ├── copyable-text.tsx                          # 可复制文本
│   │       ├── dynamic-form/                              # 动态表单组件
│   │       │   ├── index.tsx                              # 动态表单主组件
│   │       │   └── types.ts                               # 动态表单类型定义
│   │       ├── drawer.tsx                                 # 抽屉组件
│   │       ├── number-stepper.tsx                         # 数字步进器
│   │       ├── pagination-controls.tsx                    # 分页控件
│   │       ├── render-markdown.tsx                        # Markdown渲染器
│   │       ├── searchable-select.tsx                      # 可搜索选择器
│   │       └── user-avatar.tsx                            # 用户头像
│   ├── src/context/                                       # React Context
│   │   └── auth-context.tsx                               # 认证上下文
│   ├── src/hooks/                                         # 自定义 Hooks
│   │   └── use-job-operations.tsx                         # 任务操作 Hook
│   ├── src/lib/                                           # 工具库
│   │   ├── api.ts                                         # API客户端
│   │   ├── blueprint-defaults.ts                          # Blueprint 默认模板
│   │   ├── config.ts                                      # 前端配置
│   │   └── utils.ts                                       # 工具函数
│   ├── src/types/                                         # TypeScript 类型定义
│   │   ├── auth.ts                                        # 认证相关类型
│   │   ├── blueprint.ts                                   # Blueprint 相关类型
│   │   ├── explore.ts                                     # Explorer 相关类型
│   │   ├── job.ts                                         # 任务相关类型
│   │   └── service.ts                                     # 服务相关类型
│   ├── public/                                            # 静态资源
│   │   └── images/                                        # 图片资源
│   │       └── slurm_avatar.png                           # SLURM头像
│   ├── package.json                                       # 前端依赖配置
│   ├── tsconfig.json                                      # TypeScript配置
│   ├── next.config.mjs                                    # Next.js配置
│   ├── tailwind.config.ts                                 # Tailwind CSS配置
│   ├── postcss.config.mjs                                 # PostCSS配置
│   ├── start-dev.mjs                                      # 开发启动脚本
│   └── .eslintrc.json                                     # ESLint配置
├── sdks/                                                  # 多语言 SDK
│   └── python/                                            # Python SDK (magnus-sdk)
│       ├── src/
│       │   └── magnus/
│       │       ├── cli/                                   # CLI 命令行工具包
│       │       │   ├── __init__.py
│       │       │   ├── commands.py                        # CLI 命令实现
│       │       │   └── main.py                            # CLI 入口
│       │       └── __init__.py                            # SDK 核心逻辑
│       └── pyproject.toml                                 # SDK 项目配置
└── scripts/                                               # 脚本
    └── deploy.py                                          # 部署脚本
```

## 🛠️ 技术栈

### 后端技术栈
- **框架**: FastAPI (Python 异步 Web 框架)
- **Python 版本**: ≥3.14
- **数据库**: SQLite + SQLAlchemy ORM
- **调度器**: 自定义智能调度算法
- **认证**: JWT + 飞书 OAuth 2.0
- **包管理器**: UV
- **核心依赖**:
  - FastAPI ≥0.124.0
  - HTTPX ≥0.28.1
  - PyTorch ≥2.9.1 + TorchVision ≥0.24.1
  - SQLAlchemy (数据库 ORM)
  - PyJWT (JWT 令牌处理)
  - ruamel.yaml ≥0.18.16 (YAML 解析)

### 前端技术栈
- **框架**: Next.js 14.2.33 (React 18 + TypeScript)
- **构建工具**: Next.js App Router
- **样式**: Tailwind CSS 3.4.1 + PostCSS
- **图标库**: Lucide React
- **状态管理**: React Context
- **代码质量**: ESLint + TypeScript

## 🎨 蓝图系统 (Blueprint System)

### 开发体验：Python 函数即前端表单

Magnus 的蓝图系统提供了一种革命性的开发体验：**编写一个 Python 函数，自动生成前端表单，嵌入后端逻辑**。

#### 核心特性：
1. **代码即配置**: 将复杂的任务配置逻辑封装为可复用的 Python 代码
2. **类型驱动**: 使用 Python 类型注解自动生成表单字段和验证规则
3. **动态解析**: 系统自动解析函数签名，生成对应的前端表单界面
4. **参数分组**: 支持通过注解元数据对参数进行逻辑分组和范围限定
5. **运行时类型转换**: 基于 Pydantic 动态模型，自动处理字符串到强类型的转换

#### 示例蓝图代码：
```python
from typing import Annotated, Optional, Literal

DataDir = Annotated[str, {
    "label": "data_dir",
    "description": "数据输出目录",
    "placeholder": "/home/<your_name>/outputs/exp1",
    "allow_empty": False,
}]

BaseConfig = Annotated[Literal["default", "DIII-D_example"], {
    "label": "Base Config",
    "description": "选择基础配置文件",
    "options": {
        "default": {
            "label": "Default",
            "description": "完整默认配置，适合自定义实验",
        },
        "DIII-D_example": {
            "label": "DIII-D Example",
            "description": "DIII-D 托卡马克典型参数",
        },
    },
}]

Te = Annotated[Optional[float], {
    "label": "Te (keV)",
    "description": "开启以覆写 config 中电子温度",
    "scope": "Plasma Override",
    "min": 0.1,
    "max": 50.0,
    "placeholder": "e.g. 1.5",
}]

def generate_job(
    data_dir: DataDir,
    base_config: BaseConfig = "default",
    Te: Te = None,
) -> JobSubmission:
    cli_args = [f"--data_dir {data_dir}"]
    if Te is not None:
        cli_args.append(f"--Te {Te}")

    return JobSubmission(
        task_name=f"Simulation-{base_config}",
        repo_name="my-project",
        branch="main",
        entry_command=f"python main.py {' '.join(cli_args)}",
        gpu_type="cpu",
        gpu_count=0,
        job_type=JobType.A2,
    )
```

#### 支持的参数类型

| 类型 | Python 注解 | 表单元素 | 元数据 |
|------|-------------|----------|--------|
| 文本 | `str` | 单行/多行输入框 | `placeholder`, `allow_empty`, `multi_line`, `min_lines`, `color`, `border_color` |
| 整数 | `int` | 数字步进器 | `min`, `max` |
| 浮点数 | `float` | 数字输入框 | `min`, `max`, `placeholder` |
| 布尔 | `bool` | 开关 | - |
| 枚举 | `Literal["a", "b"]` | 下拉选择器 | `options` (可含 label/description) |
| 可选 | `Optional[T]` | 带启用开关的字段 | 禁用时不传参 |
| 文件传输 | `FileSecret` | 文件凭证输入框 | `placeholder`, `description` |
| 列表 | `List[T]` | 动态添加/删除项 | - |
| 组合 | `Optional[List[T]]` | 可选的列表字段 | - |

#### 通用元数据属性
- `label`: 字段显示名称
- `description`: 字段说明文字
- `scope`: 参数分组（相同 scope 的参数会被归类显示）

#### 文件传输 (FileSecret)

`FileSecret` 类型用于将本地文件传输到远程执行环境，通过 Magnus 服务器中转。

```python
# 蓝图定义
InputData = Annotated[FileSecret, {
    "label": "Input Data",
    "description": "Upload your dataset",
    "placeholder": "file secret code",
}]

def generate_job(data: InputData) -> JobSubmission:
    ...
```

**Web 端**：用户输入文件凭证（前缀 `magnus-secret:` 已预填）

**SDK 端**：直接传文件路径，SDK 自动上传到服务器
```python
from magnus import submit_blueprint
submit_blueprint("my-bp", args={"data": "/local/path/to/file.csv"})
```

**蓝图内接收文件**：
```python
from magnus import download_file
download_file(data, "my_table.csv")  # data 是 FileSecret 参数
```

#### 工作流程：
1. **编写蓝图**: 在 Blueprint 编辑器中编写 Python 函数
2. **自动解析**: 系统解析函数签名生成表单 Schema
3. **用户填写**: 用户在前端表单中填写参数
4. **类型转换**: Pydantic 自动将输入转换为强类型
5. **动态生成**: 系统调用函数生成完整的 Job 配置
6. **提交执行**: 自动提交到调度系统执行

## ⚡ 弹性服务 (Elastic Services)

### 独立于调度器的自动机系统

Magnus 的服务系统是一个独立于任务调度器的弹性计算单元，专为需要长期运行、按需伸缩的应用场景设计。

#### 核心特性：
1. **独立运行**: 服务作为独立的自动机运行，不受调度器心跳周期影响
2. **弹性伸缩**: 根据流量自动 scale up/scale to zero
3. **稳定端点**: 对外提供稳定的 HTTP API 接口，内部自动转发
4. **端口管理**: 自动分配 MAGNUS_PORT 并建立端口转发

#### 服务生命周期：
```
1. 服务创建 → 定义服务配置（代码仓库、资源需求等）
2. 端口分配 → 系统自动分配唯一的 MAGNUS_PORT
3. 任务启动 → 创建对应的 SLURM 任务运行服务代码
4. 流量检测 → 监控服务访问活动，检测空闲状态
5. 弹性伸缩 → 空闲超时自动停止，有请求时自动重启
```

#### 使用场景：
- **长期运行的服务**: Jupyter Notebook、Gradio 应用、API 服务
- **交互式应用**: 需要用户交互的 Web 应用
- **按需计算**: 流量波动大的服务，节省资源成本
- **团队协作**: 共享的计算环境和服务端点

#### 技术实现：
- **服务管理器**: 独立的后台进程监控服务状态
- **端口转发**: 通过分配的 MAGNUS_PORT 建立稳定的访问通道
- **反饥饿机制**: 防止服务因短暂空闲被误终止
- **状态持久化**: 服务配置和状态持久化到数据库

## 🪄 Explorer 智能对话

### 平台内置 AI 对话助手

Explorer 是 Magnus 平台内置的 AI 对话助手，支持多模态输入和流式响应。

#### 核心特性：
1. **多会话管理**: 支持创建、切换、删除多个独立对话会话
2. **流式响应**: 实时显示 AI 回复，包括思考过程（Thinking）的可视化
3. **多模态理解**: 支持图片上传，通过 VLM 进行视觉理解
4. **文件解析**: 支持 PDF、Word (.docx)、TXT 文件的文本提取
5. **后台持久化**: 对话生成在后台线程运行，不受客户端断连影响
6. **智能标题**: 使用小型快速模型自动生成会话标题摘要

#### 技术架构：
```
用户消息 → 图片/文件预处理 → VLM 视觉理解 → 主 LLM 生成 → 流式返回
                ↓                    ↓              ↓
            文件存储            上下文注入      后台持久化
```

#### 配置示例：
```yaml
server:
  explorer:
    api_key: "your-api-key"
    base_url: "https://api.example.com/v1"
    model_name: "qwen-max"                    # 主对话模型
    visual_model_name: "qwen-vl-max"          # 视觉理解模型
    small_fast_model_name: "qwen-turbo"       # 标题生成模型
```

#### 交互功能：
- **消息编辑**: 可编辑已发送的消息并重新生成回复
- **图片预览**: 点击图片可放大查看
- **反馈按钮**: 点赞/点踩/重新生成/复制回复
- **会话标题编辑**: 支持手动修改会话标题

## 🚀 快速开始

### 环境要求
- **Python**: ≥3.14
- **Node.js**: 最新 LTS 版本
- **SLURM 集群**: 完整的 SLURM 环境 (sbatch, squeue, scancel, sinfo)
- **飞书应用**: 需要在飞书开放平台创建应用
- **GitHub SSH密钥**: 用于访问 GitHub 仓库

### 1. 配置设置

复制示例配置并编辑：

```bash
cp configs/magnus_config.yaml.example configs/magnus_config.yaml
```

编辑 `configs/magnus_config.yaml`:

```yaml
client:
  jobs:
    poll_interval: 2                    # SDK 轮询间隔(秒)

server:
  address: http://162.105.151.196        # 服务器地址（含协议）
  front_end_port: 3011                  # 前端端口
  back_end_port: 8017                   # 后端端口
  root: /home/magnus/magnus-data        # 数据存储根目录

  jwt_signer:
    secret_key: "your-secret-key"       # JWT 密钥
    algorithm: HS256
    expire_minutes: 10080               # 7 天有效期

  github_client:
    token: "ghp_..."                    # GitHub Personal Access Token

  feishu_client:
    app_id: "your-app-id"               # 飞书应用 ID
    app_secret: "your-app-secret"       # 飞书应用密钥

  scheduler:
    spy_gpu_interval: 5                 # GPU 状态监控间隔(秒)
    heartbeat_interval: 2               # 调度器心跳间隔(秒)
    snapshot_interval: 300              # 快照间隔(秒)

  resource_cache:
    container_cache_size: 80G           # 容器镜像缓存大小（LRU 淘汰）
    repo_cache_size: 20G                # 代码仓库缓存大小（LRU 淘汰）

  service_proxy:
    max_concurrency: 1024               # 服务代理最大并发
    pool_size: 1024                     # 连接池大小

cluster:
  name: "My Cluster"                    # 集群名称
  gpus:
    - value: "rtx5090"                  # GPU 类型标识
      label: "NVIDIA GeForce RTX 5090"
      meta: "32GB • Blackwell"          # 显示的元信息
      limit: 4                          # 单任务最大 GPU 数
  max_cpu_count: 128                    # 最大 CPU 核心数
  default_memory: "1600M"               # 默认内存限制
  default_runner: "magnus"              # 默认运行用户
  default_container_image: "docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
  default_system_entry_command: |       # 容器启动前的宿主机命令
    export APPTAINER_BIND="/home:/home"
    export UV_CACHE_DIR=/home/magnus/magnus-data/uv_cache
```

### 2. 后端启动

```bash
# 进入后端目录
cd back_end

# 安装依赖 (使用 UV)
uv sync

# 启动后端服务器
uv run -m server.main
```

后端启动后会自动：
- 创建数据库表结构
- 启动调度器后台任务
- 监听配置文件中指定的端口

### 3. 前端启动

```bash
# 进入前端目录
cd front_end

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端启动后访问配置文件中指定的地址。

## 📖 使用指南

### 用户认证
1. 访问前端地址
2. 点击登录按钮，使用飞书扫码登录
3. 系统会自动创建用户账户并返回 JWT 令牌

### 提交计算任务
1. 进入 "Jobs" 页面
2. 点击 "New Job" 按钮
3. 填写任务信息：
   - **任务名称**: 任务的描述性名称
   - **代码仓库**: GitHub 仓库地址 (namespace/repo)
   - **分支和提交**: 选择要运行的代码版本
   - **任务优先级**: A1/A2/B1/B2 四级优先级
   - **GPU 资源**: 选择 GPU 类型和数量
   - **启动命令**: 训练脚本命令
4. 点击 "Launch Job" 提交任务

### 任务优先级说明
- **A1**: 最高优先级，不可被抢占，紧急任务
- **A2**: 高优先级，不可被抢占，重要任务
- **B1**: 中优先级，可被 A 类任务抢占
- **B2**: 低优先级，可被 A 类任务抢占

### 任务状态
- **Preparing**: 准备容器镜像和代码仓库
- **Pending**: 在 Magnus 队列中等待调度
- **Running**: 在 SLURM 集群中执行
- **Paused**: 被高优先级任务抢占挂起
- **Success**: 任务执行成功
- **Failed**: 任务执行失败

## 🔧 调度算法详解

### 调度循环流程
```
1. 状态同步 (Sync Reality)
   ↓
2. 获取空闲 GPU 资源
   ↓
3. 获取待调度任务 (PENDING/PAUSED)
   ↓
4. 按优先级排序 (A1 > A2 > B1 > B2)
   ↓
5. 决策调度:
   - 情况A: 资源充足 → 直接启动
   - 情况B: A类任务资源不足 → 抢占B类任务
   - 情况C: B类任务资源不足 → 继续等待
   ↓
6. 提交到 SLURM
```

### 抢占策略
- **抢占条件**: 只有 A1/A2 任务可以抢占 B1/B2 任务
- **抢占目标**: 优先抢占最新启动的 B 类任务 (LIFO 策略)
- **抢占动作**: 终止 SLURM 任务 → 标记为 PAUSED 状态
- **资源释放**: 抢占后立即释放 GPU 资源给高优先级任务

## 📊 API 接口

### 认证相关
- `POST /api/auth/feishu/login` - 飞书登录
- 请求体: `{ "code": "飞书授权码" }`
- 响应: `{ "access_token": "...", "user": {...} }`

### GitHub 相关
- `GET /api/github/{namespace}/{repo}/branches` - 获取分支列表
- `GET /api/github/{namespace}/{repo}/commits` - 获取提交历史
- 查询参数: `?branch={branch_name}`

### 任务管理
- `POST /api/jobs/submit` - 提交新任务 (需要 JWT 令牌)
- `GET /api/jobs` - 获取任务列表 (支持分页、搜索、筛选)
- 查询参数: `?skip={number}&limit={number}&search={keyword}&creator_id={user_id}`

### 用户管理
- `GET /api/users` - 获取所有注册用户列表

## 🔐 安全与隔离特性

Magnus 在设计之初就将安全性与资源隔离作为核心考量，特别是在开放 Python 代码执行（Blueprints）和弹性服务（Elastic Services）的场景下：

1. **全链路身份鉴权**:
* **飞书 OAuth 2.0**: 企业级单点登录，确保只有组织内成员可访问。
* **JWT 令牌轮换**: 采用 7 天有效期的 Access Token，所有 API 端点均受 `login_required` 依赖保护。

2. **蓝图类型安全**:
* **静态类型强校验**: 基于 Python `Annotated` 和 `Pydantic` 进行严格的参数验证，在代码执行前拦截非法输入。
* **参数范围限制**: 通过元数据（Metadata）强制限制数值范围和选项集合，防止参数越界风险。

3. **网络与资源隔离**:
* **动态端口沙箱**: 弹性服务（Elastic Services）不直接暴露物理端口，而是通过系统自动分配的 `MAGNUS_PORT` 进行流量转发。
* **SLURM 环境隔离**: 所有计算任务均在 SLURM 严格管理的 cgroup 环境中运行，防止资源超卖和越权访问。

4. **数据安全**:
* **ORM 防护**: 使用 SQLAlchemy ORM 层自动过滤 SQL 注入攻击。
* **密钥管理**: 敏感配置与代码逻辑分离，支持通过环境变量注入系统密钥。

## 🐍 Python SDK

Magnus 提供完整的 Python SDK，支持编程化调用 Magnus API。

> 📖 **完整文档**: [Magnus SDK & CLI 完整指南](docs/Magnus_SDK_Guide.md) 包含所有 API 详解、异步支持、CLI 命令等。

### 安装

```bash
pip install magnus-sdk
# 或
uv add magnus-sdk
```

### 环境配置

```bash
# 推荐：交互式配置（保存到 ~/.magnus/config.json，即时生效）
magnus login

# 或手动设置环境变量（优先级高于配置文件）
export MAGNUS_TOKEN="your-jwt-token"
export MAGNUS_ADDRESS="http://your-server:8017"
```

### 快速开始

```python
import magnus

# 方式1: 提交蓝图任务 (Fire & Forget)
job_id = magnus.submit_blueprint("quadre-simulation", args={"Te": "2.0", "B": "1.5"})
print(f"Job submitted: {job_id}")

# 方式2: 提交并等待完成 (Submit & Wait)
result = magnus.run_blueprint("my-blueprint", args={"param": "value"})
print(f"Result: {result}")

# 调用弹性服务 (RPC)
response = magnus.call_service("llm-inference", payload={"prompt": "Hello!"})

# 任务管理
jobs = magnus.list_jobs(limit=10)
job_info = magnus.get_job("job-id")
magnus.terminate_job("job-id")
```

### 异步支持

```python
import magnus
import asyncio

async def main():
    job_id = await magnus.submit_blueprint_async("my-blueprint")
    result = await magnus.run_blueprint_async("my-blueprint", timeout=300)
    response = await magnus.call_service_async("my-service", payload={"x": 1})

asyncio.run(main())
```

### API 概览

| 函数 | 说明 |
|------|------|
| `submit_blueprint(id, args)` | 提交蓝图任务，立即返回 Job ID |
| `run_blueprint(id, args, timeout)` | 提交并轮询等待完成 |
| `call_service(id, payload)` | 调用托管服务 |
| `list_jobs(limit, search)` | 列出任务 |
| `get_job(job_id)` | 获取任务详情 |
| `get_job_logs(job_id, page)` | 获取任务日志 |
| `terminate_job(job_id)` | 终止任务 |
| `get_cluster_stats()` | 获取集群状态 |
| `list_blueprints(limit, search)` | 列出蓝图 |
| `list_services(limit, search)` | 列出服务 |

所有函数均有对应的 `_async` 版本。

## 💻 命令行工具 (CLI)

Magnus SDK 内置强大的 CLI 工具。详细用法参见 [SDK 指南](docs/Magnus_SDK_Guide.md#命令行工具-cli)。

### 蓝图操作

```bash
# 提交蓝图任务 (Fire & Forget)
magnus submit quadre-simulation --Te 2.0 --B 1.5

# 提交并等待完成 (Submit & Wait)
magnus run my-blueprint --param value

# 带超时限制
magnus run my-blueprint --timeout 300 -- --param value
```

### 服务调用

```bash
# 直接传参
magnus call llm-inference --prompt "Hello!" --max_tokens 100

# 从文件读取 payload
magnus call my-service @payload.json

# 从 stdin 读取
echo '{"x": 1}' | magnus call my-service -
```

### 任务管理

```bash
# 列出最近任务
magnus jobs
magnus jobs -l 20          # 显示 20 条
magnus jobs -n "quadre"    # 按名称搜索
magnus jobs -f yaml        # YAML 格式输出

# 查看任务详情 (支持负数索引: -1 = 最新, -2 = 第二新)
magnus status -1           # 最新任务
magnus status abc123       # 按 Job ID

# 查看任务日志
magnus logs -1             # 最新任务的日志
magnus logs -1 --page 0    # 第一页日志

# 终止任务
magnus kill -1             # 终止最新任务
magnus kill -1 -f          # 跳过确认
```

### 集群与资源

```bash
# 查看集群状态
magnus cluster
magnus cluster -f yaml     # YAML 格式

# 列出蓝图
magnus blueprints
magnus blueprints -s "sim" # 搜索蓝图

# 列出服务
magnus services
magnus services -a         # 仅活跃服务
```

### 配置查看

```bash
# 查看当前 SDK 配置
magnus config
```

### 文件传输

```bash
# 发送文件/文件夹（上传到服务器）
magnus send my_table.csv
magnus send ./my_folder

# 接收文件/文件夹（从服务器下载）
magnus receive a1b2c3d4e5f6

# 接收（可选 -o/--output 重命名）
magnus receive a1b2c3d4e5f6 --output my_data.csv

# 代管文件到后端（上传到服务器，返回文件凭证）
magnus custody results.csv --expire-minutes 120
```

**输出示例**：
```
  MAGNUS_ADDRESS  http://162.105.151.196:8017
  MAGNUS_TOKEN    eyJh****************bGci
```

## 📈 项目状态

### ✅ 已完成核心模块

1. **下一代调度系统**: 实现 A1/A2/B1/B2 四级优先级抢占式调度与 2s 级心跳决策。
2. **蓝图系统 (Blueprint System)**: 完成 "Python 函数即表单" 引擎，支持多种类型（`str`, `int`, `float`, `bool`, `Literal`, `Optional`, `List`）、参数分组、范围验证与 Pydantic 运行时类型转换。
3. **弹性服务 (Elastic Services)**: 实现基于流量的 Scale-to-Zero 自动伸缩与独立自动机管理。
4. **Explorer 智能对话**: 平台内置 AI 对话助手，支持多模态输入、流式响应、思考过程可视化、后台持久化与智能标题生成。
5. **现代化交互界面**: 基于 Next.js 14 + Tailwind 的响应式控制台，集成飞书用户画像。
6. **企业级基础设施**: 完成数据库双向同步、飞书认证集成及 SLURM 集群深度对接。
7. **Python SDK**: 完整的编程接口，支持同步/异步 API，提供任务管理、蓝图执行、服务调用等功能。
8. **命令行工具**: 基于 Typer 的 CLI，支持蓝图提交/执行、服务调用、任务管理，支持负数索引快速引用任务。

### 🚧 开发中功能

1. **Magnus Metrics 协议**:
* 定义统一的指标上报协议，任务可通过该协议向外传递自定义 metrics。
* 资源监控（GPU 使用率、显存占用等）作为内置的典范 metric 实现。
* 支持用户在任务代码中上报训练 loss、accuracy 等业务指标。

## 🤝 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

## 📜 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 🙏 致谢

- [Apptainer](https://github.com/apptainer/apptainer) - 高性能容器运行时，Magnus 任务执行的隔离环境基础
- [OpenCode](https://github.com/anomalyco/opencode) - 优秀的开源 AI Coding Agent，Explorer 模块的部分实现参考了该项目
- [croc](https://github.com/schollz/croc) - 简洁高效的跨平台文件传输工具，Magnus 文件传输功能的早期设计参考

---

**Magnus** - 为 PKU Plasma 和 Rise-AGI 提供强大的计算基础设施支持 🚀
