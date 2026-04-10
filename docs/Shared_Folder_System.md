## 共享文件夹系统 (Shared Folder System)

### 概述

共享文件夹系统允许用户创建跨任务、跨用户共享的持久化存储空间。与临时文件传输（FileSecret）不同，共享文件夹提供长期存储能力，适合存放大型数据集、模型权重等需要多次访问的资源。

### 核心特性

1. **Token 认证**：每个共享文件夹由唯一 token 标识，持有 token 即可挂载
2. **生命周期管理**：支持设置过期时间，超时自动归档
3. **容量控制**：预设预期大小，超量自动归档
4. **归档恢复**：归档后 14 天内可恢复

### 配置

在 `magnus_config.yaml` 中添加：

```yaml
server:
  sharedfile:
    invalidation_retention_period: 14  # 归档保留天数
    root_path: /data/sharedfile         # 活跃存储路径
    archived_root_path: /data/archived_sharedfile  # 归档存储路径
```

---

### API 接口

#### 1. 创建共享文件夹

```http
POST /api/shared-files
Content-Type: application/json

{
  "expire_days": 30,        // 过期天数 (7-90)
  "expected_size_gb": 100   // 预期大小 GB (1-800)
}
```

**响应：**
```json
{
  "token": "abc123-def456...",
  "expire_at": "2025-05-10T00:00:00Z",
  "expected_size_gb": 100
}
```

#### 2. 获取共享文件夹信息

```http
GET /api/shared-files/{token}
```

**响应：**
```json
{
  "token": "abc123-def456...",
  "status": "active",  // "active" 或 "archived"
  "created_at": "2025-04-10T00:00:00Z",
  "expire_at": "2025-05-10T00:00:00Z",
  "expected_size_gb": 100,
  "actual_size_bytes": 53687091200,
  "is_creator": true,
  "is_admin": false
}
```

#### 3. 浏览文件列表

```http
GET /api/shared-files/{token}/files?path=subdir
```

**响应：**
```json
{
  "files": [
    {"name": "data.csv", "path": "data.csv", "type": "file", "size": 1024},
    {"name": "models", "path": "models", "type": "directory"}
  ]
}
```

#### 4. 下载文件

```http
GET /api/shared-files/{token}/download?path=data.csv
```

返回文件流。

#### 5. 更新属性（仅创建者/管理员）

```http
PATCH /api/shared-files/{token}
Content-Type: application/json

{
  "expected_size_gb": 200,  // 可选：新的预期大小
  "extend_days": 30         // 可选：延长天数
}
```

#### 6. 恢复归档（仅创建者/管理员）

```http
POST /api/shared-files/{token}/restore
Content-Type: application/json

{
  "new_expire_days": 30  // 恢复后的过期天数
}
```

---

### CLI 命令

#### 创建共享文件夹

```bash
magnus shared create --expire-days 30 --expected-size-gb 100
```

输出：
```
Shared folder token: abc123-def456...
Expire at: 2025-05-10T00:00:00Z
```

#### 查看共享文件夹信息

```bash
magnus shared info <token>
```

输出：
```
Token:     abc123-def456...
Status:    active
Created:   2025-04-10T00:00:00Z
Expires:   2025-05-10T00:00:00Z
Expected:  100 GB
Actual:    50.00 GB
```

#### 浏览文件列表

```bash
magnus shared list <token>
magnus shared list <token> --path subdir
```

输出表格显示文件名、类型和大小。

#### 下载文件

```bash
magnus shared download <token> path/to/file.txt
magnus shared download <token> path/to/file.txt --dest ./local/path
```

#### 更新属性（创建者/管理员）

```bash
# 延长过期时间
magnus shared update <token> --extend-days 30

# 更新预期大小
magnus shared update <token> --expected-size-gb 200

# 同时更新
magnus shared update <token> --extend-days 30 --expected-size-gb 200
```

#### 恢复归档（创建者/管理员）

```bash
magnus shared restore <token> --expire-days 30
```

#### 提交任务时挂载共享文件夹

```bash
magnus submit \
  --task-name "My Job" \
  --entry-command "python train.py" \
  --shared-file "models=abc123-def456..." \
  --shared-file "data=xyz789-..."
```

---

### SDK 使用

#### Python SDK

```python
from magnus import (
    create_shared_folder,
    get_shared_folder_info,
    list_shared_files,
    download_shared_file,
    update_shared_folder,
    restore_shared_folder,
    submit_job,
)

# 创建共享文件夹
result = create_shared_folder(expire_days=30, expected_size_gb=100)
token = result["token"]
print(f"Token: {token}")

# 查看信息
info = get_shared_folder_info(token)
print(f"Status: {info['status']}")
print(f"Expires: {info['expire_at']}")

# 浏览文件
files = list_shared_files(token)
for f in files:
    print(f"  {f['name']} ({f['type']})")

# 浏览子目录
subfiles = list_shared_files(token, path="subdir")

# 下载文件
from pathlib import Path
download_shared_file(token, "data/model.bin", dest=Path("./model.bin"))

# 更新属性（创建者/管理员）
update_shared_folder(token, expected_size_gb=200, extend_days=30)

# 恢复归档（创建者/管理员）
restore_shared_folder(token, new_expire_days=30)

# 提交任务并挂载
submit_job(
    task_name="Training Job",
    repo_name="my-repo",
    branch="main",
    commit_sha="HEAD",
    entry_command="python train.py",
    shared_files={
        "models": token,       # 挂载为 $MAGNUS_HOME/models
        "dataset": "another-token",
    }
)
```

#### 异步 API

