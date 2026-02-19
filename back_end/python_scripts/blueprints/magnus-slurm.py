# ============ 复制进 web 端时省略这些导入 ============
from magnus import submit_job, JobType
from typing import Annotated, Literal, Optional, List
# =====================================================
UserName = Annotated[str, {
    "label": "User Name",
    "placeholder": "enter your username on liustation2 here",
    "allow_empty": False,
}]

WorkingDirectory = Annotated[str, {
    "label": "Working Directory",
    "placeholder": "e.g. /home/magnus/local-project",
    "allow_empty": False,
}]

SlurmScript = Annotated[str, {
    "label": "Slurm Script",
    "placeholder": "enter the script you want to execute in `working_directory` here",
    "allow_empty": False,
    "multi_line": True,
    "border_color": "rgb(34,69,128)",
}]

CpuCount = Annotated[int, {
    "label": "CPU Count",
    "min": 1, 
    "max": 128, 
}]

MemoryDemand = Annotated[str, {
    "label": "Memory",
    "placeholder": "e.g. 1600M, 12G",
    "allow_empty": False,
}]

GpuCount = Annotated[int, {
    "label": "GPU Count",
    "min": 0, 
    "max": 3, 
}]

CondaEnvironment = Annotated[str, {
    "label": "Conda Environment",
    "placeholder": "e.g. base, magnus_shared",
    "allow_empty": False,
}]

OutputPath = Annotated[str, {
    "label": "Output Path",
    "description": "stderr also goes here",
}]

def blueprint(
    user_name: UserName,
    working_directory: WorkingDirectory,
    slurm_script: SlurmScript,
    cpu_count: CpuCount = 1,
    memory_demand: MemoryDemand = "1600M",
    gpu_count: GpuCount = 0,
    conda_environment: CondaEnvironment = "magnus_shared",
    output_path: OutputPath = "",
):

    def safe_quote(s: str)->str:
        return f"'{str(s).replace("'", "'\\''")}'"

    entry_command = f"""cd back_end/python_scripts
python magnus_slurm.py \\
    --working-directory {safe_quote(working_directory)} \\
    --slurm-script {safe_quote(slurm_script)} \\
    --conda-environment {safe_quote(conda_environment)} \\
    --output-path {safe_quote(output_path)}"""

    description = f"""## Magnus 代交 Slurm 任务
- **使用人**：{user_name}
- **使用资源**：{cpu_count} CPU / {memory_demand} Mem / {gpu_count} GPU
- **工作路径**：{working_directory}
- **Conda 环境**：{conda_environment}

### Slurm 脚本内容
```bash
{slurm_script}
```
"""

    submit_job(
        task_name = "Magnus Slurm",
        description = description,
        repo_name = "magnus",
        entry_command = entry_command,
        gpu_count = gpu_count,
        gpu_type = "rtx5090",
        job_type = JobType.A2,
        runner = user_name,
    )
