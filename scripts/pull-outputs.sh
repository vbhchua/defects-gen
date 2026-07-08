#!/usr/bin/env bash
#
# pull-outputs.sh — fetch a DIG run's generated images + labels out of the
# in-cluster MinIO onto this host, ready to rsync to your workstation.
#
# Run this ON THE CLUSTER HOST (the box where the kind cluster lives). MinIO is
# only reachable inside the cluster, so this wires up the `minio.osmo` hosts
# entry and a `kubectl port-forward` to svc/minio:9000, then pulls
# s3://osmo/dig/runs/<RUN>/anomaly/ to a local folder and prints the rsync line
# to copy it down to your laptop.
#
# Prefer an SFTP browser (FileZilla/Cyberduck)? Use scripts/stage-for-sftp.sh.
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

MINIO_TAG="pull-outputs"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib/minio.sh"

MC_ALIAS="${MC_ALIAS:-defectsgen}"
PNG_ONLY=0

# Print the banner comment block (lines 3.. up to the first non-# line).
usage() { awk 'NR>=3 && /^#/ { sub(/^# ?/, ""); print; next } NR>=3 { exit }' "$0"; }

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
  minio_ensure_hosts
  minio_ensure_forward
  command -v osmo >/dev/null 2>&1 || mdie "osmo CLI not found."
  mlog "runs under s3://${S3_BUCKET}/${DIG_PREFIX}/ :"
  osmo data list "s3://${S3_BUCKET}/${DIG_PREFIX}/"
}

cmd_pull() {
  local run="${1:-}" dest="${2:-outputs/${1:-}/anomaly}"
  [ -n "$run" ] || mdie "no run name given. Try: scripts/pull-outputs.sh list"
  minio_ensure_hosts
  minio_ensure_forward

  local key="${S3_BUCKET}/${DIG_PREFIX}/${run}/anomaly"
  mkdir -p "$dest"

  if [ "$PNG_ONLY" = 1 ]; then
    command -v mc >/dev/null 2>&1 || mdie "--png-only needs the MinIO client (mc). Install it or drop the flag."
    mc alias set "$MC_ALIAS" "http://127.0.0.1:${MINIO_PORT}" "$MINIO_USER" "$MINIO_PASS" >/dev/null
    mlog "copying *.png from s3://${key} -> ${dest}/ (flattened)"
    mc find "${MC_ALIAS}/${key}" --name '*.png' --exec "mc cp {} ${dest}/"
  else
    command -v osmo >/dev/null 2>&1 || mdie "osmo CLI not found."
    mlog "downloading s3://${key} -> ${dest}/"
    osmo data download "s3://${key}" "${dest}/"
  fi

  mlog "done -> ${dest}"
  print_rsync_hint "$dest"
}

# ── arg parsing (KEEP_FWD is defined in lib/minio.sh) ────────────────────
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --png-only)     PNG_ONLY=1; shift ;;
    --keep-forward) KEEP_FWD=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do positional+=("$1"); shift; done ;;
    -*) mdie "unknown flag: $1" ;;
    *)  positional+=("$1"); shift ;;
  esac
done

sub="${positional[0]:-}"
case "$sub" in
  "")   usage; exit 0 ;;
  list) cmd_list ;;
  *)    cmd_pull "${positional[@]}" ;;
esac
