#!/usr/bin/env python3
"""Build a real-world COCO test set for the GLASS model from the original
Roboflow mobile-screen export (the dataset that seeded DIG's glass use case).

- Source: scratchpad glass_zip/mobile_screen/train (320 imgs, COCO boxes).
- Keeps images with >=1 Oil/Stain/scratch box; drops 'OK' (defect-free) boxes.
- Remaps categories to the model's training ids: oil=1, scratch=2, stain=3.
- Second annotation file excludes the 15 mask/anomaly donor images DIG used.
"""
import json
import shutil
from collections import Counter
from pathlib import Path

SRC = Path("/tmp/claude-1000/-home-ubuntu/a5fdb7c7-f345-4fb1-8852-359ccc336754/scratchpad/glass_zip/mobile_screen/train")
OUT = Path("/home/ubuntu/defects-gen/finetune-rf-detr/real_test_glass")
DONORS = set(Path("/tmp/claude-1000/-home-ubuntu/a5fdb7c7-f345-4fb1-8852-359ccc336754/scratchpad/glass_donors.txt").read_text().split())
CLASSES = ["oil", "scratch", "stain"]          # model training order (ids 1..3)
SRC_TO_MODEL = {"Oil": "oil", "scratch": "scratch", "Stain": "stain"}

src = json.load(open(SRC / "_annotations.coco.json"))
src_cats = {c["id"]: c["name"] for c in src["categories"]}
anns_by_img = {}
for a in src["annotations"]:
    name = src_cats[a["category_id"]]
    if name in SRC_TO_MODEL:
        anns_by_img.setdefault(a["image_id"], []).append((SRC_TO_MODEL[name], a["bbox"]))

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

def new_coco():
    return {"info": {"description": "Roboflow mobile-screen real defects"},
            "licenses": [{"id": 1, "name": "roboflow-export"}],
            "categories": [{"id": i + 1, "name": c, "supercategory": "defect"}
                           for i, c in enumerate(CLASSES)],
            "images": [], "annotations": []}

full, unseen = new_coco(), new_coco()
cnt = {"full": Counter(), "unseen": Counter()}
img_id = af = au = 0
for im in src["images"]:
    boxes = anns_by_img.get(im["id"], [])
    if not boxes:
        continue
    stem = im["file_name"].split("_jpg")[0].split("_png")[0]
    is_unseen = stem not in DONORS
    shutil.copy(SRC / im["file_name"], OUT / im["file_name"])
    img_id += 1
    entry = {"id": img_id, "file_name": im["file_name"],
             "width": im["width"], "height": im["height"]}
    full["images"].append(entry)
    if is_unseen:
        unseen["images"].append(entry)
    for cls, bbox in boxes:
        af += 1
        ann = {"id": af, "image_id": img_id, "category_id": CLASSES.index(cls) + 1,
               "bbox": bbox, "area": bbox[2] * bbox[3], "iscrowd": 0}
        full["annotations"].append(ann)
        cnt["full"][cls] += 1
        if is_unseen:
            au += 1
            unseen["annotations"].append({**ann, "id": au})
            cnt["unseen"][cls] += 1

with open(OUT / "_annotations.coco.json", "w") as f:
    json.dump(full, f)
with open(OUT / "_annotations_unseen.coco.json", "w") as f:
    json.dump(unseen, f)
print(f"full: {len(full['images'])} imgs / {af} boxes | {dict(cnt['full'])}")
print(f"unseen: {len(unseen['images'])} imgs / {au} boxes | {dict(cnt['unseen'])}")
