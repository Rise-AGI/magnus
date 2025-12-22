import os
import sys
import time
import signal
import datetime
from math import isinf

# === Configuration ===
HEARTBEAT_INTERVAL_NORMAL = 300  # Normal: 5 Minutes
HEARTBEAT_INTERVAL_URGENT = 60   # Urgent: 1 Minute (when ending soon)
WARNING_THRESHOLD_SECONDS = 300  # Warn 5 minutes before end
PREFIX = "[Magnus Debug]"

def handle_exit(signum, frame):
    """Handle termination signals gracefully (e.g. scancel)"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{timestamp}] {PREFIX} Session terminated by signal ({signum}). Cleaning up...", flush=True)
    sys.exit(0)

def main():
    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    # 1. Parse Arguments (Duration in minutes)
    duration_minutes = float('inf')
    if len(sys.argv) > 1:
        try:
            duration_minutes = float(sys.argv[1])
        except ValueError:
            print(f"{PREFIX} Invalid duration format. Using infinite mode.")

    # 2. Get Environment Info
    user = os.environ.get("SLURM_JOB_USER", "unknown")
    job_id = os.environ.get("SLURM_JOB_ID", "N/A")
    node_name = os.environ.get("SLURMD_NODENAME", os.uname().nodename)
    
    # 3. Print Welcome Banner (Clean Text Mode)
    print("="*60, flush=True)
    print(f"{PREFIX} Debug Session Started", flush=True)
    print(f"{PREFIX} For User:      {user}", flush=True)
    print(f"{PREFIX} Node:      {node_name}", flush=True)
    print(f"{PREFIX} Job ID:    {job_id}", flush=True)
    
    duration_str = 'Infinite' if isinf(duration_minutes) else f'{duration_minutes} min'
    print(f"{PREFIX} Duration:  {duration_str}", flush=True)
    print("-" * 60, flush=True)
    print(f"   To connect to this session, run the following command", flush=True)
    print(f"   on {node_name}:", flush=True)
    print(f"", flush=True)
    print(f"       magnus-connect", flush=True)
    print(f"", flush=True)
    print("="*60, flush=True)

    # 4. The Loop
    start_time = time.time()
    if isinf(duration_minutes):
        end_time = float('inf')
    else:
        end_time = start_time + (duration_minutes * 60)
    
    print(f"{PREFIX} Main loop started. Waiting for connections...", flush=True)

    warned_termination = False

    while True:
        current_time = time.time()
        remaining = end_time - current_time

        # Check if time is up
        if not isinf(remaining) and remaining <= 0:
            print(f"{PREFIX} Time limit reached. Exiting.", flush=True)
            break
            
        # Calculate uptime and formatted timestamp
        uptime_seconds = int(current_time - start_time)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Check for Warning Threshold (e.g. less than 5 mins left)
        # Only applicable if duration is finite
        in_warning_zone = False
        if not isinf(remaining) and remaining <= WARNING_THRESHOLD_SECONDS:
            in_warning_zone = True
            if not warned_termination:
                print("-" * 60, flush=True)
                print(f"[{timestamp}] {PREFIX} !!! WARNING: Session ending in less than {int(remaining/60)} minutes !!!", flush=True)
                print("-" * 60, flush=True)
                warned_termination = True

        # Print Heartbeat
        # During warning zone, we might want to emphasize it, but plain text limits options.
        status_msg = "Heartbeat: Active"
        if in_warning_zone:
            status_msg = f"Heartbeat: ENDING SOON (Remaining: {int(remaining)}s)"
            
        print(f"[{timestamp}] {status_msg} (Uptime: {uptime})", flush=True)
        
        # Sleep logic
        # If inside warning zone, use shorter heartbeat interval (URGENT)
        # Otherwise use normal interval
        target_interval = HEARTBEAT_INTERVAL_URGENT if in_warning_zone else HEARTBEAT_INTERVAL_NORMAL
        
        if isinf(remaining):
            sleep_time = target_interval
        else:
            sleep_time = min(target_interval, remaining)
        
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()