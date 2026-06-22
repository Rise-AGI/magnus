# sdks/python/src/magnus/file_transfer.py
import os
from typing import Optional

FILE_SECRET_PREFIX = "magnus-secret:"

# Set by the magnus job wrapper on no-network remote-execution sites: when
# present, custody uploads are written into this directory instead of POSTed
# over HTTP. The magnus host pulls the directory back over the transport and
# registers each entry in custody under its token, so the returned FileSecret
# still resolves once the job's outputs are staged back. See Client._drop_file
# for the on-disk layout. Unset (the local / owned-cluster default) keeps the
# direct HTTP upload path unchanged.
ENV_CUSTODY_DROP_DIR = "MAGNUS_CUSTODY_DROP_DIR"

# Symmetric inbound counterpart of ENV_CUSTODY_DROP_DIR. Set by the same wrapper on
# no-network remote-execution sites: before the job runs, the magnus host stages the
# custody files referenced by the job's entry_command into this directory (one
# <token>/ subdir each, same layout as the drop dir). When present, download_file
# resolves a token from here (filesystem read) instead of POSTing over HTTP, so
# `magnus receive` works on compute nodes that cannot reach the backend. Unset (the
# local / owned-cluster default) keeps the direct HTTP download path unchanged.
ENV_CUSTODY_DROPIN_DIR = "MAGNUS_CUSTODY_DROPIN_DIR"


def is_file_secret(value: str) -> bool:
    return value.startswith(FILE_SECRET_PREFIX)


def normalize_secret(file_secret: str) -> str:
    if file_secret.startswith(FILE_SECRET_PREFIX):
        return file_secret[len(FILE_SECRET_PREFIX):]
    return file_secret


def get_tmp_base() -> Optional[str]:
    """返回文件中转目录。Magnus job 内返回容器可写层路径，否则 None (fallback 系统 /tmp)。

    背景：Magnus 容器有两种可写层模式：
    - overlay 模式：可写层是 ext3 镜像，受 ephemeral_storage 约束
    - writable-tmpfs 模式：可写层是 RAM tmpfs，受 memory_demand 约束
    两种模式下 /tmp 都在可写层内，但默认 tmpfs 容量可能很小。

    此函数将中转目录放在 $MAGNUS_HOME/.tmp/（容器可写层），而非
    $MAGNUS_HOME/workspace/.tmp/（host 磁盘 bind mount），以保持容器隔离。
    中转文件与容器内其他写入（pip install 等）共享同一写入预算——
    这正是用户通过 ephemeral_storage / memory_demand 声明的预期。

    判据：MAGNUS_HOME 环境变量存在 + workspace 目录已由 Magnus runtime 创建。
    嵌套容器场景下 bind-mount 链保持不变，此函数同样有效。
    """
    magnus_home = os.environ.get("MAGNUS_HOME")
    if magnus_home:
        workspace = os.path.join(magnus_home, "workspace")
        if os.path.isdir(workspace):
            tmp_base = os.path.join(magnus_home, ".tmp")
            os.makedirs(tmp_base, exist_ok=True)
            return tmp_base
    return None
