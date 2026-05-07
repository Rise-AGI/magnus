# back_end/server/_resource_manager/_helpers.py
"""Resource-manager 内部用的命名规范化与目录大小工具。"""
import os
import re


def _get_dir_size(path: str) -> int:
    """递归计算目录大小"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _image_to_sif_filename(image: str) -> str:
    """docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime -> pytorch_pytorch_2.5.1-cuda12.4-cudnn9-runtime.sif"""
    name = re.sub(r'^[a-z]+://', '', image)
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return f"{name}.sif"


def _repo_to_cache_dirname(namespace: str, repo_name: str, branch: str) -> str:
    """namespace/repo_name/branch -> namespace_repo_name_branch"""
    name = f"{namespace}_{repo_name}_{branch}"
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name
