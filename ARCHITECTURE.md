# Architecture

How this workspace turns a single GPU box into a self-contained **Defect Image
Generation (DIG)** factory: a local [kind](https://kind.sigs.k8s.io/) Kubernetes
cluster running NVIDIA **OSMO** as the workflow orchestrator, **MinIO** as the
S3 data plane, and NVIDIA's **Cosmos AnomalyGen** model stack doing the actual
image generation and labeling.

Companion docs: [`README.md`](README.md) (deployment how-to),
[`PROGRESS.md`](PROGRESS.md) (bring-up narrative), [`TIMINGS.md`](TIMINGS.md)
(measured performance), [`osmo-values/README.md`](osmo-values/README.md)
(Helm values gotchas).

## Layer map

```
┌─────────────────────────────────────────────────────────────────────┐
│ DIG workflows (physical-ai-defect-image-generation skill)           │
│   setup_* (asset staging) · texture_defect_gen_day1_manual_roi …    │
├─────────────────────────────────────────────────────────────────────┤
│ OSMO platform (namespace `osmo`)                                    │
│   Envoy gateway · core service · UI · postgres · redis              │
│   backend-operator (listener + worker) · pool `default`             │
├─────────────────────────────────────────────────────────────────────┤
│ Data plane: MinIO (`minio.osmo:9000`, bucket `osmo`, path-style)    │
├─────────────────────────────────────────────────────────────────────┤
│ kind cluster: control-plane + 5 workers, node_group labels          │
│   kai-scheduler · data · 2× service · compute (GPU)                 │
├─────────────────────────────────────────────────────────────────────┤
│ Host: single box, 62 GiB RAM, ~1 TB disk,                           │
│   1× NVIDIA RTX PRO 6000 (Blackwell)                                │
└─────────────────────────────────────────────────────────────────────┘
```

## Cluster layer (kind)

`kind-osmo-cluster-config.yaml` defines one control-plane and five workers.
Workers carry `node_group` labels that OSMO's Helm values use as
`nodeSelector`s, so every component lands on a predictable node:

| node_group | Purpose |
|---|---|
| `kai-scheduler` | KAI scheduler components (GPU-aware batch scheduling) |
| `data` | Stateful data services — MinIO's hostPath volume lives here |
| `service` (×2) | OSMO core service, UI, gateway, postgres, redis; one maps host port 80 → NodePort 30080 |
| `compute` | GPU workloads. The only node with `nvidia.com/gpu.deploy.operands` enabled; hosts the RTX PRO 6000 |

Workflow **task pods** run in namespace `default` (hex-named pods), while the
OSMO platform itself lives in namespace `osmo`. GPU capacity of the `default`
OSMO pool is 1 — exactly one GPU job at a time; setup workflows are
download-only (CPU/network) and can run alongside.

## OSMO platform layer

Two Helm charts (values in `osmo-values/`):

- **`osmo/service`** — the core OSMO service (workflow API, scheduling,
  data-URL brokering), web UI, Envoy gateway, postgres (workflow state), redis,
  and (originally) localstack-s3.
- **`osmo/backend-operator`** — the backend **listener** (subscribes to the
  service for work) and **worker** (creates/watches the K8s Jobs for workflow
  tasks). Authenticates with `loginMethod: token` against OSMO's native JWT
  endpoint — see `CLAUDE.md` for why `password`/`dev` both crash here.

Deployment-specific properties that shape everything above:

- **No-auth**: `oauth2Proxy` and `authz` are disabled; the Envoy gateway
  injects a fixed identity (`testuser` / `osmo-admin` / pool `default`) into
  every request. There is no IdP and no OAuth2 endpoints.
- **Pod template**: workflow pods are stamped from OSMO's `POD_TEMPLATE`
  config. Two patches were required for DIG GPU work: the
  `/usr/share/nvidia/nvoptix.bin` host mount and a 16 GiB `/dev/shm`
  (torchrun shared memory). A third patch injects S3 addressing env
  (`AWS_ENDPOINT_URL_S3`, `AWS_S3_FORCE_PATH_STYLE=true`,
  `AWS_DEFAULT_REGION`) into **both** the user container and the `osmo-ctrl`
  sidecar — without it boto3 falls back to virtual-hosted addressing, DNS
  fails, and the ctrl sidecar retry-spins to OOM (the root cause of every
  2026-07-01 failure; full write-up in `PROGRESS.md`).
- **Data sidecar model**: every task pod pairs the user container with an
  `osmo-ctrl` sidecar that materializes URL `inputs:` into `/osmo/data/input/N`
  before the task starts and uploads `/osmo/data/output` to the `outputs:` URL
  after it exits. Tasks never talk to S3 directly — they see plain
  directories.

## Data plane (MinIO)

MinIO replaced the chart's localstack-s3 on 2026-07-02 after
localstack-persist corrupted concurrent multipart uploads (details in
`PROGRESS.md`/`TIMINGS.md`). It runs standalone
(`kubectl apply -f osmo-values/minio.yaml`) on the `data` node with a hostPath
at `/var/lib/minio` (survives pod *and* host restarts), service
`minio.osmo:9000`, bucket `osmo`, path-style addressing.

Everything DIG-related lives under one prefix, `s3://osmo/dig`
(the `dig_url_root`):

```
s3://osmo/dig/
├── models/
│   ├── pretrained/            # 80.6 GB shared model bundle (see below)
│   └── metal_surface/         # Cosmos-AnomalyGen-Metal-2B checkpoint (iter_10000, ~14 MB)
├── datasets/
│   └── metal_surface/raw/     # UC2 raw data: clean images + per-defect masks + defect_spec.jsonl
└── runs/
    └── <workflow-name>-<stamp>/anomaly/    # per-run generated output
```

Workstation access (for `osmo data list/download`, `scripts/pull-outputs.sh`)
needs an `/etc/hosts` entry `127.0.0.1 minio.osmo` plus a live
`kubectl port-forward -n osmo svc/minio 9000:9000` — in-cluster pods reach
MinIO directly.

## Workflow layer (DIG)

Workflows come from the `physical-ai-defect-image-generation` skill and are
submitted with the `osmo` CLI. Two categories:

**Setup workflows** (CPU-only, run once; idempotent):

- `setup_pretrained.yaml` → assembles `models/pretrained` (~80.6 GB): pulls
  Cosmos-Predict2 + T5 + DINOv2 from Hugging Face inside the
  `paidf-anomalygen` container, copies the container-baked checkpoints
  (NVDINOV2, SAM2, Qwen3-VL), and adds C-RADIOv3-B.
- `setup_metal.yaml` → two parallel groups: the finetuned
  `nvidia/Cosmos-AnomalyGen-Metal-2B` checkpoint from HF into
  `models/metal_surface`, and the curated UC2 magnetic-tile defect dataset
  (public GitHub, `abin24/Magnetic-tile-defect-datasets.`) into
  `datasets/metal_surface/raw`.

**Generation workflow** — `texture_defect_generation_day1_manual_roi.yaml`,
the flow used for metal surface (no CAD/USD path exists for metal, so ROIs are
"manual": pre-captured clean images + defect masks shipped in the raw
dataset). Single Jinja-templated spec with two groups:

1. `finetune-job` — **omitted** here: we run passthrough
   (`use_pretrained_checkpoint=true`), reading the shipped checkpoint
   directly. When enabled it renders the per-usecase cookbook
   (`assets/cookbooks/metal_surface/ag_config.yaml`) in-pod and trains with
   torchrun.
2. `anomaly-infer` / task `infer-all-defects` — the GPU task. Inside the
   `nvcr.io/nvidia/paidf-anomalygen:1.0.0` image it chains:

   | Stage | What it does |
   |---|---|
   | pod-template preflight | fails fast on missing nvoptix / small `/dev/shm` |
   | checkpoint staging | symlinks the pretrained tree into `/workspace/paidf-anomalygen/checkpoints`, wraps the finetuned `iter_*.pt` into the canonical layout, `validate_checkpoint.py` |
   | `prep_testcase.sh` (AMP) | **Anomaly Mask Placement** — samples defect submasks, places them onto clean images (crop/paste, blend, morph ops per `defect_spec.jsonl`), auto-scales seeds so total entries = `num_sdg`, emits `inference.jsonl` |
   | `validate_jsonl.py` | cross-checks requested TEXTURE+ANOMALY pairs against the checkpoint's taxonomy |
   | `run_sdg.sh` | the diffusion run: Cosmos-AnomalyGen generates each defect image (35 denoising steps, guidance 7.0, 512×512 training resolution) |
   | `verify_output.sh` | completeness check of generated tree vs. JSONL |
   | DAFT conversion | `convert_to_daft_format.py` renders DAFT v3 labels (per-image JSON with scenario/defect metadata + raw rgb/mask pairs) |

   Knobs that matter on this cluster: `num_sdg` (total generated images across
   all defect types), `checkpoint_step=10000`, `infer_memory=48Gi` (the 64Gi
   YAML default exceeds the 62Gi node and fails submit validation).

**Output contract** (`runs/<name>/anomaly/`, ~280 objects for `num_sdg=30`):

```
anomaly/
├── inference/
│   ├── original_image/       # clean input crops
│   ├── original_mask/        # placed defect masks (AMP output)
│   ├── cropped_image/ cropped_mask/
│   ├── reconstructed_image/  # ★ the generated defect images
│   ├── annotated_image/      # visualization overlays
│   └── timing_summary.json   # per-rank setup/model-init/generation timings
└── inference_daft_v3/
    ├── raw/{rgb,mask}/       # training-ready image/mask pairs
    ├── contextual/           # per-image DAFT v3 label JSONs
    └── task/SDG_result.csv   # one row per generated image: source, mask, seed, guidance, steps, PSNR
```

## The AI models

The 80.6 GB `models/pretrained` bundle plus the per-usecase checkpoint form a
pipeline of seven models. Only the first two do generation; the rest are
conditioning encoders and tooling.

### Cosmos-Predict2-2B-Text2Image — the base generator

NVIDIA's Cosmos World Foundation Model family, `text2image` variant, 2 B
parameters (a 14 B variant exists; `model_size=2b` here, and the checkpoint
must match). It is a latent **diffusion transformer**: inference runs 35
denoising steps per image at guidance scale 7.0 (per `SDG_result.csv`). This
supplies the general image prior — realistic textures, lighting, and
local structure. HF repo `nvidia/Cosmos-Predict2-2B-Text2Image` (gated;
license accepted once per HF account).

### Cosmos-AnomalyGen-Metal-2B — the finetuned defect specialist

`nvidia/Cosmos-AnomalyGen-Metal-2B` (HF), a finetune of Cosmos-Predict2-2B for
metal-surface defect inpainting, shipped as `iter_10000.pt` + its training
`ag_config.yaml`. Its cookbook (which reproduces the shipped training run)
shows how it was built:

- **Anomaly embedding**: a learned, *unfrozen* embedding per taxonomy entry —
  `[metal_surface] × [MT_Blowhole, MT_Break, MT_Crack, MT_Fray, MT_Uneven]`
  (the magnetic-tile defect classes: blowhole, break, crack, fray, uneven).
  This taxonomy is baked into the checkpoint; requesting a pair outside it
  fails `validate_jsonl.py`.
- **Mask conditioning**: the AMP-placed defect mask is encoded (by NVDINOV2,
  below) and conditions generation, so the defect appears exactly where the
  mask says, with the right class appearance.
- **Training recipe** (cookbook `ag_config.yaml`): DDP FP32, lr 0.02, batch 2,
  512×512, 2000 iters with `random_ratio_crop` augmentation (ratio 1.5–8.0)
  at p=0.5, early-stop on the `nn` validation metric. (The shipped checkpoint
  step is 10000 — a longer production run of the same recipe.)

Glass and PCBA have sibling checkpoints (`Cosmos-AnomalyGen-Glass-2B`, `-PCB-2B`,
steps 9000/14000) — same architecture, different taxonomy and data.

### google-t5/t5-large — text encoder

Frozen T5-large provides the text-conditioning pathway
(`t5_model_name: checkpoints/google-t5/t5-large` in the model config), turning
the per-defect prompt/taxonomy text into embeddings the diffusion transformer
cross-attends to. (`t5-11b` is also staged in the bundle for larger variants.)

### NVDINOV2 — mask encoder

NVIDIA's DINOv2 derivative
(`NVDINOV2/nv_dinov2_classification_model.ckpt`, container-baked), wired in as
the **mask encoder** (`model.config.ag_config.mask_encoder`): it embeds the
binary defect mask into features that steer *where* and *what shape* the
generated defect takes.

### facebook/dinov2-large — feature backbone

Meta's DINOv2-large self-supervised ViT, pulled into the bundle by
`download_checkpoints`. Used as an auxiliary feature extractor on the
evaluation side (the `nn` — nearest-neighbor feature similarity — validation
metric that early-stop and best-checkpoint selection key on), not in the
generation path itself.

### SAM2 + Qwen3-VL-4B-Instruct — text-mode ROI tooling

`sam2/sam2.1_hiera_large.pt` (Segment Anything 2, hierarchical ViT) and
`Qwen/Qwen3-VL-4B-Instruct` (a 4 B vision-language model) are container-baked
and staged for AMP's **`text` spatial-dependency mode**: the VLM proposes
defect-plausible regions from a text description and SAM2 converts them to
masks. The metal flow doesn't exercise them (its raw dataset ships explicit
masks → `free`/spec-driven placement), but the inference task symlinks them in
because the AMP tooling expects them present.

