Model = Annotated[FileSecret, {
    "placeholder": "114514-apple-banana-cat",
    "description": "FeynRules 模型文件的传输密钥（远端可直接输入机上路径）",
}]

Lagrangian = Annotated[str, {
    "placeholder": "LSM",
    "description": "拉氏量变量名（如 LSM, LSNP, LNEWPHYSICS）",
}]

TargetPath = Annotated[str, {
    "placeholder": "path/to/UFO",
    "description": "UFO 输出目录路径",
}]

Restriction = Annotated[Optional[FileSecret], {
    "placeholder": "114514-apple-banana-cat",
    "description": ".rst 限制文件的传输密钥（可选）",
}]


def blueprint(
    model: Model,
    lagrangian: Lagrangian,
    output: TargetPath,
    restriction: Restriction = None,
):

    description = "## FeynRules UFO 生成任务"

    safe_secret = model.replace("'", "'\\''")
    safe_lagrangian = lagrangian.replace("'", "'\\''")
    safe_target = output.replace("'", "'\\''")

    restriction_arg = ""
    if restriction is not None:
        safe_restriction = restriction.replace("'", "'\\''")
        restriction_arg = f" --restriction_secret '{safe_restriction}'"

    # system_entry_command deals with what happens outside container
    # this blueprint needs to mount MMA license in system_entry_command
    system_entry_command = """
mounts=(
    "$HOME/.wolfram-container-license:$HOME/.WolframEngine/Licensing"
)
export APPTAINER_BIND="${APPTAINER_BIND:+$APPTAINER_BIND,}$(IFS=,; echo "${mounts[*]}")"
"""

    entry_command = f"""
REAL_HOME=$(getent passwd $(whoami) | cut -d: -f6)
if [ "$REAL_HOME" != "/root" ] && [ -d /root/.WolframEngine/Applications ]; then
    mkdir -p "$REAL_HOME/.WolframEngine"
    ln -sf /root/.WolframEngine/Applications "$REAL_HOME/.WolframEngine/Applications"
    ln -sf /root/.WolframEngine/Kernel "$REAL_HOME/.WolframEngine/Kernel"
fi

uv pip install --python /usr/local/bin/python3 --break-system-packages --quiet "magnus-sdk>=0.5.7"

export PYTHONPATH=$PWD
python3 scripts/on_magnus/run_ufo_generation.py --secret '{safe_secret}' --lagrangian '{safe_lagrangian}' --target_path '{safe_target}'{restriction_arg}
"""

    submit_job(
        task_name = "[Blueprint] Generate UFO",
        description = description,
        namespace = "HET-AGI",
        repo_name = "Collider-Agent",
        commit_sha = "HEAD",
        system_entry_command = system_entry_command,
        entry_command = entry_command,
        container_image = "docker://git.pku.edu.cn/het-agi/mma-het:latest",
        job_type = JobType.A2,
        memory_demand = "10G",
        cpu_count = 10,
    )

