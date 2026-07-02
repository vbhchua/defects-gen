# DIG bring-up — Measured timings & throughput

All numbers measured on this deployment during the 2026-07-01/02 bring-up
(sources: OSMO workflow timestamps, task logs, watcher `du`/`crictl` samples).
Collected for reuse in findings presentations. Narrative context: `PROGRESS.md`.

## Test environment

| Component | Value |
|---|---|
| Host | single machine, 62 GiB RAM, ~1 TB disk (991 G formatted) |
| GPU | 1× NVIDIA RTX PRO 6000 (Blackwell), driver 580.126.09, on `osmo-worker5` |
| Cluster | kind, 1 control-plane + 5 workers (`node_group`: kai-scheduler, data, 2× service, compute) |
| S3 backend | localstack-persist (until 2026-07-02 ~04:08) → **MinIO** on the data node |
| OSMO pool | `default`, GPU capacity 1 |

## End-to-end workflow timings (successful runs)

| Workflow | Wall clock | What it does |
|---|---|---|
| `setup_metal-8` | **~3 min** (04:11 → 04:14:26) | metal checkpoint (13.6 MB from HF) + curated UC2 dataset (3.2 MB from GitHub) → S3 |
| `setup_pretrained-3` | **55.8 min** (3345.7 s; 04:11:38 → 05:07:23) | 80.6 GB pretrained bundle: HF download + assemble + S3 upload |
| `texture_defect_gen_day1_manual_roi-2` | **31.1 min** (1865 s; 05:12:36 → 05:43:41) | Day 1 metal inference: 30 labeled defect images, passthrough checkpoint |

## Phase breakdown — `setup_pretrained-3` (the 80.6 GB bundle)

| Phase | Window | Duration | Rate |
|---|---|---|---|
| HF download (Cosmos-Predict2 + T5 + DINOv2 + …) | 04:11:38 → 04:23:05 | **~11.5 min** | ~117 MB/s from huggingface.co |
| Assemble/copy into `/osmo/data/output` | 04:23:05 → ~04:44 | **~21 min** | ~48 MB/s (large-file copy, observed 13 G / 4.5 min) |
| ctrl S3 upload → MinIO | 04:44:35 → 05:07:16 | **22:40** | **63.6 MB/s avg** (instantaneous 47–305 MB/s) |

osmo-ctrl memory: ~4 MB flat outside upload; brief 7.58 GB peak at upload start
(multipart buffering), steady ~720–770 MB during upload. Well under the 16 Gi limit.

## Phase breakdown — Day 1 manual ROI (metal, num_sdg=30)

| Phase | Window | Duration | Notes |
|---|---|---|---|
| Input download into pod | 05:12 → ~05:34 | **~22 min** | 84.5 GB (80.6 G pretrained + checkpoint + dataset) from MinIO at 65–130 MB/s |
| Model load + inference | ~05:35 → 05:43 | **~8 min** | GPU mem ramp 2.4 G → 21.6 GiB; ~25 it/s, 35 diffusion steps/image, 30 images |
| Output upload | 05:43:30 → 05:43:35 | **~5 s** | 11.7 MB at ~2.8 MB/s |

**Scaling implication:** the ~22-min input download is a fixed cost per run regardless
of `num_sdg`; GPU generation itself averaged ~16 s/image end-to-end (8 min ÷ 30, incl.
model load). Larger `num_sdg` amortizes the fixed cost — e.g. 300 images ≈ 22 min + ~80
min GPU ≈ 1.7 h, not 10× the 31-min run.

## Failure timings (diagnostic signatures)

| Failure | Time-to-failure | Signature |
|---|---|---|
| Missing S3 bucket (post-reboot wipe) | **~3 s** (`setup_metal-4`: 02:36:32 → 02:36:35) | exit 2014 "OSMO Control failure"; logs API → HTTP 500 `NoSuchBucket` |
| OAuth-type HF token | **~1 s** after task start (`setup_metal-5`/`-6`) | exit 1; `hf auth login` → `KeyError: 'accessToken'` |
| Missing S3 env on task pods (2026-07-01) | OOM after ~18 min of retry-spin | exit 2137; ctrl heap leak **~13 MB/s** → cgroup OOM; `osmo_user: Failed to parse response: EOF`; zero bytes ever downloaded |
| localstack multipart corruption (`setup_pretrained-2`) | upload died at **40%**, killed by 1 h exec timeout (3604 s) | `NoSuchUpload` → missing-part `InternalError` from 03:47:44 (11.7 min into upload); progress regressed 40% → 34%; 31.3 G of 80.6 G landed, 51 G orphaned parts |

## localstack vs MinIO (same 80.6 GB upload, same hardware)

| | localstack-persist (`setup_pretrained-2`) | MinIO (`setup_pretrained-3`) |
|---|---|---|
| Upload result | FAILED at 40% after 26+ min (multipart corruption) | **COMPLETED**, 80.6 G in 22:40 |
| Multipart errors | every multi-GB file | **zero** (log grep = 0 hits) |
| Peak observed rate | 135 MB/s (before collapse) | 305 MB/s |
| Restart durability | bucket lost on host reboot (`/tmp` mount) | on-disk at `/var/lib/osmo-minio` (host volume) |

## Misc reference rates

| Operation | Rate / time |
|---|---|
| HF → pod (small repo, metal checkpoint 13.6 MB) | seconds; ctrl → S3 at ~18.5 MB/s |
| GitHub UC2 dataset clone + curate (`setup_metal`) | 3–15 min (varies with GitHub throttling) |
| MinIO → pod input download (Day 1) | 65–130 MB/s |
| AnomalyGen inference (2B, RTX PRO 6000) | ~25 it/s @ 35 steps/image; 21.6 GiB GPU mem |
| Anomaly output tree (30 images + masks + labels) | 14 MB, ~280 objects |
