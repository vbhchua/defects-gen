# DIG bring-up — Progress Log

Progress standing up the **Defect Image Generation (DIG)** pipeline
(`physical-ai-defect-image-generation` skill) on the local kind OSMO cluster.

- **Started:** 2026-07-01
- **Operator:** testuser (osmo-admin) on the local no-auth OSMO deployment
- **Goal (first run):** Metal surface — Day 1 (manual ROI), passthrough inference + labeling
- **Cluster:** kind `osmo` cluster; compute node `osmo-worker5` has an NVIDIA RTX PRO 6000
  Blackwell GPU (pool `default`, GPU Total Capacity = 1). Node shares the host's 62 GiB RAM.

## Decisions (first-run gate) — saved to agent memory

| Choice | Value |
|---|---|
| Flow | Metal surface — Day 1 (manual ROI), `texture_defect_generation_day1_manual_roi.yaml` |
| `usecase` | `metal_surface` |
| `dig_url_root` | `s3://osmo/dig` (bucket `osmo` on the in-cluster **MinIO** since 2026-07-02; was localstack) |
| Checkpoint | pretrained passthrough (`use_pretrained_checkpoint=true`), `checkpoint_step=10000` |
| Taxonomy | `[["metal_surface","MT_Blowhole"],["MT_Break"],["MT_Crack"],["MT_Fray"],["MT_Uneven"]]` |
| Pool | `default` |
| HF token | `.env` `HF_TOKEN` (HF user `victorchua`) — **user access token (Read)** since 2026-07-02 (OAuth tokens break in-pod `hf auth login`); `hf-token` OSMO cred provisioned |

## Preflight gates

| Gate | Result (final, 2026-07-02) |
|---|---|
| §1 Credentials | ✅ PASS — `hf-token` re-provisioned with a Read **user access token** (whoami role=read, gated probes 200); verified end-to-end by `setup_metal-7`/`-8` |
| §2 Pod template | ✅ PASS — nvoptix + 16 GiB dshm patched via Helm values, verified live |
| §3 URL artifacts | ✅ PASS — `preflight_urls.sh 1 metal_surface` exit 0; all of `models/pretrained` (80.6G), `models/metal_surface`, `datasets/metal_surface/raw` on MinIO |

## ✅ Fixes applied to `osmo-values/service.yaml` (all via `helm upgrade`, ConfigMap-managed)

Three separate POD_TEMPLATE changes, all live (helm revision 3). Full write-ups in `README.md`.

1. **nvoptix + dshm mounts** (`default_user`) — required by DIG GPU work. `osmo config update`
   returns HTTP 409 (ConfigMap mode) → must edit values + redeploy. Verified.
2. **localstack S3 env** (`default_compute`, both containers) — **THE key fix of the day**, see below.

## 🔑 Root cause of all setup failures: workflow pods had no S3 addressing config

**Symptom:** every setup workflow FAILED with exit `2137`; `osmo_user` panicked
`Failed to parse response: EOF` (the ctrl/data-sidecar socket closed). dmesg on the node
showed **per-container OOM kills** (`Memory cgroup out of memory`, `shmem-rss:0kB` → anon heap,
NOT /dev/shm). The `osmo` (ctrl-side data) process grew ~13 MB/s to the container memory
limit (2 Gi metal / 16 Gi pretrained), then OOMed.

**What was actually happening (proven by live inspection):**
- The user container had **zero downloaded bytes** and **no python/hf/git process** — only
  `osmo_exec` waiting. **The download never started.**
- The task was stuck in ctrl's **"Validating WRITE access for URI output: s3://osmo/dig/…"**.
- Workflow task pods (both `osmo-ctrl` and user containers) had **NO S3 env**, unlike the
  service pods which set `AWS_ENDPOINT_URL_S3`, `AWS_S3_FORCE_PATH_STYLE=true`, `AWS_DEFAULT_REGION`.
- Without path-style, boto3 used **virtual-hosted addressing** → `osmo.localstack-s3.osmo:4566`
  (bucket as subdomain) → **DNS failure** → retries against the 24 h S3 timeout → ctrl heap
  grows while spinning → OOM. The "18 minutes running" was pure retry-spin, not downloading.

**Not the causes (ruled out):** the nvoptix/dshm pod-template patch (metal ran fine at 2 Gi
*with* the 16 Gi dshm mount); a transient first-pull race (real cause is deterministic);
data size (the tiny GitHub-clone task leaked identically).

