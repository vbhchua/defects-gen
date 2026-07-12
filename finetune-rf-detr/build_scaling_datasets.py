#!/usr/bin/env python3
"""Build nested 120/240/360-per-class metal datasets for the data-scaling study.

Design (fair comparison):
- ONE shared group split of the 20 base clean images (~70/15/15 by image count,
  every class present in every split) used by all sizes.
- Nested sampling: within each (split, class), images are ordered by a stable
  hash and the first k are taken — so dataset_120 ⊂ dataset_240 (⊂ dataset_360).
- Single-source pool: ONLY the newest DIG run. Its (mask, seed) config space is
  a superset of the older runs' (verified), so older runs add zero new distinct
  configs — including them would only inject near-duplicate pixel variants.
  Perceptual-hash dedup was removed: images sharing a base background collide
  on dHash even when their defects differ (false positives).

Per-(split,class) quotas: size N/class → train .70*N, valid .15*N, test .15*N.
If a (split, class) pool is short, everything available is taken and the
shortfall reported in the manifest.
"""
import csv
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DATA = Path("/home/ubuntu/defects-gen/data")
SOURCE_RUN = DATA / "texture_defect_gen_day1_manual_roi-3bcdb12f"  # num_sdg=1800
ROOT = Path("/home/ubuntu/defects-gen/finetune-rf-detr/scaling")
CLASSES = ["MT_Blowhole", "MT_Break", "MT_Crack", "MT_Fray", "MT_Uneven"]
SIZES = [120, 240, 360]
SPLIT_FRACS = {"train": 0.70, "valid": 0.15, "test": 0.15}
MIN_AREA = 9
SEED = 42


def load_pool():
    """One record per image from the single source run (MD5 exact-dup guard)."""
    records, by_md5 = [], set()
    stats = {"exact_dup": 0}
    root = SOURCE_RUN
    base_of = {}
    with open(root / "inference_daft_v3/task/SDG_result.csv") as f:
        for row in csv.DictReader(f):
            base_of[Path(row["output_filename"]).name] = Path(row["image_filename"]).name
    for img in sorted((root / "inference/reconstructed_image").glob("*.png")):
        cls = img.stem.split("+")[1].rsplit("_", 1)[0]
        md5 = hashlib.md5(img.read_bytes()).hexdigest()
        if md5 in by_md5:
            stats["exact_dup"] += 1
            continue
        by_md5.add(md5)
        rid = f"src_{img.name}"
        records.append({
            "img": img,
            "mask": root / "inference/original_mask" / img.name,
            "cls": cls, "base": base_of[img.name], "file_name": rid,
            # stable per-image order key for nested sampling
            "order": hashlib.md5(rid.encode()).hexdigest(),
        })
    return records, stats


def assign_bases(records):
    """Greedy split of bases targeting image-count fractions + class coverage."""
    by_base = defaultdict(list)
    for r in records:
        by_base[r["base"]].append(r)
    total = len(records)
    targets = {s: f * total for s, f in SPLIT_FRACS.items()}
    rng = random.Random(SEED)
    bases = sorted(by_base)
    rng.shuffle(bases)
    bases.sort(key=lambda b: len(by_base[b]), reverse=True)
    filled = {s: 0 for s in SPLIT_FRACS}
    split_of = {}
    for b in bases:
        s = max(SPLIT_FRACS, key=lambda s: targets[s] - filled[s])
        split_of[b] = s
        filled[s] += len(by_base[b])
    cover = {s: Counter() for s in SPLIT_FRACS}
    for r in records:
        cover[split_of[r["base"]]][r["cls"]] += 1
    for s, cnt in cover.items():
        missing = [c for c in CLASSES if cnt[c] == 0]
        assert not missing, f"split {s} missing {missing}"
    return split_of, cover


def boxes_from_mask(mask_path):
    m = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    assert m is not None, mask_path
    n, _, stats, _ = cv2.connectedComponentsWithStats((m > 127).astype(np.uint8), connectivity=8)
    return [[int(x), int(y), int(w), int(h)]
            for x, y, w, h, area in stats[1:] if area >= MIN_AREA], m.shape


def write_coco(recs, out_dir, boxes_cache):
    out_dir.mkdir(parents=True)
    coco = {
        "info": {"description": "DIG metal-surface synthetic defects (scaling study)"},
        "licenses": [{"id": 1, "name": "CC-BY-4.0"}],
        "categories": [{"id": i + 1, "name": c, "supercategory": "defect"}
                       for i, c in enumerate(CLASSES)],
        "images": [], "annotations": [],
    }
    img_id = ann_id = 0
    for r in sorted(recs, key=lambda r: r["file_name"]):
        boxes, (H, W) = boxes_cache[r["file_name"]]
        shutil.copy(r["img"], out_dir / r["file_name"])
        img_id += 1
        coco["images"].append({"id": img_id, "file_name": r["file_name"], "width": W, "height": H})
        for x, y, w, h in boxes:
            ann_id += 1
            coco["annotations"].append({"id": ann_id, "image_id": img_id,
                                        "category_id": CLASSES.index(r["cls"]) + 1,
                                        "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0})
    with open(out_dir / "_annotations.coco.json", "w") as f:
        json.dump(coco, f)
    return img_id, ann_id


def main():
    records, dstats = load_pool()
    print(f"pool: {len(records)} unique images | exact dups removed: {dstats['exact_dup']}")
    print("per class:", dict(Counter(r["cls"] for r in records)))

    split_of, cover = assign_bases(records)
    for s in SPLIT_FRACS:
        n = sum(cover[s].values())
        print(f"  {s:5s}: {n:4d} images | {dict(cover[s])}")

    # drop empty-mask records upfront so quotas reflect usable images
    boxes_cache = {}
    usable = []
    for r in records:
        boxes, shape = boxes_from_mask(r["mask"])
        if boxes:
            boxes_cache[r["file_name"]] = (boxes, shape)
            usable.append(r)
        else:
            print(f"  WARNING empty mask, dropped: {r['file_name']}")
    records = usable

    # nested sampling per (split, class)
    pool = defaultdict(list)
    for r in records:
        pool[(split_of[r["base"]], r["cls"])].append(r)
    for k in pool:
        pool[k].sort(key=lambda r: r["order"])

    manifest = {"seed": SEED, "classes": CLASSES, "dedup": dstats, "sizes": {}}
    for n_per_class in SIZES:
        ds_root = ROOT / f"dataset_{n_per_class}"
        if ds_root.exists():
            shutil.rmtree(ds_root)
        size_info = {"quota": {}, "achieved": {}, "images": {}, "boxes": {}}
        for s, frac in SPLIT_FRACS.items():
            quota = round(n_per_class * frac)
            chosen = []
            for c in CLASSES:
                avail = pool[(s, c)]
                take = avail[:quota]
                if len(take) < quota:
                    print(f"  SHORTFALL {n_per_class}/{s}/{c}: {len(take)}/{quota}")
                chosen += take
                size_info["achieved"].setdefault(s, {})[c] = len(take)
            size_info["quota"][s] = quota
            ni, na = write_coco(chosen, ds_root / s, boxes_cache)
            size_info["images"][s], size_info["boxes"][s] = ni, na
        manifest["sizes"][str(n_per_class)] = size_info
        print(f"dataset_{n_per_class}: " +
              " ".join(f"{s}={size_info['images'][s]}" for s in SPLIT_FRACS))

    ROOT.mkdir(exist_ok=True)
    with open(ROOT / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print("wrote scaling/manifest.json")


if __name__ == "__main__":
    main()
