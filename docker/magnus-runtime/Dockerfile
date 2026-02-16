# docker/magnus-runtime/Dockerfile
#
# IMPORTANT: rootlesskit (bridge networking) remaps UID to 0.
# apptainer --containall then mounts an empty tmpfs over /root.
# Therefore NOTHING under /root/ is accessible at runtime.
# All tools must live in /usr/local/bin/ or /opt/.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# System packages: slurm-client, munge, build essentials
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
        slurm-client \
        munge \
        curl \
        ca-certificates \
        git \
        build-essential \
        libssl-dev \
        zlib1g-dev \
        libbz2-dev \
        libreadline-dev \
        libsqlite3-dev \
        libffi-dev \
        liblzma-dev \
        libncurses-dev \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20.x via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# uv package manager — copy binary to /usr/local/bin/ (NOT symlink)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && cp /root/.local/bin/uvx /usr/local/bin/uvx \
    && rm -rf /root/.local/

# All uv-managed assets live under /opt/uv/ (outside /root/)
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV UV_CACHE_DIR=/opt/uv/cache

# Python 3.14 via uv
RUN uv python install 3.14 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python3 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python

# Pre-populate uv cache with large dependencies (torch ~900MB, CUDA libs ~3GB)
# Build-time proxy: docker build --network=host --build-arg HTTP_PROXY=... ...
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
RUN uv venv /tmp/_cache_warmup --python 3.14 \
    && VIRTUAL_ENV=/tmp/_cache_warmup uv pip install \
        "torch>=2.9.1" "torchvision>=0.24.1" \
    && rm -rf /tmp/_cache_warmup

CMD ["/bin/bash"]
