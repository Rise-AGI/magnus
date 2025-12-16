# back_end/python_scripts/share_gpu_direct.py

import argparse
import os
import sys
import subprocess
import signal
import time
import glob
from pathlib import Path
from datetime import datetime

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    icon = "[+]" if level == "INFO" else "[!]"
    if level == "ERROR": icon = "[x]"
    print(f"{icon} {timestamp} {msg}", flush=True)

def run_cmd(cmd):
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def get_physical_gpus():
    """
    自省：探测当前上下文中应该分享哪些 GPU。
    策略优先级：
    1. SLURM_JOB_GPUS (如果在 Slurm Job 内，这是最准确的物理 ID)
    2. CUDA_VISIBLE_DEVICES (如果有环境变量限制)
    3. 物理扫描 /dev/nvidia[0-9]* (如果是在裸机或容器内独占)
    """
    gpus = set()

    # 策略 1: 检查 Slurm 环境变量 (最推荐)
    slurm_gpus = os.getenv("SLURM_JOB_GPUS")
    if slurm_gpus:
        log(f"检测到 Slurm 环境，分配 ID: {slurm_gpus}")
        # 处理 slurm 格式，如 "0,1" 或 "0-3"
        # 简单处理逗号和单数字，复杂范围需额外解析
        for part in slurm_gpus.split(','):
            if '-' in part: # 处理 0-3 这种情况
                try:
                    start, end = map(int, part.split('-'))
                    for i in range(start, end + 1):
                        gpus.add(str(i))
                except:
                    pass
            else:
                gpus.add(part.strip())
        return sorted(list(gpus))

    # 策略 2: 检查 CUDA 变量
    cuda_gpus = os.getenv("CUDA_VISIBLE_DEVICES")
    if cuda_gpus:
        log(f"检测到 CUDA_VISIBLE_DEVICES: {cuda_gpus}")
        # 注意：这里假设变量里的 ID 是物理 ID。
        # 在某些容器化场景下这可能是逻辑 ID，但在裸机上通常对应物理 ID。
        if cuda_gpus.lower() == "all":
            # 如果是 all，落入策略 3
            pass
        elif cuda_gpus.lower() == "none":
            return []
        else:
            for part in cuda_gpus.split(','):
                gpus.add(part.strip())
            return sorted(list(gpus))

    # 策略 3: 暴力扫描 (兜底)
    # 扫描 /dev/nvidiaX，排除 nvidiactl 等控制设备
    log("未检测到环境约束，扫描所有物理设备...")
    devices = glob.glob("/dev/nvidia[0-9]*")
    for dev in devices:
        # 提取数字 ID /dev/nvidia0 -> 0
        dev_name = os.path.basename(dev)
        dev_id = dev_name.replace("nvidia", "")
        if dev_id.isdigit():
            gpus.add(dev_id)
    
    return sorted(list(gpus))

def set_gpu_acl(username, gpu_ids, action="grant"):
    """
    修改 ACL 权限
    action: 'grant' (rw) | 'revoke' (remove)
    """
    if not gpu_ids:
        log("没有探测到可用的 GPU，跳过 ACL 操作。", "WARN")
        return

    # 必须包含的基础控制设备 (否则 CUDA 初始化可能会挂)
    # 通常这些默认是对所有人可读写的，但为了保险起见，也可以加进去
    ctrl_devices = ["/dev/nvidiactl", "/dev/nvidia-uvm"]
    
    target_devices = []
    # 添加具体的 GPU 设备
    for gid in gpu_ids:
        path = f"/dev/nvidia{gid}"
        if os.path.exists(path):
            target_devices.append(path)
    
    # 执行 setfacl
    mode = "-m" if action == "grant" else "-x"
    perms = f"u:{username}:rw" if action == "grant" else f"u:{username}"
    
    op_name = "授权" if action == "grant" else "回收"
    log(f"正在执行 {op_name}操作 (用户: {username})...")

    for dev in target_devices:
        cmd = ["setfacl", mode, perms, dev]
        if run_cmd(cmd):
            log(f" -> {dev} : 成功")
        else:
            log(f" -> {dev} : 失败 (权限不足或文件不存在)", "ERROR")
            
    # 对控制设备也做一次确保 (可选，根据你的安全策略决定是否放开)
    # for dev in ctrl_devices:
    #    if os.path.exists(dev):
    #        run_cmd(["setfacl", mode, perms, dev])

def main():
    parser = argparse.ArgumentParser(description="Magnus Direct GPU Share")
    parser.add_argument("username", type=str, help="接收权限的用户名")
    args = parser.parse_args()
    username = args.username

    # 1. 权限自检
    # if os.geteuid() != 0:
    #     log("错误: 必须以 ROOT 身份运行此脚本 (用于修改 ACL)", "ERROR")
    #     sys.exit(1)

    # 2. 探测 GPU
    gpu_ids = get_physical_gpus()
    if not gpu_ids:
        log("未探测到任何 GPU 设备！退出。", "ERROR")
        sys.exit(1)
        
    log(f"探测到可用 GPU 物理 ID: {gpu_ids}")

    # 3. 注册退出信号 (确保 Ctrl+C 或被 kill 时能撤销权限)
    def shutdown(signum, frame):
        print("\n", flush=True)
        log("收到停止信号，正在回滚权限...")
        set_gpu_acl(username, gpu_ids, action="revoke")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 4. 授予权限
    try:
        set_gpu_acl(username, gpu_ids, action="grant")
        
        print("="*60, flush=True)
        print(f"🎉 GPU 共享已生效")
        print(f"   用户: {username}")
        print(f"   设备: {['/dev/nvidia'+i for i in gpu_ids]}")
        print("   状态: 进程守护中... (进程结束将自动回收权限)")
        print("="*60, flush=True)

        # 5. 守护进程 (Blocking)
        while True:
            time.sleep(10)

    except Exception as e:
        log(f"运行时发生异常: {e}", "ERROR")
        set_gpu_acl(username, gpu_ids, action="revoke")

if __name__ == "__main__":
    main()