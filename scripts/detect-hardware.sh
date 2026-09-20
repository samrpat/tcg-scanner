#!/usr/bin/env bash
# Detect the host and recommend a worker topology (REQ-OPS-003, REQ-OPS-004).
# Pass --json for machine-readable output.
set -uo pipefail

JSON=0
[[ "${1:-}" == "--json" ]] && JSON=1

ARCH="$(uname -m)"
KERNEL="$(uname -s)"
MODEL="unknown"
CORES=1
RAM_MB=0
DISK_FREE=""
GPU="none"
IS_PI=0

if [[ -r /proc/device-tree/model ]]; then
  MODEL="$(tr -d '\0' < /proc/device-tree/model)"
  [[ "$MODEL" == *"Raspberry Pi"* ]] && IS_PI=1
elif [[ "$KERNEL" == "Darwin" ]]; then
  MODEL="$(sysctl -n hw.model 2>/dev/null || echo Mac)"
fi

if [[ "$KERNEL" == "Darwin" ]]; then
  CORES="$(sysctl -n hw.ncpu)"
  RAM_MB="$(( $(sysctl -n hw.memsize) / 1048576 ))"
  DISK_FREE="$(df -h / | awk 'NR==2{print $4}')"
else
  CORES="$(nproc 2>/dev/null || echo 1)"
  RAM_MB="$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
  DISK_FREE="$(df -h / | awk 'NR==2{print $4}')"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  [[ -z "$GPU" ]] && GPU="none"
elif [[ "$KERNEL" == "Darwin" ]]; then
  GPU="Apple Silicon (CoreML)"
fi

# Hailo accelerators show up as a PCIe device on a Pi with an AI HAT.
HAILO="no"
command -v hailortcli >/dev/null 2>&1 && HAILO="yes"

DOCKER="no"
command -v docker >/dev/null 2>&1 && DOCKER="yes"

# --- Recommendation ---------------------------------------------------------
# Recognition and condition work is memory- and compute-hungry. Anything at or under
# 8GB should host the stack and push those tags to a desktop worker.
if (( IS_PI == 1 )) && (( RAM_MB <= 9000 )); then
  TOPOLOGY="pi-with-desktop-worker"
  TAGS="ingest"
  ADVICE="Run the stack here with WORKER_TAGS=ingest, and start a second worker on the desktop with WORKER_TAGS=recognize,condition."
elif (( IS_PI == 1 )); then
  TOPOLOGY="pi-only"
  TAGS="ingest,recognize,condition"
  ADVICE="Enough RAM to run everything here. Add a desktop worker later if scanning throughput disappoints."
elif [[ "$GPU" != "none" ]] && (( RAM_MB > 9000 )); then
  TOPOLOGY="desktop-worker"
  TAGS="recognize,condition"
  ADVICE="GPU present with enough RAM. Best used as the heavy worker: point REDIS_HOST and POSTGRES_HOST at the Pi."
elif [[ "$GPU" != "none" ]]; then
  # 8GB and under is too tight to hold a recognition index plus condition work alongside
  # whatever else the machine is doing.
  TOPOLOGY="client-only"
  TAGS=""
  ADVICE="GPU present but only ${RAM_MB}MB RAM. Marginal as a heavy worker — use this machine as a client and put recognition on a host with more memory."
else
  TOPOLOGY="single-host"
  TAGS="ingest,recognize,condition"
  ADVICE="No Pi and no GPU detected. Run everything on this host."
fi

if (( JSON == 1 )); then
  cat <<JSONEOF
{
  "model": "$MODEL",
  "arch": "$ARCH",
  "kernel": "$KERNEL",
  "cores": $CORES,
  "ram_mb": $RAM_MB,
  "disk_free": "$DISK_FREE",
  "gpu": "$GPU",
  "hailo": "$HAILO",
  "docker": "$DOCKER",
  "is_raspberry_pi": $([[ $IS_PI == 1 ]] && echo true || echo false),
  "topology": "$TOPOLOGY",
  "worker_tags": "$TAGS"
}
JSONEOF
  exit 0
fi

printf '\n  Hardware\n'
printf '  %-14s %s\n' "model"   "$MODEL"
printf '  %-14s %s (%s)\n' "arch" "$ARCH" "$KERNEL"
printf '  %-14s %s\n' "cores"   "$CORES"
printf '  %-14s %s MB\n' "ram"  "$RAM_MB"
printf '  %-14s %s\n' "disk free" "$DISK_FREE"
printf '  %-14s %s\n' "gpu"     "$GPU"
printf '  %-14s %s\n' "hailo"   "$HAILO"
printf '  %-14s %s\n' "docker"  "$DOCKER"

printf '\n  Recommendation\n'
printf '  %-14s %s\n' "topology" "$TOPOLOGY"
printf '  %-14s %s\n' "WORKER_TAGS" "$TAGS"
printf '\n  %s\n\n' "$ADVICE"

if [[ "$DOCKER" == "no" ]]; then
  printf '  Docker is not installed. Install it before running `make up`.\n\n'
fi

# 2,000 cards at four images each needs roughly 12GB, and Docker images add several more.
# BSD df prints "330Mi" where GNU prints "330M", so match both.
case "$DISK_FREE" in
  *Mi|*M|*Ki|*K|*B)
    printf '  WARNING: only %s free. Images need roughly 6MB per card, and the Docker\n' "$DISK_FREE"
    printf '           images for this stack need several GB. Free space before `make up`.\n\n'
    ;;
esac
