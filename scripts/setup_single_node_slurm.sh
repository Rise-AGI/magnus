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
127.0.0.1 localhost $NODE_HOSTNAME
::1 localhost ip6-localhost ip6-loopback
HOSTS
echo "[SLURM Setup] /etc/hosts:"
cat /etc/hosts

# --- 2. Generate slurm.conf ---
SLURM_CONF=/etc/slurm/slurm.conf
cat > "$SLURM_CONF" <<EOF
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
SlurmctldDebug=debug5
SlurmdDebug=debug5
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd

SlurmdUser=root
SlurmUser=root

AccountingStorageType=accounting_storage/none
JobAcctGatherType=jobacct_gather/none

NodeName=localhost CPUs=$CPUS RealMemory=$MEMORY_MB State=UNKNOWN
PartitionName=default Nodes=localhost Default=YES MaxTime=INFINITE State=UP
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
echo "[SLURM Setup] slurmctld version: $(slurmctld -V 2>&1)"

# Sanity checks before slurmctld
echo "[SLURM Setup] Pre-flight checks:"
echo "  which slurmctld: $(which slurmctld 2>&1)"
echo "  ldd (first 5):"
ldd "$(which slurmctld)" 2>&1 | head -5 | sed 's/^/    /' || true
echo "  ulimit -n (open files): $(ulimit -n 2>&1)"
echo "  ulimit -u (max procs): $(ulimit -u 2>&1)"
echo "  write test /var/log/slurm/:"
echo "x" > /var/log/slurm/_test 2>&1 && echo "    OK" && rm -f /var/log/slurm/_test || echo "    FAILED: $?"
echo "  write test /var/spool/slurmctld/:"
echo "x" > /var/spool/slurmctld/_test 2>&1 && echo "    OK" && rm -f /var/spool/slurmctld/_test || echo "    FAILED: $?"
echo "  env vars (SLURM/PATH):"
echo "    PATH=$PATH"
env | grep -iE 'slurm|munge' 2>/dev/null | sed 's/^/    /' || echo "    (no slurm/munge env)"

# Quick foreground test: run slurmctld for up to 5s, capture exit code
echo "[SLURM Setup] Foreground slurmctld test (timeout 5s)..."
set +e
timeout 5 slurmctld -D -f "$SLURM_CONF" > /var/log/slurm/slurmctld_stdout.log 2>&1
SLURMCTLD_EXIT=$?
set -e
echo "[SLURM Setup] slurmctld exit code: $SLURMCTLD_EXIT (124=timeout/OK, 139=segfault, 137=killed)"
echo "[SLURM Setup] slurmctld stdout/stderr:"
cat /var/log/slurm/slurmctld_stdout.log 2>/dev/null | head -30 || echo "(empty)"
echo "[SLURM Setup] slurmctld.log:"
cat /var/log/slurm/slurmctld.log 2>/dev/null | head -30 || echo "(empty)"

if [ "$SLURMCTLD_EXIT" != "124" ]; then
    echo "[SLURM Setup] FATAL: slurmctld failed to stay alive (exit=$SLURMCTLD_EXIT)" >&2
    echo "[SLURM Setup] Full stdout:" >&2
    cat /var/log/slurm/slurmctld_stdout.log 2>/dev/null >&2 || true
    echo "[SLURM Setup] Full log:" >&2
    cat /var/log/slurm/slurmctld.log 2>/dev/null >&2 || true
    exit 1
fi

# slurmctld survived 5s — now start it for real
echo "[SLURM Setup] Starting slurmctld -D -f $SLURM_CONF (for real)..."
rm -f /var/log/slurm/slurmctld.log /var/log/slurm/slurmctld_stdout.log
SLURM_CONF="$SLURM_CONF" slurmctld -D -f "$SLURM_CONF" > /var/log/slurm/slurmctld_stdout.log 2>&1 &
SLURMCTLD_PID=$!
sleep 2

if kill -0 "$SLURMCTLD_PID" 2>/dev/null; then
    echo "[SLURM Setup] slurmctld is running (PID=$SLURMCTLD_PID)"
else
    echo "[SLURM Setup] ERROR: slurmctld died on real start!" >&2
    cat /var/log/slurm/slurmctld_stdout.log 2>/dev/null >&2 || true
    cat /var/log/slurm/slurmctld.log 2>/dev/null >&2 || true
    exit 1
fi

# Worker
echo "[SLURM Setup] Starting slurmd -D (foreground, backgrounded)..."
slurmd -D > /var/log/slurm/slurmd_stdout.log 2>&1 &
SLURMD_PID=$!
sleep 2

if kill -0 "$SLURMD_PID" 2>/dev/null; then
    echo "[SLURM Setup] slurmd is running (PID=$SLURMD_PID)"
else
    echo "[SLURM Setup] ERROR: slurmd died! Foreground output:" >&2
    cat /var/log/slurm/slurmd_stdout.log 2>/dev/null >&2 || true
    echo "[SLURM Setup] slurmd.log:" >&2
    cat /var/log/slurm/slurmd.log 2>/dev/null >&2 || true
    exit 1
fi

# --- 5. Verify cluster ---
echo "[SLURM Setup] Checking cluster status..."
if sinfo --noheader 2>/dev/null | grep -q .; then
    echo "[SLURM Setup] Cluster is UP:"
    sinfo
    scontrol show node localhost
else
    echo "[SLURM Setup] ERROR: sinfo returned no nodes" >&2
    echo "--- ps aux | grep slurm ---" >&2
    ps aux | grep -E 'slurm|munge' 2>/dev/null || true
    exit 1
fi
