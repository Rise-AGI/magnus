> **Language / 语言**: [English](uv-image.md) · **简体中文**

# Magnus Runtime Image: uv 配置与镜像烧录经验

## 背景

Magnus job 通过 apptainer 在 SIF 容器中运行。运行链路：

```
SLURM -> wrapper.py -> apptainer exec --containall --no-mount tmp --overlay ... -> user script
```

容器环境的特殊性：
- **rootlesskit bridge 网络**会将 UID 重映射为 0，apptainer `--containall` 随后在 `/root`（UID 0 的 home）上挂载空 tmpfs
- 因此 `/root/` 下的所有内容在运行时**不可访问**
- SIF 文件系统是只读 squashfs，容器内写入需要 ephemeral overlay
- workspace 通过 bind mount 挂载，与 SIF 文件系统**跨文件系统**

## uv 关键 ENV 配置

### UV_PYTHON_INSTALL_DIR=/opt/uv/python

uv 管理的 Python 解释器安装位置。默认在 `~/.local/share/uv/python/`，即 `/root/.local/...`，会被 rootlesskit + containall 吞掉。必须迁移到 `/opt/uv/python`。

### UV_CACHE_DIR=/opt/uv/cache

uv 的包缓存目录。默认在 `~/.cache/uv/`，同理会被吞掉。迁移到 `/opt/uv/cache`。

构建时的 cache warmup 会将常用包的 wheel 写入此目录，运行时 `uv pip install` / `uv sync` 可直接命中缓存，避免重复下载。

### UV_LINK_MODE=copy

uv 安装包到 venv 时，默认用 hardlink 从缓存链接到 venv，省空间省时间。但在 Magnus job 运行时：
- uv cache 在 SIF 镜像内（squashfs，只读）
- venv 在 bind mount 的 workspace 上

**跨文件系统，hardlink 不可能。** uv 会先尝试 hardlink、失败、fallback 到 copy，产生额外的报错噪音和性能损耗。`UV_LINK_MODE=copy` 告诉 uv 直接 copy，跳过 hardlink 尝试。

**建议：所有涉及 uv 且 cache 和 venv 可能不在同一文件系统的镜像都应设置此项。**

## 工具安装原则

**核心原则：一切工具和数据都不能放在 `/root/` 下。**

| 工具 | 默认位置 | Magnus 实际位置 | 原因 |
|------|----------|-----------------|------|
| uv binary | `/root/.local/bin/uv` (symlink) | `/usr/local/bin/uv` (copy) | rootlesskit 吞 /root |
| uvx binary | `/root/.local/bin/uvx` (symlink) | `/usr/local/bin/uvx` (copy) | 同上 |
| Python | `~/.local/share/uv/python/` | `/opt/uv/python/` | 同上 |
| uv cache | `~/.cache/uv/` | `/opt/uv/cache/` | 同上 |
| Node.js | `/usr/bin/node` (apt) | `/usr/bin/node` | apt 安装，天然安全 |

安装 uv 时必须 `cp` 而非 `ln -sf`，因为 symlink 目标在 `/root/.local/` 下，会被吞。

## Cache Warmup 策略

在构建镜像时预填充 uv cache，运行时 `uv sync` 可直接命中，避免每个 job 都重新下载大包（torch ~873MB）。

### 关键教训：`uv pip install` 与 `uv sync` 的缓存不互通

uv 内部有两套独立的 installer：
- `uv pip install` — 兼容 pip 的接口，写入自己的 archive cache
- `uv sync` — workspace-aware 的原生接口，写入另一套 archive cache

**两者的缓存结构不同，互不识别。** 如果 warmup 用 `uv pip install` 预热，job 运行时的 `uv sync` 会全部报 `Identified uncached distribution`，相当于没有缓存。

此外，`uv pip install "torch>=2.9.1"` 会解析到最新版（如 2.10.0），但 `uv.lock` 锁定的可能是 2.9.1，版本不匹配也导致缓存无法命中。

### 正确做法：用 `uv sync --frozen` 预热

COPY 真实的 `pyproject.toml` + `uv.lock` 进镜像，用和 job 运行时相同的 `uv sync` 路径预热缓存：

```dockerfile
COPY back_end/pyproject.toml back_end/uv.lock /tmp/_warmup/
COPY back_end/python_scripts/pyproject.toml /tmp/_warmup/python_scripts/
RUN mkdir -p /tmp/_warmup/server /tmp/_warmup/python_scripts/scripts \
    && touch /tmp/_warmup/server/__init__.py /tmp/_warmup/python_scripts/scripts/__init__.py \
    && cd /tmp/_warmup \
    && uv sync --frozen --no-install-project --no-install-workspace \
    && rm -rf /tmp/_warmup
```

关键参数：
- `--frozen`：直接使用 COPY 进去的 `uv.lock`，不重新 resolve，版本精确对齐
- `--no-install-project`：不安装 magnus 项目本身（它不是 PyPI 包）
- `--no-install-workspace`：不安装 workspace members（python-scripts 等）

这样 warmup 和 job 走**同一条代码路径**，缓存 key 一致，100% 命中。

