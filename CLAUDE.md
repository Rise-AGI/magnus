# CLAUDE.md - Magnus 项目 AI 协作指南

## 项目概述

Magnus 是 Rise-AGI 的科学计算与机器学习计算基础设施平台，集成智能调度、资源监控、用户认证等企业级功能。

**技术栈**：
- 后端：FastAPI (Python ≥3.14) + SQLAlchemy + PyJWT
- 前端：Next.js 14 + React 18 + TypeScript 5+ + Tailwind CSS
- 调度：SLURM 集群 + 自研四级优先级调度器
- 认证：飞书 OAuth 2.0 + JWT

## 开发原则

### 1. 敏捷优先
用户完全信任你，直接动手，快速迭代。不要过度思考、不要反复确认。

### 2. Fast Fail
不要用 try-except 包裹不该包裹的代码。让错误尽早暴露。

### 3. 组件复用
前端已有的 UI 组件优先复用。

## Python 编码规范

### 代码风格

1. **少用注释**：不要在每个步骤前加注释。只在用户不了解的 API、关键步骤或需要备忘的地方加注释
2. **不加 docstring**：除非用户主动要求
3. **变量命名**：见名知意，用完整英语单词而非缩写；库的使用遵循社区通用缩略语（如 `np`, `pd`）
4. **脚本入口**：脚本任务必须用 `if __name__ == "__main__": main()`；库代码不加
5. **类型标注来源**：使用旧版类型，即 `from typing import Any, Dict, Tuple, List, Optional`
6. **类型标注范围**：
   - **必须标注**：函数签名和返回值、空容器、类的一切内部变量
   - **不必标注**：栈上可推断类型的变量
7. **类型安全**：必须通过 pylance 审查，避免 unbound 或 possible None，可用 `assert x is not None`

### 代码格式

8. **函数签名格式**：
   - 入参必须一行一个
   - 返回值箭头：左边无空格直接接 `)`，右边有空格

```python
# 正确
def create_job(
    task_name: str,
    gpu_count: int,
    priority: JobType,
) -> JobSubmission:
    ...

# 错误
def create_job(task_name: str, gpu_count: int) ->JobSubmission:
    ...
```

9. **空行规则**：
   - **大意群**（函数/类的各个动作）之间：**两个空行**
   - **小意群**（动作内部的 for/try-except 等块）之间：**一个空行**
   - 以用户最新代码为准，灵活判断

10. **等号空格**：
    - 一般情况：两旁各空一格 `x = x + 1`
    - 例外：一行内传参时不空格 `func(x=10, y=20)`，与命令行 `--x=10` 对齐

11. **逗号规则**：
    - 逗号后必须有空格：`[a, b, c]`
    - 多行时最后一个元素也加逗号：

```python
# 正确
items = [
    "first",
    "second",
    "third",
]

# 错误
items = [
    "first",
    "second",
    "third"
]
```

## 代码结构

### Python 哲学

```
back_end/
├── library/              # 项目无关、跨项目可复用
│   ├── fundamental/      # 基础工具（无脑调用即可）
│   │   ├── jwt_tools.py
│   │   ├── json_tools.py
│   │   ├── yaml_tools.py
│   │   ├── github_tools.py
│   │   └── typing.py
│   └── functional/       # 功能模块（需要理解后使用）
│       └── feishu_tools.py
└── server/               # Magnus 项目特定内容
    ├── main.py           # FastAPI 入口
    ├── models.py         # SQLAlchemy 模型
    ├── schemas.py        # Pydantic 模型
    ├── database.py       # 数据库连接
    ├── _scheduler.py     # 调度器核心
    ├── _blueprint_manager.py
    ├── _service_manager.py
    ├── _slurm_manager.py
    ├── _feishu_client.py
    ├── _github_client.py
    ├── _jwt_signer.py
    ├── _magnus_config.py
    └── routers/          # API 路由
        ├── jobs.py
        ├── blueprints.py
        ├── services.py
        ├── cluster.py
        ├── auth.py
        ├── sdk.py
        └── github.py
```

**关键约束**：`library/` 目录中**绝对不能出现 "Magnus" 字样**。如果出现，说明代码放错了位置。

### 前端结构

