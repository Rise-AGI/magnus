# sdks/python/src/magnus/bundled/blueprints/hello-world.py
from magnus import submit_job, JobType
from typing import Annotated

Message = Annotated[str, {
    "label": "Message",
    "placeholder": "enter your message here",
    "description": "The message to echo inside the container.",
}]

SleepSeconds = Annotated[int, {
    "label": "Sleep (seconds)",
    "min": 0,
    "max": 60,
    "description": "Seconds to sleep before printing. Demonstrates long-running jobs.",
}]

def blueprint(
    message: Message = "Hello from Magnus!",
    sleep_seconds: SleepSeconds = 0,
):
    safe_msg = message.replace("'", "'\\''")
    entry_command = f"sleep {sleep_seconds} && echo '{safe_msg}'"

    submit_job(
        task_name="Hello World",
        description=f"Demo: echo '{safe_msg}' after {sleep_seconds}s",
        repo_name="magnus",
        namespace="Rise-AGI",
        entry_command=entry_command,
        job_type=JobType.A2,
        container_image="docker://ubuntu:24.04",
    )
