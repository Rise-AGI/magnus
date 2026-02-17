# docker/magnus-runtime/Dockerfile
#
# IMPORTANT: rootlesskit (bridge networking) remaps UID to 0.
# apptainer --containall then mounts an empty tmpfs over /root.
# Therefore NOTHING under /root/ is accessible at runtime.
# All tools must live in /usr/local/bin/ or /opt/.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# System packages: slurm (full), munge, build essentials
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
        slurm-client \
        slurmctld \
        slurmd \
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

# SLURM spool directories (writable at runtime for single-node child cluster)
RUN mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm /run/slurm \
    && chmod 755 /var/spool/slurmctld /var/spool/slurmd

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
# Cache on squashfs (SIF) + venv on bind mount = cross-filesystem, hardlinks impossible
ENV UV_LINK_MODE=copy

# Python 3.14 via uv
RUN uv python install 3.14 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python3 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python

# Pre-populate uv cache using the real project lockfile.
# `uv sync --frozen` populates the archive cache in the exact format that
# job-time `uv sync` expects, unlike `uv pip install` which uses a different
# cache structure. magnus-sdk and pywheels are small and fast-moving, but
# removing them would break --frozen; they'll be re-downloaded at job time
# when versions drift, which is fine (they're tiny).
# Build-time proxy: docker build --network=host --build-arg HTTP_PROXY=... ...
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

# Apptainer (for nested container execution inside child Magnus)
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends software-properties-common \
    && add-apt-repository -y ppa:apptainer/ppa \
    && apt-get update -qq \
    && apt-get install -y --no-install-recommends apptainer \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged mode — container filesystem doesn't support suid
RUN sed -i 's/^allow setuid = yes/allow setuid = no/' /etc/apptainer/apptainer.conf

CMD ["/bin/bash"]