### C-RADIO (v2-B checkpoint + v3-B safetensors) — distilled vision backbone

NVIDIA's RADIO family — vision backbones distilled from multiple foundation
teachers (CLIP/DINOv2/SAM). `C-RADIOv2_B.pth` ships in the bundle's symlink
set and `nvidia/C-RADIOv3-B` is downloaded into `nvidia/C-RADIO-V3/`
specifically for the **Day 0** finetune path (PCBA), which uses it as a
perceptual feature extractor. Present in the bundle, idle in this Day 1 flow.

### Not deployed here: Qwen-Image-Edit-NVPCB-OVSL2SL

The Day 0 PCBA flows additionally call an **image-edit NIM**
(`nvidia/Qwen-Image-Edit-NVPCB-OVSL2SL`, an NVIDIA finetune of Qwen-Image-Edit
for synthetic-to-real appearance transfer on PCB renders). It needs its own
GPU, and this pool has exactly one — so Day 0 would require an external
endpoint or added capacity. Listed for completeness; no NIM runs on this
cluster.

### How they compose at inference time

```
clean image ─┐
             ├─ AMP (prep_testcase; masks from dataset, optionally Qwen3-VL+SAM2)
defect mask ─┘        │
                      ▼ inference.jsonl (num_sdg entries)
taxonomy text ─ T5-large ──────────┐
placed mask ─── NVDINOV2 ──────────┼─▶ Cosmos-AnomalyGen-Metal-2B ─▶ reconstructed_image/
(anomaly embedding, per defect) ───┘   (Cosmos-Predict2-2B core,
                                        35 steps, guidance 7.0)
                                              │
                                              ▼
                                   DAFT v3 labels + SDG_result.csv
```

## Measured behavior (validated run)

From `texture_defect_gen_day1_manual_roi-51f57bdd` (num_sdg=30) — full tables
in `TIMINGS.md`:

- **Fixed cost per run**: ~22 min pulling the 80.6 GB pretrained tree from
  MinIO into the pod (65–130 MB/s), ~7.3 min model init on the RTX PRO 6000.
- **Marginal cost**: ~2 s/image generation (≈25 it/s at 35 steps, 21.6 GiB
  GPU memory).
- Implication: `num_sdg` is cheap at the margin — batch big runs; the
  download+init overhead dominates small ones.
