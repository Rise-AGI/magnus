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
    """Use the runtime-provided scratch directory inside a Magnus container.

    The runtime supplies $MAGNUS_HOME/.tmp through an overlay, tmpfs, or a
    job-private bind mount. Capacity follows that runtime mode; the SDK does
    not impose a separate disk quota. Outside Magnus, use tempfile's default.
    """
    magnus_home = os.environ.get("MAGNUS_HOME")
    if magnus_home:
        workspace = os.path.join(magnus_home, "workspace")
        if os.path.isdir(workspace):
            tmp_base = os.path.join(magnus_home, ".tmp")
            os.makedirs(tmp_base, exist_ok=True)
            return tmp_base
    return None
