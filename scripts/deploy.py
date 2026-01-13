import os
import sys
import socket
import subprocess
from pathlib import Path
from typing import Optional

# --- Configuration & Constants ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "configs" / "magnus_config.yaml"
BACKEND_DIR = PROJECT_ROOT / "back_end"
FRONTEND_DIR = PROJECT_ROOT / "front_end"

FE_LOG_PATH = PROJECT_ROOT / "nohup_frontend_runtime.out"
BE_LOG_PATH = PROJECT_ROOT / "nohup_backend_runtime.out"

def parse_port_from_yaml(keyword: str) -> Optional[int]:
    """
    Extracts port configuration from YAML without external dependencies.
    """
    try:
        if not CONFIG_PATH.exists():
            return None
            
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                # Basic parsing: look for "key: value" pattern
                if keyword in line and ":" in line:
                    _, value = line.split(":", 1)
                    # Remove comments and whitespace
                    clean_value = value.split("#")[0].strip()
                    if clean_value.isdigit():
                        return int(clean_value)
    except Exception as e:
        print(f"[Error] Failed to parse config: {e}")
        sys.exit(1)
    return None

def is_port_in_use(port: int) -> bool:
    """Checks if a local port is currently occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def deploy():
    print(f"🚀 Starting Magnus Platform Deployment...")
    print(f"📂 Project Root: {PROJECT_ROOT}")

    # 1. Configuration Parsing
    fe_port = parse_port_from_yaml("front_end_port")
    be_port = parse_port_from_yaml("back_end_port")

    if not fe_port or not be_port:
        print("❌ Error: Could not parse ports from config.")
        sys.exit(1)

    print(f"   - Frontend Port: {fe_port}")
    print(f"   - Backend Port:  {be_port}")

    # 2. Port Availability Check
    if is_port_in_use(fe_port):
        print(f"❌ Error: Frontend port {fe_port} is already in use.")
        sys.exit(1)
    if is_port_in_use(be_port):
        print(f"❌ Error: Backend port {be_port} is already in use.")
        sys.exit(1)

    # 3. Launch Backend (UV)
    print("🔄 Launching Backend (UV)...")
    be_env = os.environ.copy()
    be_env["PYTHONUNBUFFERED"] = "1"
    be_log = open(BE_LOG_PATH, "w")
    be_proc = subprocess.Popen(
        ["uv", "run", "-m", "server.main", "--deliver"],
        cwd=BACKEND_DIR,
        stdout=be_log,
        stderr=be_log,
        start_new_session=True,
        env = be_env,
    )
    print(f"✅ Backend started [PID: {be_proc.pid}] -> {BE_LOG_PATH.name}")

    # 4. Launch Frontend (Next.js)
    print("🔄 Launching Frontend (Next.js Production)...")
    fe_env = os.environ.copy()
    fe_env["MAGNUS_DELIVER"] = "TRUE"
    
    fe_log = open(FE_LOG_PATH, "w")
    fe_proc = subprocess.Popen(
        ["npm", "run", "start", "--", "-p", str(fe_port), "-H", "0.0.0.0"],
        cwd=FRONTEND_DIR,
        stdout=fe_log,
        stderr=fe_log,
        start_new_session=True,
        env=fe_env,
    )
    print(f"✅ Frontend started [PID: {fe_proc.pid}] -> {FE_LOG_PATH.name}")

    print("\n🎉 Deployment Initiated Successfully!")
    print("   Monitor logs via: tail -f nohup_*.out")

if __name__ == "__main__":
    deploy()