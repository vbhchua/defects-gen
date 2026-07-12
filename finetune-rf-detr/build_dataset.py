#!/usr/bin/env python3
"""Build a COCO detection dataset (train/valid/test) from DIG metal-surface runs.

Images:  inference/reconstructed_image/*.png  (synthetic defect images)
Labels:  bounding boxes derived from inference/original_mask/*.png connected
         components; class parsed from the filename
         (metal_surface+<CLASS>_<idx>.png).

Split:   GROUP split by base clean image (SDG_result.csv image_filename).
         Every generated image inherits its base's split, so no background
         is shared between train/valid/test.
"""
import argparse
import csv
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

DATA = Path("/home/ubuntu/defects-gen/data")
USECASES = {
    "metal": {
        "runs": {
            "r1": DATA / "texture_defect_gen_day1_manual_roi-51f57bdd",
            "r2": DATA / "texture_defect_gen_day1_manual_roi-c2f5afb4",
        },
        "out": Path("/home/ubuntu/defects-gen/finetune-rf-detr/dataset"),
        "classes": ["MT_Blowhole", "MT_Break", "MT_Crack", "MT_Fray", "MT_Uneven"],
        "description": "DIG metal-surface synthetic defects",
    },
    "glass": {
        "runs": {"r1": DATA / "texture_defect_gen_day1_manual_roi-6771352a"},
        "out": Path("/home/ubuntu/defects-gen/finetune-rf-detr/dataset_glass"),
        "classes": ["oil", "scratch", "stain"],
        "description": "DIG phone-screen (glass) synthetic defects",
    },
}
args = argparse.ArgumentParser()
args.add_argument("--usecase", choices=USECASES, default="metal")
UC = USECASES[args.parse_args().usecase]
RUNS, OUT, CLASSES = UC["runs"], UC["out"], UC["classes"]
MIN_AREA = 9          # px^2, drop mask noise specks
SPLIT_FRACS = {"train": 0.70, "valid": 0.15, "test": 0.15}
SEED = 42


def load_records():
    """One record per generated image: paths, class, base group, md5."""
    records, seen_md5 = [], {}
    for run, root in RUNS.items():
        base_of = {}
        with open(root / "inference_daft_v3/task/SDG_result.csv") as f:
            for row in csv.DictReader(f):
                out_name = Path(row["output_filename"]).name
                base_of[out_name] = Path(row["image_filename"]).name
        img_dir = root / "inference/reconstructed_image"
        mask_dir = root / "inference/original_mask"
        for img in sorted(img_dir.glob("*.png")):
            cls = img.stem.split("+")[1].rsplit("_", 1)[0]
            assert cls in CLASSES, f"unknown class {cls} in {img.name}"
            md5 = hashlib.md5(img.read_bytes()).hexdigest()
            if md5 in seen_md5:
                print(f"  dup skipped: {run}/{img.name} == {seen_md5[md5]}")
                continue
            seen_md5[md5] = f"{run}/{img.name}"
            records.append({
                "run": run,
                "img": img,
                "mask": mask_dir / img.name,
                "cls": cls,
                "base": base_of[img.name],
                "file_name": f"{run}_{img.name}",
            })
    return records


def boxes_from_mask(mask_path):
    m = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    assert m is not None, mask_path
    binary = (m > 127).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= MIN_AREA:
            boxes.append([int(x), int(y), int(w), int(h)])
    return boxes, m.shape  # (H, W)


def assign_splits(records):
    """Greedy group assignment by image count; verify class coverage."""
    by_base = defaultdict(list)
    for r in records:
        by_base[r["base"]].append(r)
    total = len(records)
    targets = {s: f * total for s, f in SPLIT_FRACS.items()}
    rng = random.Random(SEED)
    bases = sorted(by_base, key=lambda b: len(by_base[b]), reverse=True)
    rng.shuffle(bases)
    bases.sort(key=lambda b: len(by_base[b]), reverse=True)  # stable greedy, seeded tiebreak
    filled = {s: 0 for s in SPLIT_FRACS}
    split_of = {}
    for b in bases:
        # put group into the split with the largest remaining deficit
        s = max(SPLIT_FRACS, key=lambda s: targets[s] - filled[s])
        split_of[b] = s
        filled[s] += len(by_base[b])
    # verify every class appears in every split
    cover = {s: Counter() for s in SPLIT_FRACS}
    for r in records:
        cover[split_of[r["base"]]][r["cls"]] += 1
    for s, cnt in cover.items():
        missing = [c for c in CLASSES if cnt[c] == 0]
        assert not missing, f"split {s} missing classes {missing}"
    return split_of, by_base, cover


def main():
    print("loading records...")
    records = load_records()
    print(f"{len(records)} unique images from {len(RUNS)} runs")
    split_of, by_base, cover = assign_splits(records)

    print("\nbase-group split assignment:")
    for s in SPLIT_FRACS:
        bs = [b for b, sp in split_of.items() if sp == s]
        n = sum(len(by_base[b]) for b in bs)
        print(f"  {s:5s}: {len(bs):2d} bases, {n:3d} images | per-class {dict(cover[s])}")

    if OUT.exists():
        shutil.rmtree(OUT)
    manifest = {"splits": {}, "classes": CLASSES, "seed": SEED}
    box_counter, box_sizes = Counter(), []
    for s in SPLIT_FRACS:
        (OUT / s).mkdir(parents=True)
        coco = {
            "info": {"description": UC["description"]},
            "licenses": [{"id": 1, "name": "CC-BY-4.0"}],
            "categories": [{"id": i + 1, "name": c, "supercategory": "defect"}
                           for i, c in enumerate(CLASSES)],
            "images": [], "annotations": [],
        }
        img_id = ann_id = 0
        recs = [r for r in records if split_of[r["base"]] == s]
        for r in sorted(recs, key=lambda r: r["file_name"]):
            boxes, (H, W) = boxes_from_mask(r["mask"])
            if not boxes:
                print(f"  WARNING empty mask, skipped: {r['file_name']}")
                continue
            shutil.copy(r["img"], OUT / s / r["file_name"])
            img_id += 1
            coco["images"].append({"id": img_id, "file_name": r["file_name"],
                                   "width": W, "height": H})
            for x, y, w, h in boxes:
                ann_id += 1
                coco["annotations"].append({
                    "id": ann_id, "image_id": img_id,
                    "category_id": CLASSES.index(r["cls"]) + 1,
                    "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0,
                })
                box_sizes.append((w, h))
            box_counter[len(boxes)] += 1
        with open(OUT / s / "_annotations.coco.json", "w") as f:
            json.dump(coco, f)
        manifest["splits"][s] = {
            "images": img_id, "annotations": ann_id,
            "bases": sorted(b for b, sp in split_of.items() if sp == s),
            "per_class_images": dict(cover[s]),
        }
        print(f"  wrote {s}: {img_id} images, {ann_id} boxes")

    ws = np.array([w for w, h in box_sizes]); hs = np.array([h for w, h in box_sizes])
    manifest["box_stats"] = {
        "total_boxes": len(box_sizes),
        "boxes_per_image_hist": dict(sorted(box_counter.items())),
        "width_px": {"min": int(ws.min()), "median": float(np.median(ws)), "max": int(ws.max())},
        "height_px": {"min": int(hs.min()), "median": float(np.median(hs)), "max": int(hs.max())},
    }
    with open(OUT / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print("\nbox stats:", json.dumps(manifest["box_stats"], indent=1))


if __name__ == "__main__":
    main()
