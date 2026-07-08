# shellcheck shell=bash
#
# minio.sh — shared MinIO wiring for the output-retrieval scripts. SOURCE this
# (`. "$SCRIPT_DIR/lib/minio.sh"`); it is not meant to be executed directly.
#
# Provides the config vars, logging helpers, and the two things osmo's S3 client
# needs to reach the in-cluster MinIO from the host shell:
#   minio_ensure_hosts    — add the `minio.osmo` -> 127.0.0.1 /etc/hosts entry
#   minio_ensure_forward  — ensure a `kubectl port-forward` to svc/minio:9000
#                           (reuses one already up; starts one otherwise)
# It registers an EXIT trap that stops only a port-forward it started, unless
# KEEP_FWD=1.
#
# Callers may set MINIO_TAG before sourcing to label log lines.

NS="${NS:-osmo}"
MINIO_SVC="${MINIO_SVC:-minio}"
MINIO_PORT="${MINIO_PORT:-9000}"
S3_BUCKET="${S3_BUCKET:-osmo}"
DIG_PREFIX="${DIG_PREFIX:-dig/runs}"
MINIO_HOST="minio.${NS}"                 # must match the osmo DATA credential override_url
MINIO_USER="${MINIO_USER:-test}"
MINIO_PASS="${MINIO_PASS:-testtest}"
KEEP_FWD="${KEEP_FWD:-0}"

_MINIO_PF_PID=""
_MINIO_STARTED_PF=0

mlog() { printf '\033[0;36m[%s]\033[0m %s\n' "${MINIO_TAG:-minio}" "$*" >&2; }
mdie() { printf '\033[0;31m[%s] ERROR:\033[0m %s\n' "${MINIO_TAG:-minio}" "$*" >&2; exit 1; }

_minio_cleanup() {
  if [ "$_MINIO_STARTED_PF" = 1 ] && [ "$KEEP_FWD" != 1 ] && [ -n "$_MINIO_PF_PID" ]; then
    kill "$_MINIO_PF_PID" 2>/dev/null || true
    mlog "stopped the port-forward we started (pid $_MINIO_PF_PID)"
  fi
}
trap _minio_cleanup EXIT

# Readiness via MinIO's health endpoint, falling back to a raw TCP probe.
minio_ready() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf -o /dev/null "http://127.0.0.1:${MINIO_PORT}/minio/health/ready"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/${MINIO_PORT}") 2>/dev/null
  fi
}

minio_ensure_hosts() {
  if ! grep -qE "^[^#]*[[:space:]]${MINIO_HOST}([[:space:]]|\$)" /etc/hosts; then
    mlog "adding '127.0.0.1 ${MINIO_HOST}' to /etc/hosts (needs sudo)"
    echo "127.0.0.1 ${MINIO_HOST}" | sudo tee -a /etc/hosts >/dev/null
  fi
}

minio_ensure_forward() {
  if minio_ready; then
    mlog "MinIO already reachable on 127.0.0.1:${MINIO_PORT} — reusing it"
    return
  fi
  command -v kubectl >/dev/null 2>&1 || mdie "kubectl not found — run this on the cluster host."
  mlog "starting: kubectl port-forward -n ${NS} svc/${MINIO_SVC} ${MINIO_PORT}:${MINIO_PORT}"
  kubectl port-forward -n "$NS" "svc/${MINIO_SVC}" "${MINIO_PORT}:${MINIO_PORT}" \
    >/tmp/minio-forward.log 2>&1 &
  _MINIO_PF_PID=$!
  _MINIO_STARTED_PF=1
  for _ in $(seq 1 30); do
    if minio_ready; then mlog "port-forward ready"; return; fi
    sleep 1
  done
  mdie "port-forward did not become ready — see /tmp/minio-forward.log"
}