**Fix applied (live; VERIFIED 2026-07-02 by `setup_metal-5`+`-7` — see below):** added to `default_compute` (both
`{{USER_CONTAINER_NAME}}` and `osmo-ctrl`):
```yaml
- {name: AWS_ENDPOINT_URL_S3, value: http://localstack-s3.osmo:4566}
- {name: AWS_S3_FORCE_PATH_STYLE, value: "true"}
- {name: AWS_DEFAULT_REGION, value: us-east-1}
```
`helm upgrade` → revision 3. Confirmed present in live `osmo config show POD_TEMPLATE`
`.default_compute` for both containers. (Since the MinIO migration, helm rev 4, the
endpoint value is `http://minio.osmo:9000` — same mechanism, different URL.)

> Same addressing bug also breaks `osmo data list s3://osmo/dig/…` **from the workstation
> shell** (it hits `osmo.localstack-s3.osmo:4566`, unresolvable). Verify artifacts from
> inside the cluster, or after sorting CLI-side path-style, not from the laptop shell.

## Workflow history

| Workflow | Result | Note |
|---|---|---|
| `setup_pretrained-1` | FAILED (2137) | OOM via S3 retry-spin (2026-07-01) |
| `setup_metal-1` | FAILED (2137) | same |
| `setup_metal-2` | FAILED (2137) | same |
| `setup_metal-3` | FAILED_CANCELED | run at `memory=24Gi`; still hung on S3 validation; cancelled to apply the fix |
| `setup_metal-4` | FAILED (2014, 3 s) | localstack `osmo` bucket wiped by host restart (see below) |
| `setup_metal-5` | FAILED (model exit 1) | **proved the S3 fix** (data task COMPLETED); model task died on OAuth-token `hf auth login` |
| `setup_metal-6` | FAILED (model exit 1) | stale credential — submitted ~1 min before the cred swap (creds snapshot at submit) |
| `setup_metal-7` | ✅ COMPLETED | `models/metal_surface` + `datasets/metal_surface/raw` in S3 (localstack — later re-landed on MinIO) |
| `setup_pretrained-2` | FAILED_EXEC_TIMEOUT (1h) | download/copy fine; localstack-persist corrupted the multipart upload at 40% (see MinIO section) |
| `setup_metal-8` | ✅ COMPLETED (~3 min) | first workflow on MinIO; metal artifacts re-landed |
| `setup_pretrained-3` | ✅ COMPLETED (55.8 min) | 80.6G uploaded at ~63.6 MB/s, zero multipart errors (`exec_timeout=4h`, needed <1h) |
| `texture_defect_gen_day1_manual_roi-1` | (validation reject) | `infer_memory=64Gi` > 62Gi node — never queued |
| `texture_defect_gen_day1_manual_roi-2` | ✅ COMPLETED (~31 min) | **THE GOAL** — 30 labeled metal defect images at `runs/...-51f57bdd/anomaly/` |

## ⚠️ 2026-07-02: host restart WIPED the localstack `osmo` bucket

The host/kind cluster restarted overnight (~02:25). The localstack S3 bucket `osmo`
did **not** survive, despite `PERSIST_S3=1` + the `localstack-s3-pvc` PVC: the node's
backing dir came up empty and `gresau/localstack-persist` started a fresh (bucket-less)
`/persisted-data/s3/store.json`. The chart only creates the bucket via an install-time
hook job, so nothing recreates it on restart.

