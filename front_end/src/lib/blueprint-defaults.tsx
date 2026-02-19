// front_end/src/lib/blueprint-defaults.tsx
import React from "react";

// Single source of truth for blueprint implicit imports
export const BLUEPRINT_IMPLICIT_IMPORTS = `from magnus import submit_job, JobType, FileSecret
from typing import Annotated, Literal, Optional, List`;

// Styled version for display
export function BlueprintImplicitImports() {
  return (
    <>
      <span className="text-purple-400">from</span> magnus <span className="text-purple-400">import</span> submit_job, JobType, FileSecret{"\n"}
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


def blueprint(
    user_name: UserName,
    gpu_count: GpuCount = 1,
    priority: Priority = "A2",
    runner: Runner = None,
):
    submit_job(
        task_name=f"hello-{user_name}",
        entry_command=f"echo 'Hello, {user_name}!'",
        repo_name="your-repo",
        gpu_count=gpu_count,
        job_type=getattr(JobType, priority),
        runner=runner,
    )
`;