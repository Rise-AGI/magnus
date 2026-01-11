<!-- README.md -->
# Magnus - Rise-AGI 计算基础设施平台

![Magnus Logo](https://img.shields.io/badge/Magnus-Platform-blue)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-3178c6)
![Python](https://img.shields.io/badge/Python-3.14+-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-teal)
![PyTorch](https://img.shields.io/badge/PyTorch-2.9+-ee4c2c)
![SLURM](https://img.shields.io/badge/Scheduler-SLURM-bf202f)


**Magnus** 是 Rise-AGI 的科学计算与机器学习计算基础设施，集成了智能调度、资源监控、用户认证等企业级功能。

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
- **模拟 Immediate 模式**: 通过状态检查实现资源立即分配

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
│   │   │   └── services.py                                # 服务API
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
└── scripts/                                               # 脚本
    └── deploy.sh                                          # 部署脚本
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

#### 示例蓝图代码：
```python
from typing import Annotated

UserName = Annotated[str, {
    "label": "User Name",
    "placeholder": "enter your username on liustation2 here",
    "allow_empty": False,
}]

GpuCount = Annotated[int, {
    "label": "GPU Count",
    "min": 1, 
    "max": 3, 
}]

def generate_job(
    user_name: UserName,
    gpu_count: GpuCount = 1,
) -> JobSubmission:
    return JobSubmission(
        task_name = "Magnus Debug",
        description = f"调试任务 - 使用人：{user_name}",
        namespace = "PKU-Plasma",
        repo_name = "magnus",
        branch = "main",
        entry_command = "python magnus_debug.py",
        gpu_count = gpu_count,
        gpu_type = "rtx5090",
        job_type = JobType.A2,
        runner = user_name,
    )
```

#### 支持的参数类型：
- **文本输入**: `Annotated[str, {"label": "...", "placeholder": "..."}]`
- **数字输入**: `Annotated[int, {"min": 1, "max": 10}]`
- **布尔选择**: `bool`
- **下拉选择**: `Literal["option1", "option2"]`
- **参数分组**: `{"scope": "Advanced Options"}`

#### 工作流程：
1. **编写蓝图**: 在 Blueprint 编辑器中编写 Python 函数
2. **自动解析**: 系统解析函数签名生成表单 Schema
3. **用户填写**: 用户在前端表单中填写参数
4. **动态生成**: 系统调用函数生成完整的 Job 配置
5. **提交执行**: 自动提交到调度系统执行

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

## 🚀 快速开始

### 环境要求
- **Python**: ≥3.14
- **Node.js**: 最新 LTS 版本
- **SLURM 集群**: 完整的 SLURM 环境 (sbatch, squeue, scancel, sinfo)
- **飞书应用**: 需要在飞书开放平台创建应用
- **GitHub SSH密钥**: 用于访问 GitHub 仓库

### 1. 配置设置

编辑 `configs/magnus_config.yaml`:

```yaml
server:
  public_ip: 162.105.151.196          # 公网IP地址
  front_end_port: 3011                # 前端端口
  back_end_port: 8017                 # 后端端口
  root: /data/zycai/magnus_data       # 数据存储根目录
  
  jwt_signer:
    secret_key: "your-secret-key"      # JWT 密钥
    algorithm: HS256
    expire_minutes: 10080              # 7天有效期
  
  github_client:
    token: "ghp_..."                   # GitHub Personal Access Token
  
  feishu_client:
    app_id: "your-app-id"              # 飞书应用 ID
    app_secret: "your-app-secret"      # 飞书应用密钥
  
  scheduler:
    heartbeat_interval: 2              # 调度器心跳间隔(秒)
    slurm_latency: 1                   # SLURM 延迟容忍(秒)
    conda_shell_script_path: /path/to/miniconda3/etc/profile.d/conda.sh
    execution_conda_environment: magnus  # 执行环境
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

## 📈 项目状态

### ✅ 已完成核心模块

1. **下一代调度系统**: 实现 A1/A2/B1/B2 四级优先级抢占式调度与 2s 级心跳决策。
2. **蓝图系统 (Blueprint System)**: 完成 "Python 函数即表单" 引擎，支持代码热加载与动态 UI 生成。
3. **弹性服务 (Elastic Services)**: 实现基于流量的 Scale-to-Zero 自动伸缩与独立自动机管理。
4. **现代化交互界面**: 基于 Next.js 14 + Tailwind 的响应式控制台，集成飞书用户画像。
5. **企业级基础设施**: 完成数据库双向同步、飞书认证集成及 SLURM 集群深度对接。

### 🚧 开发中功能

1. **高级监控仪表板**:
* 集群 GPU 细粒度使用率统计与可视化。

2. **Magnus Tools**:
* 基于 Blueprint 和 Service 的二次封装层。
* 为大语言模型提供标准化的调用接口，实现自然语言控制计算资源。

## 🤝 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

**Magnus** - 为 Rise-AGI 提供强大的计算基础设施支持 🚀