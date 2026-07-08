#!/usr/bin/env bash
#
# stage-for-sftp.sh — extract one or more DIG runs from the in-cluster MinIO
# into a browsable folder on this host, then print the SFTP connection details
# to open that folder in an SFTP client (FileZilla, Cyberduck, WinSCP, …).
#
# Why a separate script: an SFTP browser talks to the HOST filesystem over SSH,
# not to MinIO (and MinIO's on-disk objects are xl.meta/part.1 pairs, not plain
# PNGs). So the images must first be pulled out of S3 into a real folder — that
# is what this does — after which you point FileZilla at that folder.
#
# Run this ON THE CLUSTER HOST (the box where the kind cluster lives).
#
# Usage:
#   scripts/stage-for-sftp.sh list                    # list available run names
#   scripts/stage-for-sftp.sh <RUN_NAME> [RUN_NAME…]  # stage one or more runs
#
# Staged to <DEST_ROOT>/<RUN>/anomaly (default DEST_ROOT=outputs, git-ignored).
#
# Env overrides (defaults match this repo's deployment):
#   NS=osmo  MINIO_SVC=minio  MINIO_PORT=9000  S3_BUCKET=osmo  DIG_PREFIX=dig/runs
#   DEST_ROOT=outputs  SSH_USER=<whoami>  SSH_PORT=22
#
set -euo pipefail

MINIO_TAG="stage-for-sftp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/lib/minio.sh"

DEST_ROOT="${DEST_ROOT:-outputs}"
SSH_USER="${SSH_USER:-$(whoami)}"
SSH_PORT="${SSH_PORT:-22}"

# Print the banner comment block (lines 3.. up to the first non-# line).
usage() { awk 'NR>=3 && /^#/ { sub(/^# ?/, ""); print; next } NR>=3 { exit }' "$0"; }

# Best-effort EC2 public IP (IMDSv2, then IMDSv1); placeholder if unavailable.
detect_ip() {
  local token ip=""
  if command -v curl >/dev/null 2>&1; then
    token="$(curl -s -m 1 -X PUT 'http://169.254.169.254/latest/api/token' \
      -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
    if [ -n "$token" ]; then
      ip="$(curl -s -m 1 -H "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
    fi
    [ -n "$ip" ] || ip="$(curl -s -m 1 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
  printf '%s' "${ip:-<EC2_IP>}"
}

stage_one() {
  local run="$1" dest="${DEST_ROOT}/${1}/anomaly"
  command -v osmo >/dev/null 2>&1 || mdie "osmo CLI not found."
  mkdir -p "$dest"
  mlog "downloading s3://${S3_BUCKET}/${DIG_PREFIX}/${run}/anomaly -> ${dest}/"
  osmo data download "s3://${S3_BUCKET}/${DIG_PREFIX}/${run}/anomaly" "${dest}/"
}

print_sftp_hint() {
  local root_abs ip
  root_abs="$(cd "$DEST_ROOT" && pwd)"
  ip="$(detect_ip)"
  cat >&2 <<EOF

────────────────────────────────────────────────────────────────────────
Open in FileZilla (or any SFTP client) — connect to the HOST, not MinIO:

  Protocol : SFTP - SSH File Transfer Protocol
  Host     : ${ip}
  Port     : ${SSH_PORT}
  Logon    : Key file   (point it at your .pem; FileZilla offers to convert it)
  User     : ${SSH_USER}

  Then browse to:
    ${root_abs}

  Quick-connect URL:  sftp://${SSH_USER}@${ip}
────────────────────────────────────────────────────────────────────────
Tip: drag the run folder(s) from the remote pane to your laptop. Re-run this
script any time to add more runs under the same folder.
EOF
}

cmd_list() {
  minio_ensure_hosts
  minio_ensure_forward
  command -v osmo >/dev/null 2>&1 || mdie "osmo CLI not found."
  mlog "runs under s3://${S3_BUCKET}/${DIG_PREFIX}/ :"
  osmo data list "s3://${S3_BUCKET}/${DIG_PREFIX}/"
}

cmd_stage() {
  [ "$#" -ge 1 ] || mdie "no run name given. Try: scripts/stage-for-sftp.sh list"
  minio_ensure_hosts
  minio_ensure_forward
  local run
  for run in "$@"; do
    stage_one "$run"
  done
  mlog "staged $# run(s) under ${DEST_ROOT}/"
  print_sftp_hint
}

# ── arg parsing ──────────────────────────────────────────────────────────
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do positional+=("$1"); shift; done ;;
    -*) mdie "unknown flag: $1" ;;
    *)  positional+=("$1"); shift ;;
  esac
done

sub="${positional[0]:-}"
case "$sub" in
  "")   usage; exit 0 ;;
  list) cmd_list ;;
  *)    cmd_stage "${positional[@]}" ;;
esac
