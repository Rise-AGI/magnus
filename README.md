# Magnus - PKU-Plasma 计算基础设施平台

![Magnus Logo](https://img.shields.io/badge/Magnus-Platform-blue)
![Python](https://img.shields.io/badge/Python-3.14+-green)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-teal)

**Magnus** 是一个为 PKU-Plasma 设计的全栈机器学习训练平台，集成了智能调度、集群管理、用户认证等企业级功能。

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

## 📁 项目结构

```
Magnus-Platform/
├── configs/                    # 配置文件目录
│   └── magnus_config.yaml     # 主配置文件
├── back_end/                  # Python 后端服务
│   ├── server/               # FastAPI 服务器
│   │   ├── _scheduler.py     # 🆕 核心调度器模块
│   │   ├── models.py         # 数据库模型 (含枚举类型)
│   │   ├── routers.py        # API 路由定义
│   │   ├── schemas.py        # Pydantic 数据模型
│   │   └── main.py           # 应用入口 (含调度器后台任务)
│   ├── library/              # 核心库模块
│   │   ├── functional/       # 功能模块
│   │   │   └── _slurm_manager.py  # 🆕 SLURM集群管理器
│   │   └── fundamental/      # 基础工具模块
│   ├── scripts/              # 脚本目录
│   │   └── tests/           # 测试工具
│   │       └── test_rtx5090_nvlink/  # GPU互联测试
│   ├── pyproject.toml        # Python 项目配置
│   ├── run.py               # 后端启动入口
│   └── run.sh               # 启动脚本
└── front_end/               # Next.js 前端应用
    ├── src/app/             # Next.js App Router
    │   ├── (main)/          # 主应用页面组
    │   │   ├── jobs/       # 任务管理页面
    │   │   ├── dashboard/  # 仪表板页面
    │   │   └── cluster/    # 集群管理页面
    │   ├── auth/           # 认证相关页面
    │   └── api/            # Next.js API Routes
    ├── src/components/      # 可复用组件
    │   ├── jobs/           # 任务相关组件
    │   ├── layout/         # 布局组件
    │   ├── auth/           # 认证组件
    │   └── ui/             # UI基础组件
    ├── src/context/        # React Context
    ├── src/lib/            # 工具库
    └── src/types/          # TypeScript 类型定义
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

## 🚀 快速开始

### 环境要求
- **Python**: ≥3.14
- **Node.js**: 最新 LTS 版本
- **SLURM 集群**: 完整的 SLURM 环境 (sbatch, squeue, scancel, sinfo)
- **飞书应用**: 需要在飞书开放平台创建应用
- **GitHub Token**: 用于访问 GitHub 仓库

### 1. 配置设置

编辑 `configs/magnus_config.yaml`:

```yaml
server:
  root: /data/zycai/magnus_data        # 数据存储根目录
  port: 8017                           # 服务器端口，可自由设置
  database: 
    path: /data/zycai/magnus-database  # 数据库路径
  jwt_signer:
    secret_key: "your-secret-key"      # JWT 密钥
    algorithm: HS256
    expire_minutes: 10080              # 7天有效期
  github_client:
    token: "ghp_..."                   # GitHub Personal Access Token
  feishu_client:
    app_id: "your-app-id"              # 飞书应用 ID
    app_secret: "your-app-secret"      # 飞书应用密钥
    redirect_uri: "http://localhost:3000/auth/callback"  # 回调地址
  scheduler:
    heartbeat_interval: 2              # 调度器心跳间隔(秒)
    slurm_latency: 1                   # SLURM 延迟容忍(秒)
```

### 2. 后端启动

```bash
# 进入后端目录
cd back_end

# 安装依赖 (使用 UV)
uv sync

# 启动后端服务器
uv run run.py
# 或使用启动脚本
./run.sh
```

后端启动后会自动：
- 创建数据库表结构
- 启动调度器后台任务
- 监听 0.0.0.0:8017

### 3. 前端启动

```bash
# 进入前端目录
cd front_end

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端启动后访问：http://localhost:3000

## 📖 使用指南

### 用户认证
1. 访问 http://localhost:3000
2. 点击登录按钮，使用飞书扫码登录
3. 系统会自动创建用户账户并返回 JWT 令牌

### 提交训练任务
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

## 🧪 测试工具

### GPU 互联测试
项目包含 GPU 互联测试工具，用于检测 GPU 间的数据传输性能：

```bash
cd back_end/scripts/tests/test_rtx5090_nvlink
python test_rtx5090_nvlink.py
```

测试内容包括：
- P2P 访问权限检查
- GPU 间带宽测量
- 互联质量评估报告

## 🔐 安全特性

1. **JWT 令牌认证**: 所有受保护 API 都需要有效令牌
2. **飞书 OAuth 2.0**: 企业级身份验证
3. **SQL 注入防护**: SQLAlchemy ORM 自动防护
4. **CORS 配置**: 跨域资源共享控制
5. **敏感信息加密**: 配置文件中的密钥安全管理

## 📈 项目状态

### ✅ 已完成功能
1. **完整的集群调度系统** (智能调度器 + SLURM 集成)
2. **四级优先级系统** (A1/A2/B1/B2 + 抢占机制)
3. **实时状态同步** (SLURM ↔ 数据库双向同步)
4. **飞书认证系统** (OAuth 2.0 + JWT)
5. **现代化 Web 界面** (Next.js + Tailwind CSS)
6. **GPU 互联测试工具** (带宽测量和诊断)

### 🚧 进行中功能
1. **仪表板页面** - GPU 使用情况统计
2. **集群管理页面** - 任务队列监控
3. **日志系统** - 任务执行日志收集和展示

## 🤝 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

**Magnus** - 为 PKU-Plasma 提供强大的计算基础设施支持 🚀