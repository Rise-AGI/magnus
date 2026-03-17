Model = Annotated[FileSecret, {
    "placeholder": "114514-apple-banana-cat",
    "description": "FeynRules 模型文件的传输密钥（远端可直接输入机上路径）",
}]

Lagrangian = Annotated[str, {
    "placeholder": "LSM",
    "description": "拉氏量变量名（如 LSM, LmZp, Lag）",
}]

def blueprint(
    model: Model,
    lagrangian: Lagrangian,
):

    description = f"""## FeynRules 模型校验任务\n"""
    safe_secret = model.replace("'", "'\\''")
    safe_symbol = lagrangian.replace("'", "'\\''")

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
python3 scripts/on_magnus/run_feynrules_validation.py --secret '{safe_secret}' --symbol '{safe_symbol}'
"""

    submit_job(
        task_name = "[Blueprint] Validate FeynRules",
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

