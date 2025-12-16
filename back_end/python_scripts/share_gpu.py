import argparse
import os
import sys
import subprocess
import signal
import time
import glob
from pathlib import Path
from datetime import datetime

# ================= 配置 =================
# 是否在 ACL 失败时尝试使用 chmod (666) 作为备选方案？
# True: 如果 setfacl 报错，尝试 chmod o+rw (不太安全，但在内网单机好用)
# False: 严格模式，ACL 失败则报错退出
ENABLE_CHMOD_FALLBACK = False 
# =======================================

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    icon = "[+]" if level == "INFO" else "[!]"
    if level == "ERROR": icon = "[x]"
    print(f"{icon} {timestamp} {msg}", flush=True)

def run_cmd_verbose(cmd):
    """
    执行命令并返回 (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            check=False
        )
        return (
            result.returncode == 0, 
            result.stdout.decode().strip(), 
            result.stderr.decode().strip()
        )
    except Exception as e:
        return False, "", str(e)

def get_physical_gpus():
    """
    探测当前环境分配的 GPU。
    优先读取 Slurm 环境变量，其次 CUDA 变量，最后暴力扫描。
    """
    gpus = set()

    # Priority 1: Slurm Environment
    slurm_gpus = os.getenv("SLURM_JOB_GPUS")
    if slurm_gpus:
        log(f"环境检测: 发现 Slurm 分配信息 -> {slurm_gpus}")
        # 处理格式 "0,1" 或 "0-2" 或 "0,2-4"
        parts = slurm_gpus.split(',')
        for part in parts:
            if '-' in part:
                try:
                    start, end = map(int, part.split('-'))
                    for i in range(start, end + 1):
                        gpus.add(str(i))
                except:
                    log(f"解析 Slurm ID 失败: {part}", "WARN")
            else:
                gpus.add(part.strip())
        return sorted(list(gpus))

    # Priority 2: CUDA Environment
    cuda_gpus = os.getenv("CUDA_VISIBLE_DEVICES")
    if cuda_gpus:
        log(f"环境检测: 发现 CUDA_VISIBLE_DEVICES -> {cuda_gpus}")
        if cuda_gpus.lower() not in ["none", ""]:
            # 注意：在某些虚拟化场景下，这里的 ID 可能是虚拟 ID
            # 但在裸机/Slurm setfacl 场景下，通常对应物理 ID
            parts = cuda_gpus.split(',')
            for part in parts:
                if part.strip().isdigit():
                    gpus.add(part.strip())
            return sorted(list(gpus))

    # Priority 3: Fallback (Scan all)
    log("环境检测: 无特定限制，扫描所有物理 GPU...")
    devices = glob.glob("/dev/nvidia[0-9]*")
    for dev in devices:
        dev_name = os.path.basename(dev)
        dev_id = dev_name.replace("nvidia", "")
        if dev_id.isdigit():
            gpus.add(dev_id)
    
    return sorted(list(gpus))

def manage_gpu_access(username, gpu_ids, action="grant"):
    """
    执行权限授予或回收
    action: 'grant' | 'revoke'
    """
    if not gpu_ids:
        log("无 GPU 可操作。", "WARN")
        return

    # 包含控制设备 (可选，为了最大兼容性通常建议加上)
    target_paths = [f"/dev/nvidia{gid}" for gid in gpu_ids]
    # target_paths.extend(["/dev/nvidiactl", "/dev/nvidia-uvm"]) 

    op_name = "授权" if action == "grant" else "回收"
    log(f"正在{op_name} (用户: {username})...")

    for dev_path in target_paths:
        if not os.path.exists(dev_path):
            log(f" -> {dev_path} : 跳过 (文件不存在)", "WARN")
            continue

        # 1. 尝试使用 setfacl (最佳实践)
        if action == "grant":
            cmd = ["setfacl", "-m", f"u:{username}:rw", dev_path]
        else:
            cmd = ["setfacl", "-x", f"u:{username}", dev_path]

        success, out, err = run_cmd_verbose(cmd)

        if success:
            log(f" -> {dev_path} : ACL 成功")
        else:
            # 失败处理
            log(f" -> {dev_path} : ACL 失败! 原因: {err}", "ERROR")
            
            # 2. 备选方案 (仅在 grant 且开启 fallback 时)
            if action == "grant" and ENABLE_CHMOD_FALLBACK:
                log(f"    [Fallback] 尝试使用 chmod o+rw {dev_path} ...", "WARN")
                chmod_cmd = ["chmod", "o+rw", dev_path]
                c_success, _, c_err = run_cmd_verbose(chmod_cmd)
                if c_success:
                    log(f"    -> chmod 成功 (注意：所有用户均可访问此卡)")
                else:
                    log(f"    -> chmod 失败: {c_err}", "ERROR")

def main():
    parser = argparse.ArgumentParser(description="Magnus Direct GPU Share (Final)")
    parser.add_argument("username", type=str, help="目标用户名")
    args = parser.parse_args()
    username = args.username

    # 1. Root 检查
    if os.geteuid() != 0:
        log("错误: 必须以 Root 身份运行。", "ERROR")
        sys.exit(1)

    # 2. 探测 GPU
    gpu_ids = get_physical_gpus()
    if not gpu_ids:
        log("错误: 未能探测到任何有效的 GPU ID。", "ERROR")
        sys.exit(1)
    
    log(f"目标设备 ID: {gpu_ids}")

    # 3. 注册清理函数
    def cleanup(signum, frame):
        print("\n", flush=True)
        log("收到停止信号，正在回滚权限...")
        manage_gpu_access(username, gpu_ids, action="revoke")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 4. 执行授权
    manage_gpu_access(username, gpu_ids, action="grant")

    # 5. 保持运行
    print("="*60, flush=True)
    print(f"🎉 服务已就绪。用户 '{username}' 现可访问 GPU {gpu_ids}")
    print(f"   PID: {os.getpid()} (请保持此进程运行)")
    print("="*60, flush=True)

    while True:
        time.sleep(10)

if __name__ == "__main__":
    main()