所有函数都有对应的 `*_async` 版本：

```python
import asyncio
from magnus import (
    get_shared_folder_info_async,
    list_shared_files_async,
    download_shared_file_async,
)

async def main():
    info = await get_shared_folder_info_async(token)
    files = await list_shared_files_async(token)
    await download_shared_file_async(token, "file.txt", Path("./file.txt"))

asyncio.run(main())
```

---

### 任务中使用共享文件夹

挂载后，共享文件夹会在容器内以下路径可用：

```
$MAGNUS_HOME/<mount_name>/
```

例如 `--shared-file "models=abc123"` 会在 `$MAGNUS_HOME/models/` 提供访问。

---

## 空仓库提交功能

### 概述

现在支持提交任务时**不指定 Git 仓库**，直接进入容器执行命令。适用于：
- 快速测试容器环境
- 使用预置镜像执行独立脚本
- 与共享文件夹配合使用

### 使用方法

**前端：**
- 留空 "Repo Name" 字段即可跳过仓库克隆

**CLI：**
```bash
magnus submit \
  --task-name "Quick Test" \
  --entry-command "python -c 'print(\"Hello\")'" \
  --container-image "docker://python:3.11"
```

**SDK：**
```python
submit_job(
    task_name="Quick Test",
    entry_command="python -c 'print(\"Hello\")'",
    container_image="docker://python:3.11",
    # repo_name 和 namespace 为 None
)
```

### 行为差异

| 项目 | 有仓库 | 无仓库 |
|------|--------|--------|
| 工作目录 | `$MAGNUS_HOME/workspace/repository/` | `$MAGNUS_HOME/workspace/` |
| 分支/Commit | 必填 | 不适用 |
| 扫描步骤 | 必须 | 跳过 |

---

## 前端功能更新

### 文件页面增强

在 "文件" (Tools) 页面新增：

1. **共享文件夹管理区域**
   - 输入 token 查看文件夹状态
   - 浏览文件结构（支持目录导航）
   - 下载单个文件
   - 更新预期大小或延长过期时间（创建者/管理员）
   - 恢复已归档的文件夹（创建者/管理员）

2. **状态显示**
   - 活跃/归档状态标签
   - 创建时间、过期时间
   - 实际大小 vs 预期大小

---

## 后端改动总结

### 新增文件

| 文件 | 功能 |
|------|------|
| `back_end/server/_shared_file_manager.py` | 共享文件夹核心逻辑 |
| `back_end/server/routers/shared_files.py` | 共享文件夹 API 路由 |
| `back_end/create_user.py` | 创建用户脚本 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `back_end/server/schemas.py` | `repo_name` 和 `namespace` 改为可选；添加 `shared_files` 字段 |
| `back_end/server/models.py` | Job 表添加 `shared_files` 列 |
| `back_end/server/routers/jobs.py` | 任务提交时验证 shared_files |
| `back_end/server/_scheduler.py` | 处理无仓库模式；挂载共享文件夹 |
| `back_end/server/_magnus_config.py` | 添加 sharedfile 配置验证 |
| `back_end/server/main.py` | 添加迁移和清理任务启动 |
| `configs/magnus_config.yaml.example` | 添加 sharedfile 配置示例 |

### 前端改动

| 文件 | 改动 |
|------|------|
| `front_end/src/app/(main)/tools/page.tsx` | 共享文件夹创建 + 管理界面 |
| `front_end/src/components/jobs/job-form.tsx` | 支持空仓库；shared_files 输入 |
| `front_end/src/context/language-context.tsx` | 新增 i18n 字符串 |
| `front_end/src/types/job.ts` | 添加 `shared_files` 字段 |
| `front_end/src/hooks/use-job-operations.tsx` | clone 时传递 shared_files |

### SDK 改动

| 文件 | 改动 |
|------|------|
| `sdks/python/src/magnus/client.py` | 添加共享文件夹管理方法 |
| `sdks/python/src/magnus/__init__.py` | 导出新增函数 |
| `sdks/python/src/magnus/cli/commands.py` | 添加 `magnus shared` 子命令 |

#### 新增 SDK 函数

| 函数 | 说明 |
|------|------|
| `create_shared_folder(expire_days, expected_size_gb)` | 创建共享文件夹 |
| `get_shared_folder_info(token)` | 获取文件夹信息 |
| `list_shared_files(token, path)` | 列出文件 |
| `download_shared_file(token, file_path, dest)` | 下载文件 |
| `update_shared_folder(token, expected_size_gb, extend_days)` | 更新属性 |
| `restore_shared_folder(token, new_expire_days)` | 恢复归档 |

#### 新增 CLI 命令

| 命令 | 说明 |
|------|------|
| `magnus shared create` | 创建共享文件夹 |
| `magnus shared info <token>` | 查看信息 |
| `magnus shared list <token>` | 列出文件 |
| `magnus shared download <token> <path>` | 下载文件 |
| `magnus shared update <token>` | 更新属性 |
| `magnus shared restore <token>` | 恢复归档 |

---

## 数据库迁移

启动时自动执行以下迁移：

```sql
-- Job 表添加 shared_files 列
ALTER TABLE jobs ADD COLUMN shared_files TEXT;
```

---

## 部署注意事项

1. **存储路径**：确保 `/data/sharedfile` 和 `/data/archived_sharedfile` 有足够空间，或配置到专用存储卷

2. **权限**：运行 Magnus 的用户需要对这些目录有写权限

3. **清理任务**：后台每 60 秒检查过期/超量的共享文件夹并归档

4. **归档保留**：默认保留 14 天，可通过配置调整