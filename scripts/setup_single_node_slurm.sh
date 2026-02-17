#!/usr/bin/env bash
# scripts/setup_single_node_slurm.sh
#
# Bootstrap a single-node, CPU-only SLURM cluster inside a container.
# Called from child Magnus blueprint entry_command before deploy.py.
#
# Usage: bash setup_single_node_slurm.sh --cpus N --memory-mb M [--hostname NAME]

set -euo pipefail

# --- Parse arguments ---
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

if [[ -z "$NODE_HOSTNAME" ]]; then
    NODE_HOSTNAME=$(hostname -s)
fi

echo "[SLURM Setup] CPUs=$CPUS, Memory=${MEMORY_MB}MB"

# --- 1. Force hostname to resolve to IPv4 loopback inside container ---
# sed -i may silently fail on overlayfs bind-mounts; overwrite entirely.
# Remove ::1 hostname entries to prevent slurmctld from binding to IPv6.
cat > /etc/hosts <<HOSTS
127.0.0.1 localhost $NODE_HOSTNAME
::1 localhost ip6-localhost ip6-loopback
HOSTS
echo "[SLURM Setup] Rewrote /etc/hosts ($NODE_HOSTNAME -> 127.0.0.1)"

# --- 2. Generate slurm.conf ---
cat > /etc/slurm/slurm.conf <<EOF
ClusterName=magnus-child
SlurmctldHost=$NODE_HOSTNAME(127.0.0.1)

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
NodeName=$NODE_HOSTNAME NodeAddr=127.0.0.1 CPUs=$CPUS RealMemory=$MEMORY_MB State=UNKNOWN
PartitionName=default Nodes=$NODE_HOSTNAME Default=YES MaxTime=INFINITE State=UP
EOF

echo "[SLURM Setup] slurm.conf written"

# --- 3. Generate munge key ---
mkdir -p /etc/munge /run/munge /var/log/munge
dd if=/dev/urandom bs=1 count=1024 > /etc/munge/munge.key 2>/dev/null
# In rootless container we are UID 0; munge user may not exist
if id munge &>/dev/null; then
    chown munge:munge /etc/munge/munge.key
    chown munge:munge /run/munge /var/log/munge
fi
chmod 400 /etc/munge/munge.key

echo "[SLURM Setup] munge key generated"

# --- 4. Start daemons ---
# Munge — may need --force if running as root
munged --force 2>/dev/null || munged
sleep 1

# Verify munge works
if munge -n | unmunge > /dev/null 2>&1; then
    echo "[SLURM Setup] munge OK"
else
    echo "[SLURM Setup] WARNING: munge verification failed, continuing anyway" >&2
fi

# SLURM controller (verbose for diagnostics)
echo "[SLURM Setup] Pre-flight checks:"
echo "  hostname=$(hostname -s)"
echo "  getent hosts $NODE_HOSTNAME: $(getent hosts $NODE_HOSTNAME 2>&1 || echo FAILED)"
echo "  /etc/hosts:"
cat /etc/hosts | sed 's/^/    /'
echo "  spool dirs:"
ls -ld /var/spool/slurmctld /var/spool/slurmd /run/slurm 2>&1 | sed 's/^/    /'
echo "  select plugins:"
find /usr/lib -name 'select_*' 2>/dev/null | sed 's/^/    /' || echo "    (none found)"
echo "  slurm.conf path check:"
ls -la /etc/slurm/slurm.conf 2>&1 | sed 's/^/    /'
ls -la /etc/slurm-llnl/ 2>&1 | sed 's/^/    /'

slurmctld -vvvv
sleep 1

# SLURM worker
slurmd
sleep 1

# --- 5. Verify cluster ---
if sinfo --noheader 2>/dev/null | grep -q .; then
    echo "[SLURM Setup] Cluster is UP:"
    sinfo
else
    echo "[SLURM Setup] ERROR: sinfo returned no nodes" >&2
    echo "--- slurmctld.log ---" >&2
    cat /var/log/slurm/slurmctld.log 2>/dev/null || echo "(empty)" >&2
    echo "--- slurmd.log ---" >&2
    cat /var/log/slurm/slurmd.log 2>/dev/null || echo "(empty)" >&2
    echo "--- ps aux | grep slurm ---" >&2
    ps aux | grep -E 'slurm|munge' 2>/dev/null || true >&2
    echo "--- slurm.conf ---" >&2
    cat /etc/slurm/slurm.conf >&2
    exit 1
fi
