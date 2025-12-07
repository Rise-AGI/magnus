#!/bin/bash
#SBATCH --job-name=test_rtx5090_nvlink
#SBATCH --partition=debug
#SBATCH --output=slurm_%j.out
#SBATCH --gres=gpu:rtx5090:2
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

echo "=== Slurm 分配的资源 ==="
echo "CUDA_VISIBLE_DEVICES: $CUDA_VISIBLE_DEVICES"
nvidia-smi -L

echo "=== 开始 Python 侦测 ==="
PYTHONUNBUFFERED=1
uv run -m scripts.tests.test_rtx5090_nvlink.test_rtx5090_nvlink