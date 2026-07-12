#!/usr/bin/env python3
"""Evaluate finetuned RF-DETR Large on the held-out test split.

Produces: metrics JSON (COCOeval overall + per-class, on valid and test),
confusion matrix, latency benchmark, and GT-vs-prediction visualizations.
"""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import supervision as sv
from PIL import Image
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
from rfdetr import RFDETRLarge

ROOT = Path("/home/ubuntu/defects-gen/finetune-rf-detr")
_p = argparse.ArgumentParser()
_p.add_argument("--usecase", choices=["metal", "glass"], default="metal")
_suffix = "" if _p.parse_args().usecase == "metal" else "_glass"
DATASET = ROOT / f"dataset{_suffix}"
OUTPUT = ROOT / f"output{_suffix}"
EVAL = ROOT / f"eval{_suffix}"
EVAL.mkdir(exist_ok=True)


def coco_eval(model, split):
    """Run COCOeval on a split; returns overall + per-class metrics."""
    coco = COCO(str(DATASET / split / "_annotations.coco.json"))
    cat_ids = sorted(coco.getCatIds())
    cat_names = {c["id"]: c["name"] for c in coco.loadCats(cat_ids)}
    results = []
    for img_info in coco.loadImgs(coco.getImgIds()):
        image = Image.open(DATASET / split / img_info["file_name"])
        det = model.predict(image, threshold=0.0)
        for (x1, y1, x2, y2), score, cid in zip(det.xyxy, det.confidence, det.class_id):
            results.append({
                "image_id": img_info["id"],
                # model.predict returns 0-indexed class ids; COCO cats are 1..5
                "category_id": int(cid) + 1,
                "bbox": [float(x1), float(y1), float(x2 - x1), float(y2 - y1)],
                "score": float(score),
            })
    coco_dt = coco.loadRes(results)
    ev = COCOeval(coco, coco_dt, "bbox")
    ev.evaluate(); ev.accumulate(); ev.summarize()
    stats = ev.stats
    per_class = {}
    # precision dims: [iou, recall, cls, area, maxdet]
    for k, cid in enumerate(cat_ids):
        p_all = ev.eval["precision"][:, :, k, 0, -1]
        p_50 = ev.eval["precision"][0, :, k, 0, -1]
        r_all = ev.eval["recall"][:, k, 0, -1]
        per_class[cat_names[cid]] = {
            "AP50_95": float(np.mean(p_all[p_all > -1])) if (p_all > -1).any() else float("nan"),
            "AP50": float(np.mean(p_50[p_50 > -1])) if (p_50 > -1).any() else float("nan"),
            "AR": float(np.mean(r_all[r_all > -1])) if (r_all > -1).any() else float("nan"),
            "gt_boxes": len(coco.getAnnIds(catIds=[cid])),
        }
    return {
        "AP50_95": stats[0], "AP50": stats[1], "AP75": stats[2],
        "AP_small": stats[3], "AP_medium": stats[4], "AP_large": stats[5],
        "AR_100": stats[8], "per_class": per_class,
        "images": len(coco.getImgIds()), "gt_boxes": len(coco.getAnnIds()),
    }


def confusion_matrix(model, split, conf=0.5):
    ds = sv.DetectionDataset.from_coco(
        str(DATASET / split), str(DATASET / split / "_annotations.coco.json"))
    preds, targets = [], []
    for path, _, ann in ds:
        det = model.predict(Image.open(path), threshold=conf)
        preds.append(det)  # class ids already 0-indexed, matching ds.classes
        targets.append(ann)
    cm = sv.ConfusionMatrix.from_detections(
        predictions=preds, targets=targets, classes=ds.classes,
        conf_threshold=conf, iou_threshold=0.5)
    cm.plot(save_path=str(EVAL / f"confusion_matrix_{split}.png"))
    return cm.matrix.tolist(), ds.classes


def latency(model, split, n=50):
    files = sorted((DATASET / split).glob("*.png"))[:n]
    imgs = [Image.open(f) for f in files]
    for im in imgs[:10]:
        model.predict(im, threshold=0.5)  # warmup
    t0 = time.perf_counter()
    for im in imgs:
        model.predict(im, threshold=0.5)
    dt = (time.perf_counter() - t0) / len(imgs)
    return dt * 1000


def visualize(model, split, n=8, conf=0.5):
    ds = sv.DetectionDataset.from_coco(
        str(DATASET / split), str(DATASET / split / "_annotations.coco.json"))
    box_a = sv.BoxAnnotator(thickness=2)
    lab_a = sv.LabelAnnotator(text_scale=0.45)
    rng = np.random.default_rng(0)
    idxs = rng.choice(len(ds), size=min(n, len(ds)), replace=False)
    rows = []
    for i in idxs:
        path, img, ann = ds[int(i)]
        det = model.predict(Image.open(path), threshold=conf)
        gt = box_a.annotate(img.copy(), ann)
        gt = lab_a.annotate(gt, ann, [ds.classes[c] for c in ann.class_id])
        pr = box_a.annotate(img.copy(), det)
        pr = lab_a.annotate(pr, det, [f"{ds.classes[c]} {s:.2f}"
                                      for c, s in zip(det.class_id, det.confidence)])
        pair = np.hstack([gt, np.full((gt.shape[0], 8, 3), 255, np.uint8), pr])
        pair = cv2.resize(pair, (720, int(720 * pair.shape[0] / pair.shape[1])))
        rows.append(pair)
    H = max(r.shape[0] for r in rows)
    rows = [cv2.copyMakeBorder(r, 0, H - r.shape[0], 0, 0, cv2.BORDER_CONSTANT,
                               value=(255, 255, 255)) for r in rows]
    half = (len(rows) + 1) // 2
    def stack(rs):
        return np.vstack([np.vstack([r, np.full((8, r.shape[1], 3), 255, np.uint8)]) for r in rs])
    grid = np.hstack([stack(rows[:half]), stack(rows[half:])]) if len(rows) > 1 else rows[0]
    cv2.imwrite(str(EVAL / f"predictions_{split}.png"), grid)


def main():
    # pick best checkpoint by stored val metric: prefer EMA if better
    ckpts = {p.name: p for p in OUTPUT.glob("checkpoint_best*.pth")}
    print("checkpoints:", list(ckpts))
    ckpt = ckpts.get("checkpoint_best_total.pth") or ckpts.get("checkpoint_best_ema.pth") \
        or ckpts["checkpoint_best_regular.pth"]
    print("using:", ckpt)
    model = RFDETRLarge(pretrain_weights=str(ckpt))
    model.optimize_for_inference()

    metrics = {"checkpoint": ckpt.name}
    for split in ["valid", "test"]:
        print(f"\n=== COCOeval on {split} ===")
        metrics[split] = coco_eval(model, split)

    print("\n=== confusion matrix (test, conf=0.5, IoU=0.5) ===")
    cm, classes = confusion_matrix(model, "test")
    metrics["confusion_matrix_test"] = {"classes": classes, "matrix": cm}

    print("\n=== latency ===")
    ms = latency(model, "test")
    metrics["latency_ms_per_image"] = ms
    print(f"{ms:.1f} ms/image")

    visualize(model, "test")
    with open(EVAL / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    print("\nsaved eval/metrics.json")


if __name__ == "__main__":
    main()
