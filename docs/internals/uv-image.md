> **Language / 语言**: **English** · [简体中文](uv-image.zh-CN.md)

# Magnus Runtime Image: uv Configuration and Image Burn-in Experience

## Background

Magnus jobs run in SIF containers via apptainer. Execution chain:

```
SLURM -> wrapper.py -> apptainer exec --containall --no-mount tmp --overlay ... -> user script
```

Peculiarities of the container environment:
- **rootlesskit bridge network** remaps UID to 0, and apptainer `--containall` then mounts an empty tmpfs over `/root` (UID 0's home)
- Therefore everything under `/root/` is **inaccessible** at runtime
- The SIF filesystem is read-only squashfs; writes inside the container require an ephemeral overlay
- The workspace is mounted via bind mount, which is on a **different filesystem** from the SIF filesystem

## Key uv ENV Configuration

### UV_PYTHON_INSTALL_DIR=/opt/uv/python

Location where uv-managed Python interpreters are installed. Default is `~/.local/share/uv/python/`, i.e. `/root/.local/...`, which gets swallowed by rootlesskit + containall. Must be relocated to `/opt/uv/python`.

### UV_CACHE_DIR=/opt/uv/cache

uv's package cache directory. Default is `~/.cache/uv/`, which gets swallowed the same way. Relocate to `/opt/uv/cache`.

Cache warmup at build time writes wheels of commonly used packages into this directory, so at runtime `uv pip install` / `uv sync` can hit the cache directly, avoiding repeated downloads.

### UV_LINK_MODE=copy

When uv installs packages into a venv, by default it hardlinks from the cache to the venv to save space and time. But at Magnus job runtime:
- The uv cache is inside the SIF image (squashfs, read-only)
- The venv is on the bind-mounted workspace

**Different filesystems — hardlink is impossible.** uv first tries hardlink, fails, falls back to copy, producing extra error noise and performance overhead. `UV_LINK_MODE=copy` tells uv to copy directly, skipping the hardlink attempt.

**Recommendation: any image involving uv where the cache and the venv may not be on the same filesystem should set this.**

## Tool Installation Principles

**Core principle: no tool or data may be placed under `/root/`.**

| Tool | Default Location | Magnus Actual Location | Reason |
|------|------------------|------------------------|--------|
| uv binary | `/root/.local/bin/uv` (symlink) | `/usr/local/bin/uv` (copy) | rootlesskit swallows /root |
| uvx binary | `/root/.local/bin/uvx` (symlink) | `/usr/local/bin/uvx` (copy) | same as above |
| Python | `~/.local/share/uv/python/` | `/opt/uv/python/` | same as above |
| uv cache | `~/.cache/uv/` | `/opt/uv/cache/` | same as above |
| Node.js | `/usr/bin/node` (apt) | `/usr/bin/node` | installed via apt, inherently safe |

When installing uv, you must `cp` rather than `ln -sf`, because a symlink target under `/root/.local/` would be swallowed.

## Cache Warmup Strategy

Pre-populating the uv cache at image build time so that runtime `uv sync` can hit the cache directly, avoiding every job re-downloading large packages (torch ~873MB).

### Key Lesson: Caches of `uv pip install` and `uv sync` Are Not Interchangeable

uv has two independent installers internally:
- `uv pip install` — pip-compatible interface, writes to its own archive cache
- `uv sync` — workspace-aware native interface, writes to another archive cache

**The two cache structures are different and do not recognize each other.** If warmup uses `uv pip install` to preheat, the `uv sync` at job runtime will all report `Identified uncached distribution`, which is equivalent to having no cache.

In addition, `uv pip install "torch>=2.9.1"` will resolve to the latest version (e.g. 2.10.0), but the `uv.lock` may pin 2.9.1, and version mismatch also causes cache miss.

### Correct Approach: Use `uv sync --frozen` to Preheat

COPY the real `pyproject.toml` + `uv.lock` into the image, and preheat the cache using the same `uv sync` path as at job runtime:

```dockerfile
COPY back_end/pyproject.toml back_end/uv.lock /tmp/_warmup/
COPY back_end/python_scripts/pyproject.toml /tmp/_warmup/python_scripts/
RUN mkdir -p /tmp/_warmup/server /tmp/_warmup/python_scripts/scripts \
    && touch /tmp/_warmup/server/__init__.py /tmp/_warmup/python_scripts/scripts/__init__.py \
    && cd /tmp/_warmup \
    && uv sync --frozen --no-install-project --no-install-workspace \
    && rm -rf /tmp/_warmup
```

Key parameters:
- `--frozen`: use the COPY'd `uv.lock` directly, do not re-resolve, versions exactly aligned
- `--no-install-project`: do not install the magnus project itself (it is not a PyPI package)
- `--no-install-workspace`: do not install workspace members (python-scripts, etc.)

This way warmup and job go through the **same code path**, with consistent cache keys and 100% hit rate.

### Handling Internally Developed Packages

magnus-sdk and pywheels are fast-changing internal packages, but they do not need to be excluded from the lock file:
- Excluding would break `--frozen` (lock file inconsistent with pyproject.toml)
- They are small (a few hundred KB), and re-downloading when the version drifts is fast
- During warmup they are cached together with the lock file; if the lock version at job runtime has not changed, they will still hit

### Build-time Proxy

The lab network may require a proxy to download PyPI packages and CUDA libraries:

```bash
sudo docker build --network=host \
    --build-arg HTTP_PROXY=... \
    --build-arg HTTPS_PROXY=... \
    --build-arg ALL_PROXY=... \
    -t parkcai/magnus-runtime:latest \
    -f docker/magnus-runtime/Dockerfile \
    .   # project root, because we need to COPY files from back_end/
```

`--network=host` lets the build process use the host network directly, combined with `--build-arg` to inject proxy environment variables. These ARGs do not persist into the final image.

## Container Isolation and Ephemeral Storage

### apptainer Parameters

```
apptainer exec --nv --containall --no-mount tmp --overlay {overlay} ...
```

| Parameter | Purpose |
|-----------|---------|
| `--nv` | pass through NVIDIA GPU drivers |
| `--containall` | isolate env, home, PID, IPC; home becomes a 64MB tmpfs |
| `--no-mount tmp` | prevent apptainer from mounting a 64MB tmpfs on `/tmp`, so that /tmp writes fall onto the overlay |
| `--overlay` | provide a writable ephemeral overlay, size controlled by the `ephemeral_storage` field |

### Environment Variable Passing

`--containall` isolates host environment variables. Variables needed inside the container are injected via the `APPTAINERENV_` prefix:

```bash
export APPTAINERENV_MAGNUS_TOKEN=...
export APPTAINERENV_HOME=/magnus
```

### Write Isolation

All writes inside the container on non-bind-mount paths fall onto the ephemeral overlay, bounded by the `ephemeral_storage` size (default 10G). The overlay is deleted when the job ends. The only bind mount is job working table → `/magnus/workspace`.

## Recommended Directory Structure

```
/usr/local/bin/
    uv          # uv main program (cp, not symlink)
    uvx         # uvx (cp, not symlink)
    python3     # symlink -> /opt/uv/python/.../bin/python3
    python      # symlink -> /opt/uv/python/.../bin/python3

/opt/uv/
    python/     # UV_PYTHON_INSTALL_DIR - uv-managed Python interpreters
    cache/      # UV_CACHE_DIR - wheel cache, pre-populated at build time
```

## Dockerfile Template

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

## Pitfall Log

1. **uv: command not found** — the uv install script places a symlink under `/root/.local/bin/`, which rootlesskit + containall swallow along with `/root`. Fix: `cp` to `/usr/local/bin/`.
2. **No space left on device (/tmp)** — `--containall` by default mounts a 64MB tmpfs on `/tmp` (`sessiondir max size`), unrelated to job memory. Fix: `--no-mount tmp`, so that `/tmp` falls onto the ephemeral overlay.
3. **unexpected EOF during apptainer pull** — network jitter while downloading a large image (~4.5GB SIF). Fix: 3 retries with exponential backoff in `_resource_manager.py`; non-transient errors (unauthorized, not found) fail immediately.
4. **hardlink across filesystems fails** — the cache on SIF (squashfs) and the venv on the bind mount are on different filesystems. Fix: `UV_LINK_MODE=copy`.
5. **`uv pip install` cache does not work for `uv sync`** — the archive caches of the two installers do not recognize each other. After preheating via `uv pip install`, `uv sync` at job runtime reports everything uncached, effectively installing nothing. Fix: change warmup to `uv sync --frozen`, COPY the real lockfile into the image, ensuring the cache format is consistent with job runtime.
