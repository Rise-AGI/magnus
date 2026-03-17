Process = Annotated[str, {
    "placeholder": "p p > t t~",
    "description": "过程定义，每行一个。第一行为 generate，其余为 add process",
    "allow_empty": False,
    "multi_line": True,
    "min_lines": 5,
}]

Output = Annotated[str, {
    "placeholder": "e.g. tmp/pp_ttbar",
    "description": "MadGraph5 process directory 的输出路径（仅远端调用时生效）",
    "allow_empty": False,
}]


UFO = Annotated[Optional[FileSecret], {
    "placeholder": "114514-apple-banana-cat",
    "description": "UFO 标准输出的传输密钥（自定义模型时提供，与 model 二选一）",
}]


Model = Annotated[Optional[str], {
    "placeholder": "e.g. sm",
    "description": "MG5 内置模型名称（如 sm、mssm 等，与 UFO 二选一）",
}]

Definitions = Annotated[Optional[str], {
    "placeholder": "e.g. l+ = e+ mu+",
    "description": "多粒子定义，每行一个（不含 define 前缀，可选）",
    "multi_line": True,
}]


def blueprint(
    process: Process,
    output: Output,
    ufo: UFO = None,
    model: Model = None,
    definitions: Definitions = None,
):

    description = "## MadGraph5 过程编译任务"

    safe_process = process.replace("'", "'\\''")
    safe_target = output.replace("'", "'\\''")

    definitions_arg = ""
    if definitions is not None and definitions.strip():
        safe_definitions = definitions.replace("'", "'\\''")
        definitions_arg = f" --definitions '{safe_definitions}'"

    ufo_arg = ""
    model_arg = ""
    if ufo is not None:
        safe_ufo = ufo.replace("'", "'\\''")
        ufo_arg = f" --ufo_secret '{safe_ufo}'"
    elif model is not None and model.strip():
        safe_model = model.replace("'", "'\\''")
        model_arg = f" --model '{safe_model}'"

    system_entry_command = """
# No system entry command needed.
"""

    entry_command = f"""
export PYTHONUSERBASE=/tmp/magnus-pip-$MAGNUS_JOB_ID
export PIP_CACHE_DIR=/tmp/magnus-pip-cache-madgraph-compile
pip3 install "magnus-sdk>=0.5.5" --quiet
python3 scripts/on_magnus/run_madgraph_compile.py --process '{safe_process}' --target_path '{safe_target}'{ufo_arg}{model_arg}{definitions_arg}
"""

    submit_job(
        task_name = "[Blueprint] MadGraph Compile",
        description = description,
        namespace = "HET-AGI",
        repo_name = "Collider-Agent",
        commit_sha = "HEAD",
        system_entry_command = system_entry_command,
        entry_command = entry_command,
        container_image = "docker://git.pku.edu.cn/het-agi/collider:latest",
        job_type = JobType.A2,
        runner = "magnus",
        cpu_count = 10,
        memory_demand = "64G",
    )

