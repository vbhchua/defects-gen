# RF-DETR Large Data-Scaling Study — DIG Metal-Surface Defects

**Date:** 2026-07-12 · **Hardware:** 1× NVIDIA RTX PRO 6000 Blackwell (96 GB) · all local.
Companion to [REPORT.md](REPORT.md). Question: **how much does detector
performance improve with more synthetic training data per class?**

## 1. Scope: why 120 vs 240 (and not 360)

The study was planned as 120 / 240 / 360 images per class. During data prep we
found that **DIG derives its (mask, seed) generation configs deterministically
from `num_sdg`**: the new 1,200-image run (`…-dd864727`, `num_sdg=1200`)
regenerated *all 600 configs* of the earlier runs (verified: 600/600 config-key
overlap; regenerated images are near-copies up to GPU noise, 4 even bitwise
identical). Old + new therefore contain only **240 distinct configs per class**
— a 360/class dataset would need ~1/3 near-duplicate padding, which would
artificially flatten the scaling curve. This report therefore covers **120 vs
240/class**. A clean 360 point requires a fresh `num_sdg=1800` run — since
approved and in flight (`…-3bcdb12f`); a follow-up will extend the curve with
all three models rebuilt from that single run.

A first dedup attempt using perceptual hashing (dHash) was discarded: images
sharing a base background collide on coarse hashes even when their defects
differ — config-key comparison is the correct duplicate test for DIG data.

## 2. Methodology

- **Single source**: both datasets built solely from run `…-dd864727`
  (1,200 images, exactly 240 distinct configs/class, 20 base backgrounds;
  2 images dropped for empty masks).
- **One shared group split** of the 20 base clean images (~70/15/15, seed 42,
  every class in every split) — identical base assignment for both sizes, so
  no model ever trains on a test background.
- **Nested sampling**: per (split, class), images ordered by a stable hash and
  the first k taken → `dataset_120 ⊂ dataset_240`.
- **Achieved sizes** (nominal → actual): 120/class → 420/90/90 train/valid/test
  (quotas met exactly); 240/class → 825/153/156 (some (split, class) pools run
  short because classes are unevenly spread over base groups; shortfalls in
  `scaling/manifest.json`).
- **Identical recipe**: RF-DETR Large from COCO weights, 704², batch 16, EMA,
  early stopping (patience 10, max 40). 120-model stopped at epoch 36,
  240-model ran all 40.
- **Common test set**: both models are additionally evaluated on the *same*
  156-image test split of `dataset_240` (superset of the 120's test images,
  same held-out bases) — the numbers to compare.

## 3. Results

### Common test set (156 images, identical for both models)

| Metric | 120/class | 240/class | Δ absolute | Δ relative |
|---|---|---|---|---|
| **mAP@50:95** | 0.698 | **0.772** | **+0.074** | **+10.6 %** |
| mAP@50 | 0.865 | 0.926 | +0.061 | +7.1 % |
| mAP@75 | 0.793 | 0.859 | +0.066 | +8.3 % |
| AR@100 | 0.819 | 0.854 | +0.035 | +4.3 % |

### Per-class AP@50:95 (common test)

| Class | 120/class | 240/class | Δ |
|---|---|---|---|
| MT_Blowhole | 0.668 | 0.747 | +0.079 |
| MT_Break | 0.540 | 0.630 | +0.090 |
| MT_Crack | 0.612 | 0.712 | +0.100 |
| MT_Fray | 0.750 | 0.826 | +0.076 |
| MT_Uneven | 0.919 | 0.947 | +0.028 |

### Own-split metrics (each model's own valid/test, ~70/15/15)

| | 120: valid / test | 240: valid / test |
|---|---|---|
| mAP@50:95 | 0.639 / 0.696 | 0.732 / 0.772 |
| mAP@50 | 0.794 / 0.871 | 0.875 / 0.926 |

Latency is unchanged (11.2 ms/image for both — same architecture).

## 4. Findings

1. **Doubling synthetic data per class gives a consistent, meaningful uplift**:
   +7 to +11 points relative across every headline metric, and *every class
   improves*. No sign of saturation between 120 and 240.
2. **Hard classes benefit most**: MT_Crack (+0.100) and MT_Break (+0.090) —
   the low-contrast, thin/smudgy defects — gain the most; the already-easy
   MT_Uneven is near its ceiling (+0.028). More synthetic data
   disproportionately helps exactly where the detector is weakest.
3. **Localization tightens too**: mAP@75 rises as much as mAP@50, so extra data
   improves box quality, not just detection hit-rate.
4. Consistency check: the 120-model's 0.698 on the common test aligns with the
   original REPORT.md model (~126/class, different split) at 0.688 — the
   pipeline is reproducible.
5. **Caveats**: single training run per size (no seed variance estimate);
   156-image test set → CIs of a few points; diversity is bounded by 20 base
   backgrounds and 25 mask shapes per class regardless of image count;
   synthetic→synthetic evaluation only.

## 5. Recommendation

The curve is still rising at 240/class, so a `num_sdg=1800` run (360/class
distinct configs, ~4 h GPU) is the obvious next experiment — ideally combined
with **new base backgrounds**, since config diversity (not image count alone)
is what the 20 shared bases ultimately limit. Validation on real defect images
remains the decisive gate before deployment claims.

## 6. Artifacts & reproduce

```
scaling/dataset_120, dataset_240   # nested COCO datasets (gitignored)
scaling/output_120, output_240     # checkpoints (gitignored)
scaling/eval_120.json, eval_240.json, manifest.json, scaling_comparison.png
```

```bash
.venv/bin/python build_scaling_datasets.py
for n in 120 240; do
  .venv/bin/python scaling_train.py --n $n
  .venv/bin/python scaling_eval.py  --n $n
done
```
