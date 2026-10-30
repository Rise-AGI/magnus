"""Job-scoped SIF preparation, also embedded in Python 3.6 compute wrappers."""

import os
import shlex
import tempfile
from typing import NamedTuple


class RuntimeImage(NamedTuple):
    image_path: str
    temporary_path: str
    preparation_shell: str


def validate_runtime_image_options(
    runtime: str,
    mode: str,
    timeout_seconds: int,
) -> None:
    if runtime not in ("apptainer", "singularity"):
        raise ValueError("Unsupported Slurm container runtime")
    if mode not in ("native", "sandbox"):
        raise ValueError("Unsupported container image mode")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("Container preparation timeout must be a positive integer")


def check_runtime_image_owner(
    mode: str,
    ephemeral_directory: str,
) -> None:
    # Extracted image permissions can mask inherited ACLs. A different owner
    # cannot reliably reclaim the tree, including after a hard kill.
    if mode == "sandbox" and os.stat(ephemeral_directory).st_uid != os.geteuid():
        raise RuntimeError("Sandbox image mode requires ownership of the Job temporary directory")


def prepare_runtime_image(
    runtime: str,
    mode: str,
    source_image: str,
    ephemeral_directory: str,
    timeout_seconds: int,
) -> RuntimeImage:
    validate_runtime_image_options(
        runtime,
        mode,
        timeout_seconds,
    )
    check_runtime_image_owner(
        mode,
        ephemeral_directory,
    )
    if mode == "native":
        return RuntimeImage(
            source_image,
            "",
            "",
        )
    # Each attempt owns its directory. A failed conversion is never reused as
    # a ready image; the wrapper runs this command before any task fan-out.
    directory = tempfile.mkdtemp(
        prefix="runtime-",
        dir=os.path.join(ephemeral_directory, ".magnus_tmp"),
    )
    image = os.path.join(directory, "rootfs")
    command = " ".join(
        shlex.quote(argument)
        for argument in (
            "timeout", "--signal=TERM", "--kill-after=5", str(timeout_seconds),
            runtime, "build", "--sandbox", "--notest", "--disable-cache", image, source_image,
        )
    )
    shell = (
        'echo "[Magnus] Preparing one read-only sandbox for this Job"\n'
        "if " + command + "; then :; else\n"
        "_prepare_status=$?\n"
        'echo "[Magnus] Runtime image preparation failed (exit $_prepare_status); '
        'no tasks started" >&2\n'
        'exit "$_prepare_status"\nfi\n'
        'echo "[Magnus] Runtime image preparation complete"\n'
    )
    return RuntimeImage(
        image,
        os.path.join(directory, "temporary"),
        shell,
    )


def runtime_temporary_bind_shell(
    runtime: str,
    mode: str,
) -> str:
    if mode != "sandbox":
        return ""
    prefix = runtime.upper()
    # Same-node tasks share Job-private scratch for MPI local transports.
    return f'''_node_id="$SLURM_NODEID"
[ -n "$_node_id" ] || _node_id=0
case "$_node_id" in ''|*[!0-9]*) echo "[Magnus] Invalid Slurm node identity" >&2; exit 1;; esac
_job_tmp="$MAGNUS_RUNTIME_TEMP/$_node_id"
mkdir -p "$_job_tmp/tmp" "$_job_tmp/sdk"
export {prefix}_BIND="${prefix}_BIND,$_job_tmp/tmp:/tmp,$_job_tmp/tmp:/var/tmp,$_job_tmp/sdk:$MAGNUS_HOME/.tmp"
export {prefix}ENV_TMPDIR=/tmp
'''
