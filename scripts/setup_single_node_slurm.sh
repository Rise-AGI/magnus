#!/usr/bin/env bash
# scripts/setup_single_node_slurm.sh
#
# Bootstrap a single-node, CPU-only SLURM cluster inside a container.
# Called from child Magnus blueprint entry_command before deploy.py.
#
# Usage: bash setup_single_node_slurm.sh --cpus N --memory-mb M

set -euo pipefail

# --- Parse arguments ---
CPUS=4
MEMORY_MB=8192

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cpus)      CPUS="$2";      shift 2 ;;
        --memory-mb) MEMORY_MB="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

echo "[SLURM Setup] CPUs=$CPUS, Memory=${MEMORY_MB}MB"

# --- 1. Generate slurm.conf ---
cat > /etc/slurm/slurm.conf <<EOF
ClusterName=magnus-child
SlurmctldHost=localhost

ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
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

# Single CPU-only node — no GRES
NodeName=localhost CPUs=$CPUS RealMemory=$MEMORY_MB State=UNKNOWN
PartitionName=default Nodes=localhost Default=YES MaxTime=INFINITE State=UP
EOF

echo "[SLURM Setup] slurm.conf written"

# --- 2. Generate munge key ---
mkdir -p /etc/munge /run/munge /var/log/munge
dd if=/dev/urandom bs=1 count=1024 > /etc/munge/munge.key 2>/dev/null
# In rootless container we are UID 0; munge user may not exist
if id munge &>/dev/null; then
    chown munge:munge /etc/munge/munge.key
    chown munge:munge /run/munge /var/log/munge
fi
chmod 400 /etc/munge/munge.key

echo "[SLURM Setup] munge key generated"

# --- 3. Start daemons ---
# Munge — may need --force if running as root
munged --force 2>/dev/null || munged
sleep 1

# Verify munge works
if munge -n | unmunge > /dev/null 2>&1; then
    echo "[SLURM Setup] munge OK"
else
    echo "[SLURM Setup] WARNING: munge verification failed, continuing anyway" >&2
fi

# SLURM controller
slurmctld
sleep 1

# SLURM worker
slurmd
sleep 1

# --- 4. Verify cluster ---
if sinfo --noheader 2>/dev/null | grep -q .; then
    echo "[SLURM Setup] Cluster is UP:"
    sinfo
else
    echo "[SLURM Setup] ERROR: sinfo returned no nodes" >&2
    exit 1
fi
