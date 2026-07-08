#!/usr/bin/env bash
#
# pull-outputs.sh — fetch a DIG run's generated images + labels out of the
# in-cluster MinIO onto this host, ready to rsync to your workstation.
#
# Run this ON THE CLUSTER HOST (the box where the kind cluster lives). MinIO is
# only reachable inside the cluster, so the osmo S3 client needs two things this
# script wires up for you:
#   1. a `minio.osmo` -> 127.0.0.1 entry in /etc/hosts (matches the osmo DATA
#      credential's override_url), and
#   2. a `kubectl port-forward` to svc/minio:9000.
# It then pulls s3://osmo/dig/runs/<RUN>/anomaly/ to a local folder and prints
# the rsync line to copy it down to your laptop.
#
# Usage:
#   scripts/pull-outputs.sh list                  # list available run names
#   scripts/pull-outputs.sh <RUN_NAME> [DEST]     # download a run's anomaly tree
#                                                 # (default DEST: outputs/<RUN>/anomaly)
#   scripts/pull-outputs.sh --png-only <RUN_NAME> # only the generated PNGs (needs mc;
#                                                 # flattens them into one folder)
#   scripts/pull-outputs.sh --keep-forward <RUN>  # leave the port-forward running on exit
#
# Env overrides (defaults match this repo's deployment):
#   NS=osmo  MINIO_SVC=minio  MINIO_PORT=9000  S3_BUCKET=osmo  DIG_PREFIX=dig/runs
#   MINIO_USER=test  MINIO_PASS=testtest  MC_ALIAS=defectsgen
#
set -euo pipefail

NS="${NS:-osmo}"
MINIO_SVC="${MINIO_SVC:-minio}"
MINIO_PORT="${MINIO_PORT:-9000}"
S3_BUCKET="${S3_BUCKET:-osmo}"
DIG_PREFIX="${DIG_PREFIX:-dig/runs}"
MINIO_HOST="minio.${NS}"                 # must match the osmo DATA credential override_url
MINIO_USER="${MINIO_USER:-test}"
MINIO_PASS="${MINIO_PASS:-testtest}"
MC_ALIAS="${MC_ALIAS:-defectsgen}"

PNG_ONLY=0
KEEP_FWD=0
PF_PID=""
STARTED_PF=0

log() { printf '\033[0;36m[pull-outputs]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[0;31m[pull-outputs] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Print the banner comment block (lines 3.. up to the first non-# line).
usage() { awk 'NR>=3 && /^#/ { sub(/^# ?/, ""); print; next } NR>=3 { exit }' "$0"; }

cleanup() {
  if [ "$STARTED_PF" = 1 ] && [ "$KEEP_FWD" != 1 ] && [ -n "$PF_PID" ]; then
    kill "$PF_PID" 2>/dev/null || true
    log "stopped the port-forward we started (pid $PF_PID)"
  fi
}
trap cleanup EXIT

# Readiness check via MinIO's health endpoint, falling back to a raw TCP probe.
minio_ready() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf -o /dev/null "http://127.0.0.1:${MINIO_PORT}/minio/health/ready"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/${MINIO_PORT}") 2>/dev/null
  fi
}

ensure_hosts() {
  if ! grep -qE "^[^#]*[[:space:]]${MINIO_HOST}([[:space:]]|\$)" /etc/hosts; then
    log "adding '127.0.0.1 ${MINIO_HOST}' to /etc/hosts (needs sudo)"
    echo "127.0.0.1 ${MINIO_HOST}" | sudo tee -a /etc/hosts >/dev/null
  fi
}

ensure_forward() {
  if minio_ready; then
    log "MinIO already reachable on 127.0.0.1:${MINIO_PORT} — reusing it"
    return
  fi
  command -v kubectl >/dev/null 2>&1 || die "kubectl not found — run this on the cluster host."
  log "starting: kubectl port-forward -n ${NS} svc/${MINIO_SVC} ${MINIO_PORT}:${MINIO_PORT}"
  kubectl port-forward -n "$NS" "svc/${MINIO_SVC}" "${MINIO_PORT}:${MINIO_PORT}" \
    >/tmp/pull-outputs-pf.log 2>&1 &
  PF_PID=$!
  STARTED_PF=1
  for _ in $(seq 1 30); do
    if minio_ready; then log "port-forward ready"; return; fi
    sleep 1
  done
  die "port-forward did not become ready — see /tmp/pull-outputs-pf.log"
}

print_rsync_hint() {
  local dest="$1" abspath run_tag
  abspath="$(cd "$(dirname "$dest")" && pwd)/$(basename "$dest")"
  run_tag="$(basename "$(dirname "$dest")")-$(basename "$dest")"
  cat >&2 <<EOF

────────────────────────────────────────────────────────────────────────
Copy it to your workstation — run this ON YOUR LAPTOP:

  rsync -avz -e "ssh -i ~/.ssh/<key>.pem" \\
    ubuntu@<EC2_IP>:${abspath}/ ./${run_tag}/
────────────────────────────────────────────────────────────────────────
EOF
}

cmd_list() {
  ensure_hosts
  ensure_forward
  command -v osmo >/dev/null 2>&1 || die "osmo CLI not found."
  log "runs under s3://${S3_BUCKET}/${DIG_PREFIX}/ :"
  osmo data list "s3://${S3_BUCKET}/${DIG_PREFIX}/"
}

cmd_pull() {
  local run="${1:-}" dest="${2:-outputs/${1:-}/anomaly}"
  [ -n "$run" ] || die "no run name given. Try: scripts/pull-outputs.sh list"
  ensure_hosts
  ensure_forward

  local key="${S3_BUCKET}/${DIG_PREFIX}/${run}/anomaly"
  mkdir -p "$dest"

  if [ "$PNG_ONLY" = 1 ]; then
    command -v mc >/dev/null 2>&1 || die "--png-only needs the MinIO client (mc). Install it or drop the flag."
    mc alias set "$MC_ALIAS" "http://127.0.0.1:${MINIO_PORT}" "$MINIO_USER" "$MINIO_PASS" >/dev/null
    log "copying *.png from s3://${key} -> ${dest}/ (flattened)"
    mc find "${MC_ALIAS}/${key}" --name '*.png' --exec "mc cp {} ${dest}/"
  else
    command -v osmo >/dev/null 2>&1 || die "osmo CLI not found."
    log "downloading s3://${key} -> ${dest}/"
    osmo data download "s3://${key}" "${dest}/"
  fi

  log "done -> ${dest}"
  print_rsync_hint "$dest"
}

# ── arg parsing ──────────────────────────────────────────────────────────
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --png-only)     PNG_ONLY=1; shift ;;
    --keep-forward) KEEP_FWD=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do positional+=("$1"); shift; done ;;
    -*) die "unknown flag: $1" ;;
    *)  positional+=("$1"); shift ;;
  esac
done

sub="${positional[0]:-}"
case "$sub" in
  "")   usage; exit 0 ;;
  list) cmd_list ;;
  *)    cmd_pull "${positional[@]}" ;;
esac
