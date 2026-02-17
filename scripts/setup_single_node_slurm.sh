#!/usr/bin/env bash
# scripts/setup_single_node_slurm.sh
# Executed by Magnus blueprint `reproduce-magnus` on cloud.
# THIS IS NOT DEAD CODE.
#
# Bootstrap a single-node, CPU-only SLURM cluster inside a container.
# Usage: bash setup_single_node_slurm.sh [--cpus N] [--memory-mb M] [--hostname NAME]
#
# Root cause log (2026-02):
#   PartitionName=default is a SLURM reserved word (case-insensitive).
#   It defines a DEFAULT template, not an actual partition. Use any other name (e.g. batch).

set -euo pipefail

CPUS=4
MEMORY_MB=8192
NODE_HOSTNAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cpus)      CPUS="$2";           shift 2 ;;
        --memory-mb) MEMORY_MB="$2";      shift 2 ;;
        --hostname)  NODE_HOSTNAME="$2";  shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

[[ -z "$NODE_HOSTNAME" ]] && NODE_HOSTNAME=$(hostname -s)

echo "[SLURM] hostname=$NODE_HOSTNAME, CPUs=$CPUS, Memory=${MEMORY_MB}MB"

# 1. Resolve hostname to loopback
cat > /etc/hosts <<HOSTS
127.0.0.1 localhost $NODE_HOSTNAME
::1       localhost ip6-localhost ip6-loopback
HOSTS

# 2. Generate slurm.conf
cat > /etc/slurm/slurm.conf <<EOF
ClusterName=magnus-child
SlurmctldHost=localhost
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
SelectType=select/linear
ReturnToService=2
SlurmctldPidFile=/run/slurm/slurmctld.pid
SlurmdPidFile=/run/slurm/slurmd.pid
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmdLogFile=/var/log/slurm/slurmd.log
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd
SlurmdUser=root
SlurmUser=root
AccountingStorageType=accounting_storage/none
JobAcctGatherType=jobacct_gather/none
NodeName=localhost CPUs=$CPUS RealMemory=$MEMORY_MB State=UNKNOWN
PartitionName=batch Nodes=localhost Default=YES MaxTime=INFINITE State=UP
EOF

# 3. Munge
mkdir -p /etc/munge /run/munge /var/log/munge
dd if=/dev/urandom bs=1 count=1024 > /etc/munge/munge.key 2>/dev/null
if id munge &>/dev/null; then
    chown munge:munge /etc/munge/munge.key /run/munge /var/log/munge
fi
chmod 400 /etc/munge/munge.key
munged --force 2>/dev/null || munged

# 4. Start slurmctld (foreground, backgrounded)
slurmctld -D -f /etc/slurm/slurm.conf > /var/log/slurm/slurmctld_stdout.log 2>&1 &
SLURMCTLD_PID=$!
sleep 3

if ! kill -0 "$SLURMCTLD_PID" 2>/dev/null; then
    echo "[SLURM] FATAL: slurmctld died" >&2
    cat /var/log/slurm/slurmctld_stdout.log /var/log/slurm/slurmctld.log 2>/dev/null >&2
    exit 1
fi
echo "[SLURM] slurmctld running (PID=$SLURMCTLD_PID)"

# 5. Start slurmd (foreground, backgrounded)
slurmd -D > /var/log/slurm/slurmd_stdout.log 2>&1 &
SLURMD_PID=$!
sleep 2

if ! kill -0 "$SLURMD_PID" 2>/dev/null; then
    echo "[SLURM] FATAL: slurmd died" >&2
    cat /var/log/slurm/slurmd_stdout.log /var/log/slurm/slurmd.log 2>/dev/null >&2
    exit 1
fi
echo "[SLURM] slurmd running (PID=$SLURMD_PID)"

# 6. Verify
if sinfo --noheader 2>/dev/null | grep -q .; then
    echo "[SLURM] Cluster is UP:"
    sinfo
else
    echo "[SLURM] FATAL: sinfo returned no nodes" >&2
    exit 1
fi
