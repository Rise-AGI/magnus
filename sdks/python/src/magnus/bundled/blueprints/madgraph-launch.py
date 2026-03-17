Process = Annotated[FileSecret, {
    "placeholder": "114514-apple-banana-cat",
    "description": "已编译 process 目录的传输密钥（madgraph-compile 的产出）",
}]

Commands = Annotated[str, {
    "placeholder": "done\nset nevents 1000\ndone",
    "description": "MG5 launch body（state machine 格式）",
}]

Output = Annotated[str, {
    "placeholder": "path/to/output",
    "description": "输出目录路径",
}]

Pdf = Annotated[Optional[str], {
    "placeholder": "LUXlep-NNPDF31_nlo_as_0118_luxqed",
    "description": "LHAPDF PDF 集名称（可选，从 CERN 下载安装）",
}]


def blueprint(
    process: Process,
    commands: Commands,
    output: Output,
    pdf: Pdf = None,
):

    description = "## MadGraph5 事件生成任务"

    safe_process = process.replace("'", "'\\''")
    safe_commands = commands.replace("'", "'\\''")
    safe_target = output.replace("'", "'\\''")

    pdf_arg = ""
    if pdf is not None:
        safe_pdf = pdf.replace("'", "'\\''")
        pdf_arg = f" --pdf '{safe_pdf}'"

    system_entry_command = """
# No system entry command needed.
"""

    entry_command = f"""
export PYTHONUSERBASE=/tmp/magnus-pip-$MAGNUS_JOB_ID
export PIP_CACHE_DIR=/tmp/magnus-pip-cache-madgraph-launch
pip3 install "magnus-sdk>=0.5.5" --quiet
python3 scripts/on_magnus/run_madgraph_launch.py --process_secret '{safe_process}' --launch_commands '{safe_commands}' --target_path '{safe_target}'{pdf_arg}
"""

    submit_job(
        task_name = "[Blueprint] MadGraph Launch",
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

