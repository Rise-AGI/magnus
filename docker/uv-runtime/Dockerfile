# docker/uv-runtime/Dockerfile
#
# Lightweight uv-only runtime for blueprints that just need Python + git.
# No SLURM, no Node.js, no Apptainer — minimal attack surface.
#
# Build (behind proxy):
#   cp $(which uv) docker/uv-runtime/uv
#   docker build --network=host \
#       --build-arg HTTP_PROXY=$HTTP_PROXY \
#       --build-arg HTTPS_PROXY=$HTTPS_PROXY \
#       --build-arg ALL_PROXY=$ALL_PROXY \
#       -f docker/uv-runtime/Dockerfile -t parkcai/uv-runtime:latest .
#   rm docker/uv-runtime/uv
#
# IMPORTANT: rootlesskit (bridge networking) remaps UID to 0.
# apptainer --containall then mounts an empty tmpfs over /root.
# Therefore NOTHING under /root/ is accessible at runtime.
# All tools must live in /usr/local/bin/ or /opt/.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Build-time proxy (docker build --build-arg HTTP_PROXY=... ...)
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG ALL_PROXY

# Minimal system packages
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# uv package manager — COPY from build context (avoids network fetch of install.sh)
COPY docker/uv-runtime/uv /usr/local/bin/uv
RUN chmod +x /usr/local/bin/uv

# All uv-managed assets live under /opt/uv/ (outside /root/)
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV UV_CACHE_DIR=/opt/uv/cache
# Cache on squashfs (SIF) + venv on bind mount = cross-filesystem, hardlinks impossible
ENV UV_LINK_MODE=copy

# Python 3.14 via uv
RUN uv python install 3.14 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python3 \
    && ln -sf $(uv python find 3.14) /usr/local/bin/python

CMD ["/bin/bash"]
