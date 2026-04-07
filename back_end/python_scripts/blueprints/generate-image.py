# ============ 复制进 web 端时省略这些导入 ============
from magnus import submit_job, JobType, FileSecret
from typing import Annotated, Literal, Optional, List
# =====================================================
Prompt = Annotated[str, {
    "label": "Prompt",
    "description": "编辑指令或生成描述",
    "placeholder": "Make the shirt color blue and add sunglasses",
    "allow_empty": False,
    "multi_line": True,
    "min_lines": 3,
}]

Reference = Annotated[Optional[FileSecret], {
    "label": "Reference Image",
    "description": "参考图片，Kontext 将基于此图进行编辑",
}]

Width = Annotated[Optional[int], {
    "label": "Width",
    "description": "输出宽度，留空则保持原图尺寸",
    "scope": "Advanced",
    "min": 256,
    "max": 2048,
}]

Height = Annotated[Optional[int], {
    "label": "Height",
    "description": "输出高度，留空则保持原图尺寸",
    "scope": "Advanced",
    "min": 256,
    "max": 2048,
}]

Steps = Annotated[int, {
    "label": "Inference Steps",
    "description": "推理步数，越多越精细",
    "scope": "Advanced",
    "min": 1,
    "max": 50,
}]

GuidanceScale = Annotated[float, {
    "label": "Guidance Scale",
    "description": "引导强度，越高越忠实于 prompt",
    "scope": "Advanced",
    "min": 0.0,
    "max": 10.0,
}]

Seed = Annotated[Optional[int], {
    "label": "Random Seed",
    "description": "随机种子，留空则随机",
    "scope": "Advanced",
}]

HfToken = Annotated[Optional[str], {
    "label": "HuggingFace Token",
    "description": "下载 gated 模型权重所需，从 huggingface.co/settings/tokens 获取",
    "scope": "Advanced",
}]


def blueprint(
    prompt: Prompt,
    reference: Reference = None,
    width: Width = None,
    height: Height = None,
    num_inference_steps: Steps = 28,
    guidance_scale: GuidanceScale = 2.5,
    seed: Seed = None,
    hf_token: HfToken = None,
):

    safe_prompt = prompt.replace("'", "'\"'\"'")
    ref_arg = f" --reference-secret '{reference}'" if reference else ""
    seed_arg = f" --seed {seed}" if seed is not None else ""
    width_arg = f" --width {width}" if width is not None else ""
    height_arg = f" --height {height}" if height is not None else ""

    pip_line = "pip install magnus-sdk 'diffusers>=0.36.0' transformers accelerate sentencepiece protobuf pillow"
    run_parts = [
        "python back_end/python_scripts/generate_image.py",
        f"    --prompt '{safe_prompt}'",
        f"    --steps {num_inference_steps}",
        f"    --guidance-scale {guidance_scale}{width_arg}{height_arg}{ref_arg}{seed_arg}",
    ]
    entry_command = pip_line + "\n" + " \\\n".join(run_parts)

    size_str = f"{width}x{height}" if width and height else "原图尺寸"
    mode = "Kontext Edit" if reference else "Text-to-Image"
    description = f"""## Generate Image ({mode})

- **Prompt**: {prompt}
- **Size**: {size_str}
- **Steps**: {num_inference_steps}
"""

    sys_cmd_lines = [
        'mounts=(',
        '  "/home/magnus/.cache:$MAGNUS_HOME/.cache"',
        ')',
        'export APPTAINER_BIND=$(IFS=,; echo "${mounts[*]}")',
    ]
    if hf_token:
        sys_cmd_lines.append(f'export APPTAINERENV_HF_TOKEN="{hf_token}"')

    submit_job(
        task_name="[Blueprint] Generate Image",
        description=description,
        repo_name="magnus",
        entry_command=entry_command,
        container_image="docker://pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime",
        gpu_count=1,
        gpu_type="rtx5090",
        job_type=JobType.A2,
        memory_demand="64G",
        ephemeral_storage="30G",
        runner="magnus",
        system_entry_command="\n".join(sys_cmd_lines),
    )
