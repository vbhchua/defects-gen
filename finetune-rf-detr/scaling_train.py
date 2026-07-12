#!/usr/bin/env python3
"""Train one RF-DETR Large model for the data-scaling study (same recipe as the
main metal/glass finetunes, for comparability)."""
import argparse

from rfdetr import RFDETRLarge

ROOT = "/home/ubuntu/defects-gen/finetune-rf-detr/scaling"

p = argparse.ArgumentParser()
p.add_argument("--n", type=int, required=True, choices=[120, 240, 360])
n = p.parse_args().n

model = RFDETRLarge()
model.train(
    dataset_dir=f"{ROOT}/dataset_{n}",
    output_dir=f"{ROOT}/output_{n}",
    epochs=40,
    batch_size=16,
    grad_accum_steps=1,
    early_stopping=True,
    early_stopping_patience=10,
)
