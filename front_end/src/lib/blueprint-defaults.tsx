// front_end/src/lib/blueprint-defaults.tsx
import React from "react";

// Single source of truth for blueprint implicit imports
export const BLUEPRINT_IMPLICIT_IMPORTS = `from server import JobSubmission, JobType, FileSecret
from typing import Annotated, Literal, Optional, List`;

// Styled version for display
export function BlueprintImplicitImports() {
  return (
    <>
      <span className="text-purple-400">from</span> server <span className="text-purple-400">import</span> JobSubmission, JobType, FileSecret{"\n"}
      <span className="text-purple-400">from</span> typing <span className="text-purple-400">import</span> Annotated, Literal, Optional, List
    </>
  );
}

export const DEFAULT_CODE_TEMPLATE = `UserName = Annotated[str, {
    "label": "User Name",
    "placeholder": "your username on the cluster",
    "allow_empty": False,
}]

GpuCount = Annotated[int, {
    "label": "GPU Count",
    "min": 1,
    "max": 4,
}]

Priority = Annotated[Literal["A1", "A2", "B1", "B2"], {
    "label": "Priority",
    "description": "A1/A2: high priority (non-preemptible), B1/B2: low priority (preemptible by A)",
    "options": {
        "A1": {"label": "A1", "description": "Highest priority"},
        "A2": {"label": "A2", "description": "High priority"},
        "B1": {"label": "B1", "description": "Low priority"},
        "B2": {"label": "B2", "description": "Lowest priority"},
    },
}]

Runner = Annotated[Optional[str], {
    "label": "Runner",
    "description": "Override the default runner user",
    "placeholder": "leave empty for default",
}]


def generate_job(
    user_name: UserName,
    gpu_count: GpuCount = 1,
    priority: Priority = "A2",
    runner: Runner = None,
) -> JobSubmission:

    entry_command = """echo "Hello from Magnus!"
sleep 60"""

    description = f"""## My Blueprint Task
- User: {user_name}
- GPUs: {gpu_count}
- Priority: {priority}
"""

    return JobSubmission(
        task_name="My Task",
        description=description,
        namespace="YourOrg",
        repo_name="your-repo",
        branch="main",
        commit_sha="HEAD",
        entry_command=entry_command,
        gpu_count=gpu_count,
        gpu_type="rtx5090",
        job_type=JobType[priority],
        runner=runner,
    )
`;