# docker/magnus-runtime/Dockerfile
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

# Install uv, then use uv to install Python 3.14
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv python install 3.14 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python3 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python

# Pre-populate uv cache with large dependencies (torch ~900MB, CUDA libs ~3GB)
# Build-time proxy: docker build --network=host --build-arg HTTP_PROXY=... ...
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY
ENV UV_CACHE_DIR=/opt/uv/cache
RUN uv venv /tmp/_cache_warmup --python 3.14 \
    && VIRTUAL_ENV=/tmp/_cache_warmup uv pip install \
        "torch>=2.9.1" "torchvision>=0.24.1" \
    && rm -rf /tmp/_cache_warmup

CMD ["/bin/bash"]
