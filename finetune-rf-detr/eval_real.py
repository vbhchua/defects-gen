#!/usr/bin/env python3
"""Sanity-check the scaling-study models on the REAL Magnetic Tile test set.

Evaluates each trained checkpoint (120/240/360 per class, synthetic-only
training) on real photographs: full set and the subset whose mask shapes were
never used by the generation pipeline ("unseen").
"""
import gc
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
from rfdetr import RFDETRLarge

ROOT = Path("/home/ubuntu/defects-gen/finetune-rf-detr")
REAL = ROOT / "real_test"
SIZES = [120, 240, 360]


def coco_eval(model, ann_file):
    coco = COCO(str(ann_file))
    cat_ids = sorted(coco.getCatIds())
    cat_names = {c["id"]: c["name"] for c in coco.loadCats(cat_ids)}
    results = []
    for info in coco.loadImgs(coco.getImgIds()):
        img = Image.open(REAL / info["file_name"]).convert("RGB")
        det = model.predict(img, threshold=0.0)
        for (x1, y1, x2, y2), sc, cid in zip(det.xyxy, det.confidence, det.class_id):
            results.append({"image_id": info["id"], "category_id": int(cid) + 1,
                            "bbox": [float(x1), float(y1), float(x2 - x1), float(y2 - y1)],
                            "score": float(sc)})
    ev = COCOeval(coco, coco.loadRes(results), "bbox")
    ev.evaluate(); ev.accumulate(); ev.summarize()
    per_class = {}
    for k, cid in enumerate(cat_ids):
        p_all = ev.eval["precision"][:, :, k, 0, -1]
        p_50 = ev.eval["precision"][0, :, k, 0, -1]
        per_class[cat_names[cid]] = {
            "AP50_95": float(np.mean(p_all[p_all > -1])) if (p_all > -1).any() else None,
            "AP50": float(np.mean(p_50[p_50 > -1])) if (p_50 > -1).any() else None,
            "gt_boxes": len(coco.getAnnIds(catIds=[cid])),
        }
    return {"AP50_95": ev.stats[0], "AP50": ev.stats[1], "AP75": ev.stats[2],
            "AR_100": ev.stats[8], "per_class": per_class,
            "images": len(coco.getImgIds()), "gt_boxes": len(coco.getAnnIds())}


def main():
    out = {}
    for n in SIZES:
        ckpt_dir = ROOT / "scaling" / f"output_{n}"
        ckpts = {p.name: p for p in ckpt_dir.glob("checkpoint_best*.pth")}
        ckpt = ckpts.get("checkpoint_best_total.pth") or ckpts.get("checkpoint_best_ema.pth") \
            or ckpts["checkpoint_best_regular.pth"]
        print(f"\n##### model {n}/class ({ckpt.name}) #####")
        model = RFDETRLarge(pretrain_weights=str(ckpt))
        model.optimize_for_inference()
        out[str(n)] = {
            "checkpoint": ckpt.name,
            "real_full": coco_eval(model, REAL / "_annotations.coco.json"),
            "real_unseen": coco_eval(model, REAL / "_annotations_unseen.coco.json"),
        }
        del model
        gc.collect(); torch.cuda.empty_cache()
    with open(ROOT / "scaling" / "eval_real.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\nsaved scaling/eval_real.json")


if __name__ == "__main__":
    main()
