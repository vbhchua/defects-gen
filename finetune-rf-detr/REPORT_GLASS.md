# RF-DETR Large Finetuning Report — DIG Phone-Screen (Glass) Defect Detection

**Date:** 2026-07-12 · **Hardware:** 1× NVIDIA RTX PRO 6000 Blackwell (96 GB) · **Everything ran locally.**
Follow-up to [REPORT.md](REPORT.md) (metal surface). Same pipeline, scripts now
parameterized with `--usecase glass`. **All metal artifacts preserved** — glass
uses separate `dataset_glass/`, `output_glass/`, `eval_glass/` directories; the
metal checkpoints were additionally write-protected and MD5-verified intact
after this run (`output/.metal_checkpoint.md5`).

## 1. Dataset

Source: DIG run `data/texture_defect_gen_day1_manual_roi-6771352a` — the
phone-screen ("glass" / mobile_screen) use case. 900 synthetic 640×640 images,
perfectly balanced across 3 classes (**oil, scratch, stain**, 300 each), no
duplicates. Boxes derived from pixel masks exactly as for metal (8-connected
components, min 9 px²): **928 boxes** (97.5 % single-defect images), median box
99×119 px.

Same leakage-free **group split by base clean image** — the 900 images come from
**20 base images** (45 variants each):

| Split | Base groups | Images | Boxes | oil / scratch / stain (images) |
|---|---|---|---|---|
| train | 14 | 630 | 651 | 210 / 210 / 210 |
| valid | 3 | 135 | 141 | 45 / 45 / 45 |
| test  | 3 | 135 | 136 | 45 / 45 / 45 |

Unlike metal, every split is exactly class-balanced.

## 2. Training

Identical recipe: `RFDETRLarge` (Apache 2.0) from COCO weights, 704×704,
batch 16, EMA, early stopping (patience 10), max 40 epochs.

- **Early-stopped at epoch 22** (best val at epoch 12). Wall time ≈ 10 min.
- Best validation mAP@50:95: **0.735** (regular) / **0.761** (EMA, epoch 11).
- Curves: `eval_glass/training_curves.png` — converges faster than metal
  (fewer, more visually distinct classes; more images per class).

## 3. Test-set results

Checkpoint: `output_glass/checkpoint_best_total.pth`, COCOeval.

| Metric | valid | **test** |
|---|---|---|
| mAP@50:95 | 0.758 | **0.776** |
| mAP@50 | 0.926 | **0.966** |
| mAP@75 | 0.841 | **0.891** |
| AR@100 | 0.811 | 0.819 |
| AP small / medium / large | 0.54 / 0.60 / 0.82 | 0.58 / 0.72 / 0.85 |

Per-class (test):

| Class | AP@50:95 | AP@50 | AR | GT boxes |
|---|---|---|---|---|
| oil | 0.899 | 1.000 | 0.933 | 45 |
| scratch | 0.719 | 0.949 | 0.778 | 46 |
| stain | 0.709 | 0.949 | 0.744 | 45 |

Test slightly exceeds valid — with only 3 base groups per split this is normal
sampling variance between backgrounds, not a red flag; the two agree within a
few points.

### Error analysis (confusion at conf 0.5, IoU 0.5 — `eval_glass/confusion_matrix_test.png`)

- **Zero cross-class confusion** — oil/scratch/stain are never mistaken for
  each other. All errors are detection errors, not classification errors.
- oil: perfect at IoU 0.5 (45/45, AP@50 = 1.0); its AP@50:95 gap is purely box
  tightness on large translucent smears.
- scratch: 43/46 found, 3 missed + 1 FP — thin faint lines, the hardest to
  localize tightly (lowest AP@75 contribution).
- stain: 42/45 found, 3 missed + 2 FP — misses are the tiny (<10 px) specks;
  small-object AP (0.58) is the weakest area overall.

### Inference speed

**18.0 ms/image** (~56 FPS) single-image `model.predict()` after
`optimize_for_inference()` (640×640 inputs; metal's smaller images ran 11 ms).

Qualitative GT-vs-prediction pairs: `eval_glass/predictions_test.png`.

## 4. Metal vs glass summary

| | Metal (5 classes) | Glass (3 classes) |
|---|---|---|
| Train / valid / test images | 439 / 85 / 105 | 630 / 135 / 135 |
| Test mAP@50 | 0.851 | **0.966** |
| Test mAP@50:95 | 0.688 | **0.776** |
| Weakest class | MT_Break (AP@50 0.67) | stain/scratch (AP@50 0.95) |
| Cross-class confusion | some (Blowhole↔Fray/Crack) | none |

Glass is the easier task (fewer, visually distinct classes; more data; uniform
imaging) and the model is close to saturating mAP@50 on it. Remaining headroom
is small-defect recall and box tightness. As with metal, the decisive next
validation is performance on **real** (non-synthetic) defect images.

## 5. Real-world sanity check (added later on 2026-07-12)

Test set: the **original Roboflow mobile-screen export** that seeded DIG's
glass use case (recovered from `s3://osmo/dig/uploads/glass-zip/`): 300 real
defect images / 636 boxes with the export's own annotations, remapped to the
model's classes. "Unseen" (285 imgs / 607 boxes) excludes the 15 images whose
masks/anomalies DIG used; results are within half a point of the full set.

| Set | mAP@50:95 | mAP@50 | mAP@75 | AR@100 |
|---|---|---|---|---|
| Synthetic test (reference) | 0.776 | 0.966 | 0.891 | 0.819 |
| **Real, unseen** | **0.290** | **0.563** | 0.279 | 0.480 |

Per-class on real (unseen): **oil 0.949 AP@50 / 0.653 AP@50:95** — near-perfect
transfer; **stain 0.421 / 0.096**; **scratch 0.321 / 0.120**.

Reading (`eval_glass/real_predictions.png`, `eval_glass/metrics_real.json`):

- Transfer is substantially better than the metal use case (0.563 vs ~0.39
  real mAP@50) — plausibly because the synthetic backgrounds *are* real phone
  photos from this same dataset, so only the defect appearance is synthetic.
- **Oil is production-grade on real data already.** Scratch/stain are found at
  moderate rates but with **poor box quality** (AP@75 collapses to ~0.1–0.28):
  real scratches are long, thin and often annotated as larger regions than the
  synthetic point-defect style the model learned. Some real oil smudges are
  classified as stain (appearance confusion the synthetic set never taught).
- Same conclusion as metal: synthetic-only training carries real signal but
  isn't deployable alone; mixing real images and diversifying synthetic defect
  geometry (especially elongated scratches) are the next levers.

## 6. Reproduce

```bash
cd /home/ubuntu/defects-gen/finetune-rf-detr
.venv/bin/python build_dataset.py --usecase glass
.venv/bin/python train.py --usecase glass      # ~10 min
.venv/bin/python evaluate.py --usecase glass   # eval_glass/metrics.json + figures
```
