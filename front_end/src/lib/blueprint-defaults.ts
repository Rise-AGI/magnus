// front_end/src/lib/blueprint-defaults.ts
export const DEFAULT_CODE_TEMPLATE = `from typing import Annotated

UserName = Annotated[str, {
    "label": "User Name",
    "placeholder": "enter your username on liustation2 here",
}]

GpuCount = Annotated[int, {
    "label": "GPU Count",
    "min": 1, 
    "max": 3, 
}]

Timeout = Annotated[str, {
    "label": "Timeout After",
    "placeholder": "e.g. 120 or infinity",
    "description": "Integer in minutes or 'infinity' for no time limit."
}]

def generate_job(
    user_name: UserName,
    gpu_count: GpuCount = 1,
    timeout: Timeout = "infinity",
) -> JobSubmission:
    
    timeout_value = timeout.strip().lower()
    
    command_suffix = ""
    if timeout_value != "infinity":
        command_suffix = f" {int(timeout_value)}"
        
    entry_command = f"""cd back_end/python_scripts
python magnus_debug.py{command_suffix}"""

    time_display = "无限" if timeout_value == "infinity" else f"{int(timeout_value)}分钟"
    
    description = f"""## Magnus 占卡调试任务
- 使用人：{user_name}
- GPU数量：{gpu_count}
- 使用时长：{time_display}
- 使用方式：

\`\`\`bash
magnus-connect
\`\`\`"""

    return JobSubmission(
        task_name = "Magnus Debug",
        description = description,
        namespace = "PKU-Plasma",
        repo_name = "magnus",
        branch = "main",
        commit_sha = "HEAD",
        entry_command = entry_command,
        gpu_count = gpu_count,
        gpu_type = "rtx5090",
        job_type = JobType.A2,
        runner = user_name,
    )
`;