```
front_end/src/
├── app/                  # Next.js App Router 页面
│   ├── (main)/           # 主应用页面组
│   │   ├── jobs/
│   │   ├── blueprints/
│   │   ├── services/
│   │   ├── cluster/
│   │   └── dashboard/
│   └── auth/callback/    # 飞书回调
├── components/
│   ├── ui/               # 基础 UI 组件（必须复用）
│   ├── jobs/             # 任务相关组件
│   ├── blueprints/       # 蓝图相关组件
│   ├── services/         # 服务相关组件
│   └── layout/           # 布局组件
├── lib/                  # 工具库
│   ├── api.ts            # API 客户端
│   └── utils.ts
├── types/                # TypeScript 类型定义
├── hooks/                # 自定义 Hooks
└── context/              # React Context
```

## 可复用前端组件

开发前端功能时，**必须优先使用**以下已有组件：

| 组件 | 路径 | 用途 |
|------|------|------|
| `CopyableText` | `components/ui/copyable-text.tsx` | 可复制文本 |
| `SearchableSelect` | `components/ui/searchable-select.tsx` | 可搜索下拉选择 |
| `NumberStepper` | `components/ui/number-stepper.tsx` | 数字步进器 |
| `DynamicForm` | `components/ui/dynamic-form/` | 动态表单（Blueprint 核心） |
| `Drawer` | `components/ui/drawer.tsx` | 抽屉面板 |
| `ConfirmationDialog` | `components/ui/confirmation-dialog.tsx` | 确认对话框 |
| `PaginationControls` | `components/ui/pagination-controls.tsx` | 分页控件 |
| `UserAvatar` | `components/ui/user-avatar.tsx` | 用户头像 |
| `RenderMarkdown` | `components/ui/render-markdown.tsx` | Markdown 渲染 |

## 国际化 (i18n)

### 架构

- **语言上下文**：`context/language-context.tsx` - 包含所有翻译定义和 `useLanguage` hook
- **语言切换**：`components/layout/language-toggle.tsx` - Header 右侧的下拉选择器
- **默认语言**：简体中文，用户选择后存储在 `localStorage`

### 使用方式

```tsx
import { useLanguage } from "@/context/language-context";

function MyComponent() {
  const { t } = useLanguage();

  return <h1>{t("jobs.title")}</h1>;
}

// 带参数插值
t("jobForm.memoryDefault", { value: "64G" })  // "默认：64G"
```

### 翻译键命名规范

```
模块.子模块.具体项
```

示例：
- `jobs.table.task` - Jobs 页面表格的 Task 列
- `jobForm.taskName` - Job 表单的任务名称字段
- `common.cancel` - 通用取消按钮

### 不翻译的术语

以下专有名词保持英文，不做翻译：
- **导航项**：Dashboard, Cluster, Jobs, Blueprints, Services, Explorer
- **品牌**：Magnus Platform
- **技术术语**：GPU, CPU, HEAD, commit sha 等

### 添加新翻译

在 `language-context.tsx` 的 `translations` 对象中添加：

```tsx
const translations = {
  // ...existing translations
  "myModule.newKey": { zh: "中文文本", en: "English text" },
} as const;
```

**注意**：使用 `as const` 确保 TypeScript 严格检查翻译键。

## 核心业务概念

### 四级优先级调度
- **A1**：最高优先级，不可被抢占
- **A2**：高优先级，不可被抢占
- **B1**：中优先级，可被 A 类抢占
- **B2**：低优先级，可被 A 类抢占

### 蓝图系统 (Blueprint)
Python 函数即前端表单。使用 `Annotated` 类型注解自动生成表单字段。

### 弹性服务 (Elastic Services)
独立于调度器的自动机，支持 scale-to-zero 弹性伸缩。

## 常用命令

```bash
# 后端
cd back_end
uv sync                    # 安装依赖
uv run -m server.main      # 启动后端

# 前端
cd front_end
npm install                # 安装依赖
npm run dev                # 启动开发服务器

# 类型检查
cd front_end && npx tsc --noEmit
```

## 配置文件

主配置文件：`configs/magnus_config.yaml`，前后端共享。具体字段参考 README.md。


## 协作提醒

1. **增量修改**：使用 Edit 工具增量修改文件，不要用 Write 全量重写
2. **不要 git push**：用户会自己 review 并 push
3. **library 纯净**：library 里不能有 Magnus 相关逻辑
4. **不用管 sdk 版本号**：sdk 版本号由用户自己掌管，不要代为自增
