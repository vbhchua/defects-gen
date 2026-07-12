#!/usr/bin/env python3
"""Build a real-world COCO test set from the original Magnetic Tile dataset.

Images: <class>/Imgs/*.jpg (grayscale real photos), masks: same-stem .png.
Boxes derived from mask connected components exactly like the synthetic sets.
Two annotation files over the same image dir:
  _annotations.coco.json         all real defect images
  _annotations_unseen.coco.json  excluding the 25 mask-shape donors used by DIG
"""
import csv
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

SRC = Path("/tmp/claude-1000/-home-ubuntu/a5fdb7c7-f345-4fb1-8852-359ccc336754/scratchpad/mt_real")
OUT = Path("/home/ubuntu/defects-gen/finetune-rf-detr/real_test")
SDG_CSV = Path("/home/ubuntu/defects-gen/data/texture_defect_gen_day1_manual_roi-3bcdb12f/inference_daft_v3/task/SDG_result.csv")
CLASSES = ["MT_Blowhole", "MT_Break", "MT_Crack", "MT_Fray", "MT_Uneven"]
MIN_AREA = 9

donors = set()
for r in csv.DictReader(open(SDG_CSV)):
    m = re.match(r"(exp\d+_num_\d+)_mask", r["mask_filename"].split("/")[-1])
    if m:
        donors.add(m.group(1))

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

def new_coco():
    return {"info": {"description": "Magnetic Tile real defects"},
            "licenses": [{"id": 1, "name": "research"}],
            "categories": [{"id": i + 1, "name": c, "supercategory": "defect"}
                           for i, c in enumerate(CLASSES)],
            "images": [], "annotations": []}

full, unseen = new_coco(), new_coco()
counters = {"full": Counter(), "unseen": Counter()}
img_id = ann_full = ann_unseen = 0
empty = 0
for cls in CLASSES:
    for jpg in sorted((SRC / cls / "Imgs").glob("*.jpg")):
        mask_path = jpg.with_suffix(".png")
        m = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        assert m is not None, mask_path
        n, _, stats, _ = cv2.connectedComponentsWithStats((m > 127).astype(np.uint8), 8)
        boxes = [[int(x), int(y), int(w), int(h)]
                 for x, y, w, h, area in stats[1:] if area >= MIN_AREA]
        if not boxes:
            empty += 1
            continue
        fname = f"{cls}+{jpg.name}"
        shutil.copy(jpg, OUT / fname)
        img_id += 1
        H, W = m.shape
        img_entry = {"id": img_id, "file_name": fname, "width": W, "height": H}
        is_unseen = jpg.stem not in donors
        full["images"].append(img_entry)
        counters["full"][cls] += 1
        if is_unseen:
            unseen["images"].append(img_entry)
            counters["unseen"][cls] += 1
        for x, y, w, h in boxes:
            ann_full += 1
            ann = {"id": ann_full, "image_id": img_id,
                   "category_id": CLASSES.index(cls) + 1,
                   "bbox": [x, y, w, h], "area": w * h, "iscrowd": 0}
            full["annotations"].append(ann)
            if is_unseen:
                ann_unseen += 1
                unseen["annotations"].append({**ann, "id": ann_unseen})

with open(OUT / "_annotations.coco.json", "w") as f:
    json.dump(full, f)
with open(OUT / "_annotations_unseen.coco.json", "w") as f:
    json.dump(unseen, f)
print(f"full: {len(full['images'])} imgs / {ann_full} boxes | {dict(counters['full'])}")
print(f"unseen: {len(unseen['images'])} imgs / {ann_unseen} boxes | {dict(counters['unseen'])}")
print(f"empty masks skipped: {empty}")
