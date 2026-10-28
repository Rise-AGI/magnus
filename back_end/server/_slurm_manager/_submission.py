"""Submission outcomes and exact-identity reconciliation, independent of jobs."""
import re
import subprocess
from collections.abc import Callable


class SubmissionUncertain(RuntimeError):
    """The request may have reached Slurm; retrying could duplicate execution."""


class SubmissionDeferred(RuntimeError):
    """Slurm explicitly refused a temporarily exhausted submission quota."""


class SubmissionRejected(RuntimeError):
    """A definite scheduler rejection, safe to finalize without reconciliation."""


_SUBMIT_LIMIT = re.compile(
    r"\b(?:AssocMaxSubmitJobLimit|AssocGrpSubmitJobsLimit|"
    r"QOSMaxSubmitJobPerUserLimit|QOSGrpSubmitJobsLimit)\b",
)
_JOB_ID = re.compile(r"[1-9][0-9]*\Z")


def submit_batch(
    transport,
    command: list[str],
    script: str,
    environment: dict[str, str],
    before_start: Callable[[], None] | None = None,
) -> str:
    try:
        result = transport.run(
            command,
            input=script,
            env=environment,
            check=True,
            timeout=60,
            before_start=before_start,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip()
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        token = environment.get("MAGNUS_TOKEN")
        if token:
            detail = detail.replace(token, "[redacted]")
        if error.returncode < 0 or error.returncode == 255 or not detail or re.search(
            r"socket.*timed out|communication.*fail|slurm_receive_msg|zero bytes|unexpected message",
            detail,
            re.IGNORECASE,
        ):
            raise SubmissionUncertain("Slurm submission reply was lost; awaiting reconciliation") from None
        if _SUBMIT_LIMIT.search(detail):
            raise SubmissionDeferred("Slurm submission quota is temporarily exhausted") from None
        raise SubmissionRejected(
            f"Slurm rejected submission (exit {error.returncode}): {detail[-4096:] or 'no diagnostic returned'}",
        ) from None
    except InterruptedError:
        raise
    except (subprocess.TimeoutExpired, TimeoutError, OSError):
        # Even a connection failure may follow remote acceptance. Only an
        # explicit scheduler rejection proves that resubmission is safe.
        raise SubmissionUncertain("Slurm submission acceptance is unknown; awaiting reconciliation") from None
    if not isinstance(result.stdout, str):
        raise SubmissionUncertain("Slurm returned no valid job ID; awaiting reconciliation")
    reply = result.stdout.strip().split(";")
    if len(reply) > 2 or not _JOB_ID.fullmatch(reply[0]):
        raise SubmissionUncertain("Slurm returned no valid job ID; awaiting reconciliation")
    return reply[0]


def find_submissions(
    transport,
    comments: set[str],
    since: str,
    until: str,
) -> dict[str, str]:
    matches = {comment: set() for comment in comments}
    commands = [
        ["env", "TZ=UTC", "squeue", "--noheader", "--format=%i|%k"],
        ["env", "TZ=UTC", "sacct", "--noheader", "--parsable2", "--allocations", "--allusers",
         f"--starttime={since}", f"--endtime={until}", "--format=JobIDRaw,Comment%256"],
    ]
    for command in commands:
        try:
            result = transport.run(
                command,
                timeout=15,
                check=True,
            )
        except (OSError, TimeoutError, subprocess.SubprocessError):
            continue
        for line in result.stdout.splitlines():
            fields = line.strip().split("|")
            if len(fields) == 2 and fields[1].strip() in matches and _JOB_ID.fullmatch(fields[0].strip()):
                matches[fields[1].strip()].add(fields[0].strip())
    # Absence (including delayed or disabled accounting) cannot authorize retry.
    return {comment: next(iter(ids)) for comment, ids in matches.items() if len(ids) == 1}
