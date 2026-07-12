#!/usr/bin/env python3
"""Evaluate one scaling-study model on its own valid/test and on the COMMON
test set (dataset_360/test — same base groups, superset images) so the three
models are comparable on identical data."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
from rfdetr import RFDETRLarge

ROOT = Path("/home/ubuntu/defects-gen/finetune-rf-detr/scaling")

p = argparse.ArgumentParser()
p.add_argument("--n", type=int, required=True, choices=[120, 240, 360])
n = p.parse_args().n

# common test = test split of the LARGEST dataset built so far (superset images,
# same base groups — fair for every model since test bases are never trained on)
COMMON_TEST = sorted(ROOT.glob("dataset_*/test"),
                     key=lambda p: int(p.parent.name.split("_")[1]))[-1]


def coco_eval(model, split_dir):
    coco = COCO(str(split_dir / "_annotations.coco.json"))
    cat_ids = sorted(coco.getCatIds())
    cat_names = {c["id"]: c["name"] for c in coco.loadCats(cat_ids)}
    results = []
    for info in coco.loadImgs(coco.getImgIds()):
        det = model.predict(Image.open(split_dir / info["file_name"]), threshold=0.0)
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
            "AP_small": ev.stats[3], "AP_medium": ev.stats[4], "AP_large": ev.stats[5],
            "AR_100": ev.stats[8], "per_class": per_class,
            "images": len(coco.getImgIds()), "gt_boxes": len(coco.getAnnIds())}


def main():
    out = ROOT / f"output_{n}"
    ckpts = {p.name: p for p in out.glob("checkpoint_best*.pth")}
    ckpt = ckpts.get("checkpoint_best_total.pth") or ckpts.get("checkpoint_best_ema.pth") \
        or ckpts["checkpoint_best_regular.pth"]
    model = RFDETRLarge(pretrain_weights=str(ckpt))
    model.optimize_for_inference()

    metrics = {"n_per_class": n, "checkpoint": ckpt.name}
    metrics["common_test_source"] = COMMON_TEST.parent.name
    for label, split_dir in [
        ("valid", ROOT / f"dataset_{n}" / "valid"),
        ("test", ROOT / f"dataset_{n}" / "test"),
        ("common_test", COMMON_TEST),
    ]:
        print(f"=== {label} ===")
        metrics[label] = coco_eval(model, split_dir)

    files = sorted(COMMON_TEST.glob("*.png"))[:50]
    imgs = [Image.open(f) for f in files]
    for im in imgs[:10]:
        model.predict(im, threshold=0.5)
    t0 = time.perf_counter()
    for im in imgs:
        model.predict(im, threshold=0.5)
    metrics["latency_ms_per_image"] = (time.perf_counter() - t0) / len(imgs) * 1000

    with open(ROOT / f"eval_{n}.json", "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"saved scaling/eval_{n}.json")


if __name__ == "__main__":
    main()
