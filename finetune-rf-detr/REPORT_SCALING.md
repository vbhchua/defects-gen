# RF-DETR Large Data-Scaling Study — DIG Metal-Surface Defects

**Date:** 2026-07-12 · **Hardware:** 1× NVIDIA RTX PRO 6000 Blackwell (96 GB) · all local.
Companion to [REPORT.md](REPORT.md). Two questions:

1. How does detector performance scale with synthetic training data (120 / 240 / 360 images per class)?
2. **Does it transfer to real photographs?** (sanity check against the original Magnetic Tile dataset)

## 1. Data

All three datasets are built from a **single DIG run** (`…-3bcdb12f`,
`num_sdg=1800` → exactly 360 distinct (mask, seed) configs per class, 1 empty
mask dropped). Single-source matters: DIG derives generation configs
deterministically from `num_sdg`, so a larger re-run regenerates every smaller
run's configs — **runs are not additive**, and combining runs only adds
near-duplicate pixel variants (verified: the 1200-run contained all 600 configs
of the older runs, 4 bitwise-identical). A perceptual-hash dedup was evaluated
and rejected — same-background images collide on dHash even with different
defects; config-key comparison is the correct duplicate test.

- **One shared group split** of the 20 base backgrounds (~70/15/15, seed 42);
  no model ever trains on a test background.
- **Nested sampling** per (split, class): `dataset_120 ⊂ dataset_240 ⊂ dataset_360`.
- Achieved sizes: 120 → 420/90/90 and 240 → 840/180/180 (quotas exact);
  360 → 1260/254/247 (minor valid/test shortfalls, see `scaling/manifest.json`).
- Identical recipe: RF-DETR Large from COCO weights, 704², batch 16, EMA,
  early stopping (patience 10, max 40 epochs).

## 2. Synthetic scaling — it works

Common test set = `dataset_360/test` (247 images, superset of the smaller
test sets, same held-out bases — identical images for all three models):

| Images/class | mAP@50:95 | mAP@50 | mAP@75 |
|---|---|---|---|
| 120 | 0.724 | 0.871 | 0.805 |
| 240 | 0.758 | 0.910 | 0.835 |
| 360 | **0.797** | **0.927** | **0.889** |

Monotone improvement, +7.3 points mAP@50:95 from 120→360 (+10%), still rising
at 360. On synthetic data, more synthetic data straightforwardly helps.

## 3. Real-world sanity check — the punchline

Test set: the **original Magnetic Tile dataset** (the real photographs whose
clean images and mask shapes seeded DIG): 386 defect images / 443 boxes,
boxes derived from the dataset's own pixel masks. We report the **unseen**
subset (361 images) that excludes the 25 images whose mask shapes were used by
the generation pipeline; full-set numbers are within ±0.005 of these.

| Images/class | REAL mAP@50:95 | REAL mAP@50 | REAL AR@100 |
|---|---|---|---|
| 120 | **0.253** | **0.395** | 0.605 |
| 240 | 0.224 | 0.375 | 0.578 |
| 360 | 0.208 | 0.350 | 0.540 |

Per-class AP@50 on real (unseen):

| Model | Blowhole | Break | Crack | Fray | Uneven |
|---|---|---|---|---|---|
| 120 | 0.684 | 0.338 | 0.067 | 0.412 | 0.473 |
| 240 | 0.560 | 0.357 | 0.097 | 0.368 | 0.494 |
| 360 | 0.539 | 0.297 | 0.101 | 0.343 | 0.470 |

**Findings:**

1. **There is a large sim-to-real gap**: 0.93 mAP@50 on synthetic vs 0.35–0.40
   on real. The detectors carry real signal (AR ≈ 0.54–0.61 — they *find* most
   defects) but with poor precision and box quality on real textures.
2. **The synthetic scaling curve inverts on real data**: the 120/class model
   transfers best; every metric declines monotonically as synthetic data
   grows. More images from the *same 20 backgrounds and 25 mask shapes* make
   the model increasingly specialized to the synthetic rendering distribution
   — classic diversity-starved overfitting, not a data-volume problem.
   (Differences are modest — 4.5 points mAP@50 across the sweep on ~412 boxes —
   but the monotone trend is consistent across mAP@50:95, mAP@50, mAP@75, AR.)
3. **Class-level transfer varies hugely**: Blowhole transfers best (0.68
   AP@50); Crack essentially does not transfer (0.07–0.10) — real cracks are
   hairline, low-contrast structures the synthetic renders don't resemble
   closely enough. Qualitative examples: `scaling/real_predictions.png`.
4. Consistency: earlier 120-vs-240 iteration (built from the 1200-image run,
   different sampling) showed the same synthetic-side trend (0.698→0.772).

## 4. Recommendations

- **Diversity, not volume**: adding synthetic images beyond ~120/class from
  the same 20 bases buys synthetic-side polish but *hurts* real transfer.
  Next lever is more base backgrounds and more/varied defect mask sources.
- **Mix real data in**: even a small real training split (say 20–50 real
  images) with synthetic augmentation is the standard sim-to-real remedy and
  the obvious next experiment; the current models have never seen a real photo.
- **Crack needs targeted work**: thin-structure defects need either real
  examples or higher-fidelity synthetic cracks.
- Treat synthetic-side mAP as a development metric only; gate deployment
  decisions on real-data evaluation.

## 5. Artifacts & reproduce

```
scaling/eval_{120,240,360}.json    # synthetic evals (own + common test)
scaling/eval_real.json             # real-world sanity check
scaling/scaling_comparison.png     # synthetic vs real curves + per-class
scaling/real_predictions.png       # qualitative real-image predictions (120 model)
scaling/manifest.json              # split/quota bookkeeping
real_test/                         # real COCO test set (gitignored)
```

```bash
.venv/bin/python build_scaling_datasets.py        # from run …-3bcdb12f
for n in 120 240 360; do
  .venv/bin/python scaling_train.py --n $n
  .venv/bin/python scaling_eval.py  --n $n
done
.venv/bin/python build_real_testset.py            # real Magnetic Tile → COCO
.venv/bin/python eval_real.py
```