**Symptoms:** any workflow fails in ~3 s with exit `2014` "OSMO Control failure"
(`setup_metal-4`); `osmo workflow logs` returns HTTP 500 `NoSuchBucket` (the service
can't read/write its own workflow logs either).

**Root cause found later (repo-packaging pass):** the kind config bind-mounted the
localstack data dir to the **host's `/tmp/localstack-s3`** — `/tmp` is cleared on reboot,
so the "empty backing dir" was guaranteed. The repo's kind config now mounts MinIO's data
dir at `/var/lib/osmo-minio` instead.

**Fix:** `kubectl exec -n osmo deploy/localstack-s3 -- awslocal s3 mb s3://osmo`
(recreated 02:40, confirmed persisted into `store.json` — pod restarts are now covered;
a node-filesystem wipe is not). All previously-uploaded DIG artifacts are gone with the
bucket (none existed — no setup workflow had succeeded yet). **Treat exit 2014 +
NoSuchBucket after any host restart as this, and recreate the bucket first.**

## ✅ 2026-07-02: S3 path-style fix VERIFIED — new blocker: HF token type

`setup_metal-5` (after recreating the bucket) proved the POD_TEMPLATE S3 env fix works:

- `metal_surface-data`: **COMPLETED** — 3.2 MB uploaded to
  `s3://osmo/dig/datasets/metal_surface/raw` in ~4 s (80 objects live in the bucket).
  No exit 2137, no OOM, no "Failed to parse response: EOF". **The 2026-07-01 root cause
  is fixed.** `datasets/metal_surface/raw` is now populated.
- `metal_surface-model`: **FAILED exit 1 in ~1 s** — new, unrelated blocker: the in-pod
  `hf auth login` (huggingface_hub 1.17.0) crashes with `KeyError: 'accessToken'` because
  `.env`'s `HF_TOKEN` is an **OAuth token** (`hf_oau...`, `auth.type=oauth`, expires
  2026-07-31). OAuth tokens read gated repos fine (probe 200 — which is why the §1
  preflight passed) but have no `auth.accessToken` in whoami, which `hf auth login` needs.

**Unblock (RESOLVED — see next section):** mint a real HF **user access token** (Read) at
https://huggingface.co/settings/tokens (account `victorchua`), put it in `.env`, then:

```bash
set -a; . ./.env; set +a
osmo credential set hf-token --type GENERIC --payload token="$HF_TOKEN"
# resubmit (data task output persists; harmless to rerun):
osmo workflow submit "$SKILL/assets/configs/setup/setup_metal.yaml" --pool default --set dig_url_root=s3://osmo/dig
osmo workflow submit "$SKILL/assets/configs/setup/setup_pretrained.yaml" --pool default --set dig_url_root=s3://osmo/dig
```

Then continue with NEXT STEPS 3–5 below (steps 1–2 otherwise done/superseded).

## ✅ 2026-07-02 (cont.): HF token rotated — metal setup COMPLETE, pretrained in flight

- User minted a proper HF **user access token** (Read); `.env` updated. `osmo credential set`
  cannot overwrite → **delete then set** (`osmo credential delete hf-token` first).
- **Credentials are snapshotted at SUBMIT time** (proven: `setup_metal-6`, submitted ~1 min
  before the cred swap, still failed with the stale token even though its task ran after).
  Always fix the credential BEFORE submitting.
- `setup_metal-7`: **COMPLETED** — `models/metal_surface` (2 files, 13.6 MB) +
  `datasets/metal_surface/raw` both live in S3. Metal Day 1 artifacts: 2 of 3 done.
- `setup_pretrained-2`: RUNNING, healthy (survived the stale token — its script uses the
  `HF_TOKEN` env var directly, no `hf auth login`; OAuth tokens can still download).
  HF download done in ~12 min; ~71G copy → then ctrl S3 upload is the long pole (~1h).
  ctrl memory flat at 3.9 MB (S3 fix holding). Task pods run in namespace **`default`**
  (hex names); ctrl container is distroless — use pod spec + `crictl` on the kind node,
  not `kubectl exec`, for health checks.

## 🔄 2026-07-02 (cont.): localstack REPLACED with MinIO — pretrained upload was corrupted

`setup_pretrained-2` FAILED_EXEC_TIMEOUT (1h limit). Download (80.6G in ~12 min) and copy
were flawless; the ctrl S3 **upload to localstack corrupted at 40%**:

- `CompleteMultipartUpload` → `NoSuchUpload`, then `InternalError: [Errno 2] No such file
  or directory: /persisted-data/s3/assets/osmo/multiparts/<id>/part-NNN` on every multi-GB
  file; progress regressed 40%→34% in retry loops until the exec timeout.
- **Root cause (from localstack logs + `localstack_persist` source):** the persist thread
  runs every 10 s and takes a global write lock; under heavy concurrent upload the stalls
  exceed the client timeout → boto3 *retries* `CompleteMultipartUpload`; the first attempt
  already succeeded and `rm -rf`'d the parts dir → the retry gets `NoSuchUpload`, and
  concurrent completes race into missing-part `InternalError`. Not disk (851G free), not
  a localstack restart, not fixable by timeout alone.