### 自研包的处理

magnus-sdk 和 pywheels 是快变的自研包，但不需要从 lock 文件中剔除：
- 剔除会破坏 `--frozen`（lock 文件与 pyproject.toml 不一致）
- 它们很小（几百 KB），即使版本漂移重新下载也很快
- warmup 时会随 lock 文件一起被缓存，如果 job 运行时 lock 版本没变，照样命中

### 构建时代理

实验室网络可能需要代理才能下载 PyPI 包和 CUDA 库：

```bash
sudo docker build --network=host \
    --build-arg HTTP_PROXY=... \
    --build-arg HTTPS_PROXY=... \
    --build-arg ALL_PROXY=... \
    -t parkcai/magnus-runtime:latest \
    -f docker/magnus-runtime/Dockerfile \
    .   # 项目根目录，因为需要 COPY back_end/ 的文件
```

`--network=host` 让构建过程直接使用宿主机网络，配合 `--build-arg` 注入代理环境变量。这些 ARG 不会持久化到最终镜像。

## 容器隔离与 ephemeral storage

### apptainer 参数

```
apptainer exec --nv --containall --no-mount tmp --overlay {overlay} ...
```

| 参数 | 作用 |
|------|------|
| `--nv` | 透传 NVIDIA GPU 驱动 |
| `--containall` | 隔离 env、home、PID、IPC；home 变为 64MB tmpfs |
| `--no-mount tmp` | 禁止 apptainer 在 `/tmp` 上挂 64MB tmpfs，让 /tmp 写入落到 overlay |
| `--overlay` | 提供可写的 ephemeral overlay，大小由 `ephemeral_storage` 字段控制 |

### 环境变量传递

`--containall` 会隔离宿主机环境变量。需要传入容器的变量通过 `APPTAINERENV_` 前缀注入：

```bash
export APPTAINERENV_MAGNUS_TOKEN=...
export APPTAINERENV_HOME=/magnus
```

### 写入隔离

容器内所有非 bind-mount 路径的写入都落到 ephemeral overlay 上，受 `ephemeral_storage` 大小约束（默认 10G）。overlay 在 job 结束后删除。唯一的 bind mount 是 job working table → `/magnus/workspace`。

## 推荐目录结构

```
/usr/local/bin/
    uv          # uv 主程序 (cp, 非 symlink)
    uvx         # uvx (cp, 非 symlink)
    python3     # symlink -> /opt/uv/python/.../bin/python3
    python      # symlink -> /opt/uv/python/.../bin/python3

/opt/uv/
    python/     # UV_PYTHON_INSTALL_DIR - uv 管理的 Python 解释器
    cache/      # UV_CACHE_DIR - wheel 缓存，构建时预填充
```

## Dockerfile 模板

```dockerfile
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

# ... system packages ...

# uv — copy binary, NOT symlink
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && cp /root/.local/bin/uvx /usr/local/bin/uvx \
    && rm -rf /root/.local/

# uv assets under /opt/uv/ (outside /root/)
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV UV_CACHE_DIR=/opt/uv/cache
ENV UV_LINK_MODE=copy

# Python via uv
RUN uv python install 3.14 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python3 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python

# Cache warmup: use real lockfile, same code path as job-time `uv sync`
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
COPY back_end/pyproject.toml back_end/uv.lock /tmp/_warmup/
COPY back_end/python_scripts/pyproject.toml /tmp/_warmup/python_scripts/
RUN mkdir -p /tmp/_warmup/server /tmp/_warmup/python_scripts/scripts \
    && touch /tmp/_warmup/server/__init__.py /tmp/_warmup/python_scripts/scripts/__init__.py \
    && cd /tmp/_warmup \
    && uv sync --frozen --no-install-project --no-install-workspace \
    && rm -rf /tmp/_warmup

CMD ["/bin/bash"]
```

## 踩坑记录

1. **uv: command not found** — uv 安装脚本在 `/root/.local/bin/` 下放 symlink，rootlesskit + containall 吞掉 `/root`。解法：`cp` 到 `/usr/local/bin/`。
2. **No space left on device (/tmp)** — `--containall` 默认在 `/tmp` 挂 64MB tmpfs（`sessiondir max size`），与 job memory 无关。解法：`--no-mount tmp`，让 `/tmp` 落到 ephemeral overlay。
3. **unexpected EOF during apptainer pull** — 大镜像（~4.5GB SIF）下载时网络抖动。解法：`_resource_manager.py` 中 3 次重试 + 指数退避，非瞬态错误（unauthorized, not found）直接失败。
4. **hardlink 跨文件系统失败** — SIF (squashfs) 上的 cache 和 bind mount 上的 venv 跨文件系统。解法：`UV_LINK_MODE=copy`。
5. **`uv pip install` 缓存对 `uv sync` 无效** — 两套 installer 的 archive cache 互不识别。warmup 用 `uv pip install` 预热后，job 运行时 `uv sync` 全部报 uncached，等于白装。解法：warmup 改用 `uv sync --frozen`，COPY 真实 lockfile 进镜像，确保缓存格式和 job 运行时一致。
