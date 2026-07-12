#!/usr/bin/env python3
"""Finetune RF-DETR Large on a DIG defect dataset (metal or glass)."""
import argparse

from rfdetr import RFDETRLarge

ROOT = "/home/ubuntu/defects-gen/finetune-rf-detr"
DIRS = {
    "metal": (f"{ROOT}/dataset", f"{ROOT}/output"),
    "glass": (f"{ROOT}/dataset_glass", f"{ROOT}/output_glass"),
}
p = argparse.ArgumentParser()
p.add_argument("--usecase", choices=DIRS, default="metal")
dataset_dir, output_dir = DIRS[p.parse_args().usecase]

model = RFDETRLarge()
model.train(
    dataset_dir=dataset_dir,
    output_dir=output_dir,
    epochs=40,
    batch_size=16,
    grad_accum_steps=1,
    early_stopping=True,
    early_stopping_patience=10,
)