**Fix: migrated the S3 backend to MinIO** (helm rev 4, per user's "use the 1TB disk"):

- `osmo-values/minio.yaml` (plain `kubectl apply`): MinIO on the `data` node, hostPath
  `/var/lib/minio` (host 1 TB volume), service `minio.osmo:9000`, creds `test`/`testtest`
  (MinIO needs ≥8-char secret).
- `service.yaml`: `localstackS3.enabled: false`; all `override_url`/`AWS_ENDPOINT_URL_S3`
  → `http://minio.osmo:9000`; `access_key` → `testtest`. Backup of the old values at
  `osmo-values/service.yaml.bak-localstack`.
- CLI DATA credential `osmo` recreated (delete + set) with the MinIO override_url and
  `addressing_style=path` (also fixes workstation-shell `osmo data list` addressing).
- Bucket `osmo` recreated fresh in MinIO (`mc mb`) — localstack contents discarded, so
  **metal artifacts must re-land**: `setup_metal-8` (smoke test) + `setup_pretrained-3`
  (`exec_timeout=4h`) submitted 04:10; watcher on both.
- Verify artifacts in-cluster: `kubectl exec -n osmo deploy/minio -- du -sh /data/osmo/dig`.
- MinIO survives pod AND host restarts (data on disk); the bucket-wipe gotcha is obsolete.

## 🎉 2026-07-02 (cont.): MinIO validated — ALL setup artifacts landed; Day 1 flow RUNNING

- `setup_metal-8`: COMPLETED in ~3 min (smoke test of the MinIO stack).
- `setup_pretrained-3`: **COMPLETED in 55.8 min** — 80.6G uploaded at ~63.6 MB/s avg,
  **zero multipart errors** (grep for NoSuchUpload/InternalError/Retrying = 0 hits).
  The MinIO migration fully resolved the localstack multipart corruption.
- MinIO console pinned to :9001 (`minio.yaml` updated + applied). Dashboard:
  `kubectl port-forward -n osmo svc/minio 9001:9001` → http://localhost:9001 (test/testtest).
- Workstation CLI data ops fixed: `/etc/hosts` maps `minio.osmo` → 127.0.0.1 + a
  background `kubectl port-forward svc/minio 9000:9000`; `osmo data list s3://osmo/dig/`
  now works from the shell (credential has `addressing_style=path`).
- **§3 preflight: PASS** (`preflight_urls.sh 1 metal_surface` exit 0 — all 3 artifacts).
- **Day 1 submitted:** `texture_defect_gen_day1_manual_roi-2` (STAMP 51f57bdd,
  passthrough, checkpoint_step=10000, 5 metal defects, num_sdg=30). Gotcha: default
  `infer_memory=64Gi` fails validation on the 62Gi node → **`--set infer_memory=48Gi`**.
  Task `infer-all-defects` RUNNING; watcher on it (3 h budget; expect a long quiet
  input-download phase — the pod pulls the 80.6G pretrained tree from MinIO first).
- Output will land at `s3://osmo/dig/runs/<name>/anomaly/`.

## ✅ 2026-07-02: GOAL REACHED — metal Day 1 flow COMPLETED, outputs retrieved

`texture_defect_gen_day1_manual_roi-2` (run name `...-51f57bdd`) **COMPLETED** in ~31 min:
~22 min pulling the 80.6G pretrained tree from MinIO into the pod (65-130 MB/s), ~8 min
AnomalyGen inference on the RTX PRO 6000 (21.6 GiB GPU mem, ~25 it/s), 12M output upload.
Zero S3 errors. Output: 30 generated defect images (6 per defect × 5 metal defect types)
with masks, crops, annotations, `SDG_result.csv`, and DAFT v3 labeling artifacts at
`s3://osmo/dig/runs/texture_defect_gen_day1_manual_roi-51f57bdd/anomaly/`.

Retrieved locally to `outputs/texture_defect_gen_day1_manual_roi-51f57bdd/anomaly/`;
preview grid at `outputs/.../preview/index.html` (10 samples: original → mask → recon).

**Submit gotcha for this cluster:** YAML defaults `infer_memory`/`train_memory` = 64Gi
exceed the 62Gi node → always `--set infer_memory=48Gi` (+ `train_memory=48Gi` if
finetuning). Saved to agent memory.

## ▶️ STATUS: bring-up COMPLETE (2026-07-02). Possible follow-ons

The first-run goal (metal Day 1 manual ROI, passthrough) is **done and verified** — all
preflight gates PASS, all setup artifacts persisted on MinIO, outputs retrieved locally.
Nothing is pending. Options for the next session (in rough order of increasing effort):

1. **Bigger metal dataset** — rerun the Day 1 submit with a fresh `$STAMP` and larger
   `num_sdg` (30 images took ~8 min of GPU time; the fixed ~22-min input download
   dominates short runs, so batching bigger `num_sdg` is cheap per-image).
2. **Glass (UC3) Day 1** — needs the Roboflow `mobile_screen.zip` browser download +
   upload first (see skill `references/setup.md` §"Glass case"), then `setup_glass.yaml`,
   then the same Day 1 flow with `usecase=glass checkpoint_step=9000`.
3. **Finetune from scratch on metal** — `finetune.yaml` or Day 1 with
   `use_pretrained_checkpoint=false`; remember `--set train_memory=48Gi` and that
   training holds the single GPU for its duration.
4. **PCBA Day 0** — requires an image-edit endpoint decision first: the pool has 1 GPU
   total, so a local Qwen-Image-Edit NIM cannot run alongside a DIG job (Option A
   external URL, or add GPU capacity).

Canonical submit block (metal Day 1, all cluster gotchas baked in):

```bash
cd /home/ubuntu/defects-gen && set -a; . ./.env; set +a
SKILL=/home/ubuntu/.claude/skills/physical-ai-defect-image-generation
DIG_URL_ROOT=s3://osmo/dig bash "$SKILL/scripts/preflight_urls.sh" 1 metal_surface
STAMP=$(cat /proc/sys/kernel/random/uuid | cut -c1-8)
osmo workflow submit "$SKILL/assets/configs/texture_defect_generation_day1_manual_roi.yaml" \
  --pool default --set name=texture_defect_gen_day1_manual_roi-$STAMP \
    dig_url_root=s3://osmo/dig usecase=metal_surface checkpoint_step=10000 \
    use_pretrained_checkpoint=true infer_memory=48Gi \
    'anomaly_types_json=[["metal_surface","MT_Blowhole"],["metal_surface","MT_Break"],["metal_surface","MT_Crack"],["metal_surface","MT_Fray"],["metal_surface","MT_Uneven"]]' \
    num_sdg=30
# outputs: osmo data download s3://osmo/dig/runs/texture_defect_gen_day1_manual_roi-$STAMP/anomaly ./outputs/.../
```

## Environment notes / watch-items

- **1 GPU pool** — one GPU-bound job at a time; setup is download-only (CPU/network).
- **Node sizing** — 62Gi node: always `--set infer_memory=48Gi` (and `train_memory=48Gi`
  when finetuning); the YAML 64Gi defaults fail submit validation.
- **Monitoring:** long jobs → background watcher; `osmo workflow logs` has no `-f` (poll it).
  Task pods run in namespace **`default`** with hex names; osmo-ctrl is distroless
  (no `kubectl exec`) — inspect via pod spec + `crictl` on the kind node.
- **S3 = MinIO** (`minio.osmo:9000`, `test`/`testtest`, path-style). Dashboard:
  `kubectl port-forward -n osmo svc/minio 9001:9001` → http://localhost:9001.
  Workstation CLI data ops need the `minio.osmo` → 127.0.0.1 `/etc/hosts` entry **and**
  a `kubectl port-forward -n osmo svc/minio 9000:9000` running (port-forwards do not
  survive reboots — restart them per session). In-cluster verify:
  `kubectl exec -n osmo deploy/minio -- du -sh /data/osmo/dig`.
- **`osmo` DATA credential** (profile `s3://osmo`): MinIO creds + `override_url` +
  `addressing_style=path`. `osmo credential set` cannot overwrite — delete first.
- **Credentials snapshot at submit time** — fix creds BEFORE submitting, never after.
- **HF token must be a user access token** (Read); OAuth tokens (`hf_oau...`) pass repo
  probes but crash in-pod `hf auth login` (`KeyError: 'accessToken'`).
- The localstack bucket-wipe gotcha (exit 2014 + NoSuchBucket after host restart) is
  **obsolete** — MinIO persists to disk. If exit 2014 ever reappears, check
  `kubectl exec -n osmo deploy/minio -- ls /data/` first.
- No background watchers active; all workflows terminal.
