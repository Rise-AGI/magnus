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

# Compute node name is decoupled from hostname to avoid SLURM controller/node conflicts
COMPUTE_NODE="cnode1"

echo "[SLURM Setup] hostname=$NODE_HOSTNAME, compute_node=$COMPUTE_NODE, CPUs=$CPUS, Memory=${MEMORY_MB}MB"

# --- 1. Force hostname + compute node to resolve to IPv4 loopback ---
cat > /etc/hosts <<HOSTS
127.0.0.1 localhost $NODE_HOSTNAME $COMPUTE_NODE
::1 localhost ip6-localhost ip6-loopback
HOSTS
echo "[SLURM Setup] /etc/hosts:"
cat /etc/hosts

# --- 2. Generate slurm.conf ---
SLURM_CONF=/etc/slurm/slurm.conf
cat > "$SLURM_CONF" <<EOF
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
SlurmctldDebug=debug5
SlurmdDebug=debug5
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd

SlurmdUser=root
SlurmUser=root

AccountingStorageType=accounting_storage/none
JobAcctGatherType=jobacct_gather/none

NodeName=$COMPUTE_NODE NodeAddr=127.0.0.1 CPUs=$CPUS RealMemory=$MEMORY_MB State=UNKNOWN
PartitionName=default Nodes=$COMPUTE_NODE Default=YES MaxTime=INFINITE State=UP
EOF

echo "[SLURM Setup] slurm.conf written ($(wc -l < "$SLURM_CONF") lines, $(wc -c < "$SLURM_CONF") bytes)"
echo "[SLURM Setup] slurm.conf content:"
cat "$SLURM_CONF"

# --- 3. Generate munge key ---
mkdir -p /etc/munge /run/munge /var/log/munge
dd if=/dev/urandom bs=1 count=1024 > /etc/munge/munge.key 2>/dev/null
if id munge &>/dev/null; then
    chown munge:munge /etc/munge/munge.key
    chown munge:munge /run/munge /var/log/munge
fi
chmod 400 /etc/munge/munge.key
echo "[SLURM Setup] munge key generated"

# --- 4. Start daemons ---
munged --force 2>/dev/null || munged
sleep 1

if munge -n | unmunge > /dev/null 2>&1; then
    echo "[SLURM Setup] munge OK"
else
    echo "[SLURM Setup] WARNING: munge verification failed" >&2
fi

# Controller
echo "[SLURM Setup] Diagnostics before slurmctld:"
echo "  SLURM env vars:"
env | grep -i slurm 2>/dev/null | sed 's/^/    /' || echo "    (none)"
echo "  /etc/slurm/ contents:"
ls -la /etc/slurm/ 2>&1 | sed 's/^/    /'
echo "  Package version:"
dpkg -l 'slurm*' 2>/dev/null | grep '^ii' | sed 's/^/    /' || echo "    (dpkg not available)"
echo "  slurmctld binary:"
which slurmctld 2>&1 | sed 's/^/    /'
slurmctld -V 2>&1 | sed 's/^/    /'

echo "[SLURM Setup] Starting slurmctld -f $SLURM_CONF ..."
SLURM_CONF="$SLURM_CONF" slurmctld -f "$SLURM_CONF" || echo "[SLURM Setup] WARNING: slurmctld exited with code $?" >&2
sleep 2

echo "[SLURM Setup] slurmctld.log after start:"
cat /var/log/slurm/slurmctld.log 2>/dev/null || echo "(empty)"

# Worker (use -N to tell slurmd its compute node name, decoupled from hostname)
echo "[SLURM Setup] Starting slurmd -N $COMPUTE_NODE..."
slurmd -N "$COMPUTE_NODE" || echo "[SLURM Setup] WARNING: slurmd exited with code $?" >&2
sleep 2

echo "[SLURM Setup] slurmd.log after start:"
cat /var/log/slurm/slurmd.log 2>/dev/null || echo "(empty)"

# --- 5. Verify cluster ---
echo "[SLURM Setup] Checking cluster status..."
if sinfo --noheader 2>/dev/null | grep -q .; then
    echo "[SLURM Setup] Cluster is UP:"
    sinfo
    scontrol show node "$COMPUTE_NODE"
else
    echo "[SLURM Setup] ERROR: sinfo returned no nodes" >&2
    echo "--- ps aux | grep slurm ---" >&2
    ps aux | grep -E 'slurm|munge' 2>/dev/null || true
    exit 1
fi
