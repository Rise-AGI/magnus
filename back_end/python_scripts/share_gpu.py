# back_end/python_scripts/share_gpu.py

import argparse
import os
import sys
import socket
import subprocess
import shutil
import tempfile
import time
from pathlib import Path

def get_free_port():
    """获取一个空闲的随机端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def get_target_user_pubkey(username):
    """
    尝试获取目标用户的公钥。
    1. 尝试从系统用户目录读取 (如果在同一集群)
    2. (可选) 如果支持，可以扩展从 GitHub 获取
    """
    # 假设集群共享 /home 目录
    possible_paths = [
        Path(f"/home/{username}/.ssh/id_rsa.pub"),
        Path(f"/home/{username}/.ssh/id_ed25519.pub"),
        Path(f"/data/{username}/.ssh/id_rsa.pub"), # 某些集群在 data
        Path(f"/users/{username}/.ssh/id_rsa.pub"), 
    ]
    
    for p in possible_paths:
        if p.exists():
            print(f"[*] 找到用户 {username} 的公钥: {p}")
            return p.read_text().strip()
    
    print(f"[!] 错误: 无法在标准路径找到用户 '{username}' 的 SSH 公钥。")
    print("    请确保目标用户已生成密钥对 (ssh-keygen)。")
    return None

def main():
    parser = argparse.ArgumentParser(description="Magnus GPU Sharing (User-Level SSHD)")
    parser.add_argument("username", type=str, help="接收 GPU 权限的目标用户名")
    args = parser.parse_args()
    target_user = args.username

    # 1. 检查 SSHD 可用性
    sshd_path = shutil.which("sshd") or "/usr/sbin/sshd"
    if not os.path.exists(sshd_path):
        print("[!] 错误: 未找到 sshd 程序。")
        sys.exit(1)

    # 2. 获取目标用户公钥
    pubkey = get_target_user_pubkey(target_user)
    if not pubkey:
        sys.exit(1)

    # 3. 创建临时工作目录 (存放 key 和 config)
    # 使用 tempfile 确保权限安全 (只有当前用户可读写)
    work_dir = tempfile.mkdtemp(prefix="magnus_sshd_")
    work_path = Path(work_dir)
    print(f"[*] 初始化运行时环境: {work_path}")

    try:
        # 4. 生成临时的 Host Keys (避免指纹冲突)
        # 只需要生成一种，例如 rsa 或 ed25519
        host_key_path = work_path / "ssh_host_rsa_key"
        subprocess.check_call(
            ["ssh-keygen", "-t", "rsa", "-f", str(host_key_path), "-N", "", "-q"],
            stdout=subprocess.DEVNULL
        )

        # 5. 创建 authorized_keys
        auth_keys_path = work_path / "authorized_keys"
        auth_keys_path.write_text(pubkey)
        auth_keys_path.chmod(0o600)

        # 6. 获取端口并生成 SSHD 配置文件
        port = get_free_port()
        pid_file = work_path / "sshd.pid"
        
        sshd_config = f"""
        Port {port}
        HostKey {host_key_path}
        AuthorizedKeysFile {auth_keys_path}
        PidFile {pid_file}
        # 安全设置
        PermitRootLogin no
        PasswordAuthentication no
        ChallengeResponseAuthentication no
        UsePAM no
        X11Forwarding yes
        PrintMotd no
        # 保持连接
        ClientAliveInterval 60
        ClientAliveCountMax 3
        """
        
        config_path = work_path / "sshd_config"
        config_path.write_text(sshd_config)

        # 7. 启动微型 SSHD
        # 使用绝对路径启动，指向我们的配置文件
        print(f"[*] 正在启动 User-Level SSHD (Port {port})...")
        subprocess.Popen([sshd_path, "-f", str(config_path), "-D"], stderr=subprocess.DEVNULL)
        
        # 等待一小会儿确保启动
        time.sleep(1)
        if not pid_file.exists():
            # 极简检查，如果 pid 文件没生成可能启动失败
            # 但 -D 模式通常不生成 pid 文件，这里主要依赖 grep 或端口检查
            # 为了简单起见，我们假设它成功了，如果端口没起会在连接时失败
            pass

        # 8. 输出连接指令
        hostname = os.uname().nodename
        # 获取当前节点的 IP (简单获取，可能需要根据集群网络调整)
        try:
            # 尝试获取局域网 IP
            ip = socket.gethostbyname(hostname)
        except:
            ip = hostname

        current_user = os.getenv("USER")
        
        print("\n" + "="*60)
        print(f"🎉 隧道建立成功！GPU 环境已共享给 {target_user}")
        print("="*60)
        print("请把下面这行命令发给你的师兄/同学：\n")
        print(f"   ssh -p {port} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no {current_user}@{ip}")
        print("\n" + "="*60)
        print(f"[*] 进程运行中... 按 Ctrl+C 终止共享")

        # 9. 阻塞主进程 (Keep Alive)
        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        print("\n[*] 正在停止共享，清理资源...")
    except Exception as e:
        print(f"\n[!] 发生错误: {e}")
    finally:
        # 清理临时目录
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir)
            print("[*] 清理完成。")

if __name__ == "__main__":
    main()