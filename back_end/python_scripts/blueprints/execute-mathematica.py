# ============ 复制进 web 端时省略这些导入 ============
from magnus import submit_job, JobType
from typing import Annotated, Literal, Optional, List
# =====================================================
MathematicaCode = Annotated[str, {
    "label": "Mathematica Code",
    "description": "请输入要执行的 Mathematica 代码",
    "placeholder": "Solve[x^2 - 5x + 6 == 0, x]",
    "allow_empty": False,
    "multi_line": True,
    "min_lines": 5,
}]

Timeout = Annotated[float, {
    "label": "Timeout (seconds)",
    "description": "代码执行超时时间",
    "scope": "Advanced",
    "min": 1.0,
}]

MagnusAddress = Annotated[str, {
    "label": "Magnus Address",
    "description": "Magnus 平台的 API 地址",
    "scope": "Advanced",
}]


def blueprint(
    code: MathematicaCode,
    timeout: Timeout = 300.0,
    address: MagnusAddress = "http://127.0.0.1:3011",
):

    description = f"""## Mathematica 代码运行任务

- 执行的代码：

```mathematica
{code}
```"""

    safe_code = code.replace("'", "'\\''")
    safe_address = address.replace("'", "'\\''")
    entry_commands = [
        "cd back_end/python_scripts",
        "uv sync --quiet",
        f"uv run execute_mathematica.py --address '{safe_address}' --code '{safe_code}' --timeout {timeout}",
    ]
    entry_command = "\n".join(entry_commands)

    submit_job(
        task_name = "[Blueprint] Execute Mathematica",
        description = description,
        repo_name = "magnus",
        entry_command=entry_command,
        job_type = JobType.A2,
        memory_demand = "100M",
    )
