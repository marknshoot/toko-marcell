#!/usr/bin/env python3
"""
Toko Marcell Multimodal VLM: Advanced Contrastive Fine-Tuning Experiments.

Implements the 4 experiments derived from literature review:
1. 'baseline'     : Standard InfoNCE Full Fine-Tuning (Chia et al., 2022)
2. 'lora'         : Parameter-Efficient Fine-Tuning with LoRA (Hu et al., 2021)
3. 'decoupled_lr' : Asymmetric Learning Rates + Visual Layer Freezing (WiSE-FT)
4. 'siglip'       : Pairwise Sigmoid Loss Function (Zhai et al., Google DeepMind, 2023)

Supports local GPU/CPU execution and Kaggle GPU (T4/P100) acceleration.

Usage:
  # Run LoRA experiment
  python manual/pipelines/experiments_clip.py --exp lora --batch-size 32 --epochs 3

  # Run SigLIP loss experiment
  python manual/pipelines/experiments_clip.py --exp siglip --batch-size 32 --epochs 3
"""

import argparse
import collections
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import Dataset, DataLoader, Sampler
from transformers import (
    CLIPModel,
    CLIPProcessor,
    get_cosine_schedule_with_warmup,
)

try:
    from peft import LoraConfig, get_peft_model, TaskType
    PEFT_AVAILABLE = True
except ImportError:
    PEFT_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data" / "processed"
DEFAULT_SAVE_ROOT = BASE_DIR / "models" / "experiments"
DEFAULT_MODEL_NAME = "openai/clip-vit-base-patch32"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


class FashionVLMDataset(Dataset):
    """PyTorch Dataset loading paired local catalog images and fashion captions."""

    def __init__(self, data: List[Dict[str, Any]], processor: CLIPProcessor, max_length: int = 64):
        self.data = data
        self.processor = processor
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        item = self.data[idx]
        image_path = item["image_path"]

        try:
            image = Image.open(image_path).convert("RGB")
        except Exception:
            image = Image.new("RGB", (224, 224), color=(240, 240, 240))

        caption = item.get("caption") or item.get("title") or "Fashion product"

        inputs = self.processor(
            text=[caption],
            images=image,
            return_tensors="pt",
            padding="max_length",
            truncation=True,
            max_length=self.max_length,
        )

        return {
            "asin": item.get("asin", ""),
            "category": item.get("category", "General"),
            "input_ids": inputs["input_ids"].squeeze(0),
            "attention_mask": inputs["attention_mask"].squeeze(0),
            "pixel_values": inputs["pixel_values"].squeeze(0),
        }


class CategoryAwareBatchSampler(Sampler):
    """Groups items from identical categories into mini-batches to generate hard negatives."""

    def __init__(self, data: List[Dict[str, Any]], batch_size: int, shuffle: bool = True):
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.category_to_indices = collections.defaultdict(list)

        for idx, item in enumerate(data):
            cat = item.get("category", "Other")
            self.category_to_indices[cat].append(idx)

        self.batches = self._create_batches()

    def _create_batches(self) -> List[List[int]]:
        batches = []
        overflow = []
        cat_keys = list(self.category_to_indices.keys())
        if self.shuffle:
            random.shuffle(cat_keys)

        for cat in cat_keys:
            indices = list(self.category_to_indices[cat])
            if self.shuffle:
                random.shuffle(indices)

            for i in range(0, len(indices) - len(indices) % self.batch_size, self.batch_size):
                batches.append(indices[i : i + self.batch_size])

            rem = len(indices) % self.batch_size
            if rem > 0:
                overflow.extend(indices[len(indices) - rem :])

        if self.shuffle:
            random.shuffle(overflow)

        for i in range(0, len(overflow) - len(overflow) % self.batch_size, self.batch_size):
            batches.append(overflow[i : i + self.batch_size])

        final_rem = len(overflow) % self.batch_size
        if final_rem > 0:
            batches.append(overflow[len(overflow) - final_rem :])

        if self.shuffle:
            random.shuffle(batches)

        return batches

    def __iter__(self):
        self.batches = self._create_batches()
        for batch in self.batches:
            yield batch

    def __len__(self) -> int:
        return len(self.batches)


class SigLIPLoss(nn.Module):
    """
    Sigmoid Loss for Language Image Pre-Training (SigLIP; Zhai et al., ICCV 2023).
    Replaces multi-class softmax InfoNCE with pairwise binary cross-entropy,
    eliminating competition between negatives and stabilizing small-batch dynamics.
    """

    def __init__(self, init_temp: float = 10.0, init_bias: float = -10.0):
        super().__init__()
        self.temp = nn.Parameter(torch.tensor(math.log(init_temp)))
        self.bias = nn.Parameter(torch.tensor(init_bias))

    def forward(self, image_features: torch.Tensor, text_features: torch.Tensor) -> Tuple[torch.Tensor, float]:
        # Pairwise dot product [B, B]
        logits = torch.matmul(image_features, text_features.t()) * self.temp.exp() + self.bias
        batch_size = image_features.shape[0]

        # Target matrix: +1 on diagonal (matching pairs), -1 on off-diagonal
        y = 2.0 * torch.eye(batch_size, device=image_features.device) - 1.0

        # Pairwise sigmoid binary cross entropy: -log(sigmoid(y * logits)) = log(1 + exp(-y * logits))
        loss = F.softplus(-y * logits).mean()

        # In-batch text-to-image Recall@1
        with torch.no_grad():
            preds = logits.t().argmax(dim=-1)
            ground_truth = torch.arange(batch_size, device=image_features.device)
            r1_acc = (preds == ground_truth).float().mean().item()

        return loss, r1_acc


def extract_normalized_features(
    model: nn.Module,
    input_ids: torch.Tensor,
    attention_mask: torch.Tensor,
    pixel_values: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Helper to extract and unit-normalize dual representations across wrapped/LoRA models."""
    raw_model = model.module if hasattr(model, "module") else model
    if hasattr(raw_model, "get_image_features"):
        base = raw_model
    elif hasattr(raw_model, "base_model") and hasattr(raw_model.base_model, "get_image_features"):
        base = raw_model.base_model
    elif hasattr(raw_model, "model") and hasattr(raw_model.model, "get_image_features"):
        base = raw_model.model
    else:
        base = raw_model

    img_out = base.get_image_features(pixel_values=pixel_values)
    img_feats = img_out.pooler_output if hasattr(img_out, "pooler_output") else img_out

    txt_out = base.get_text_features(input_ids=input_ids, attention_mask=attention_mask)
    txt_feats = txt_out.pooler_output if hasattr(txt_out, "pooler_output") else txt_out

    img_feats = img_feats / (img_feats.norm(dim=-1, keepdim=True) + 1e-8)
    txt_feats = txt_feats / (txt_feats.norm(dim=-1, keepdim=True) + 1e-8)
    return img_feats, txt_feats


def compute_infonce_loss(
    model: nn.Module,
    image_features: torch.Tensor,
    text_features: torch.Tensor,
    device: torch.device,
) -> Tuple[torch.Tensor, float]:
    """Computes standard symmetric InfoNCE contrastive loss."""
    raw_model = model.module if hasattr(model, "module") else model
    if hasattr(raw_model, "logit_scale"):
        logit_scale = raw_model.logit_scale.exp().clamp(max=100.0)
    elif hasattr(raw_model, "base_model") and hasattr(raw_model.base_model, "logit_scale"):
        logit_scale = raw_model.base_model.logit_scale.exp().clamp(max=100.0)
    else:
        logit_scale = torch.tensor(14.0, device=device)

    logits_per_image = logit_scale * torch.matmul(image_features, text_features.t())
    logits_per_text = logits_per_image.t()

    batch_size = image_features.shape[0]
    ground_truth = torch.arange(batch_size, dtype=torch.long, device=device)

    loss_i2t = F.cross_entropy(logits_per_image, ground_truth)
    loss_t2i = F.cross_entropy(logits_per_text, ground_truth)
    total_loss = (loss_i2t + loss_t2i) / 2.0

    with torch.no_grad():
        preds = logits_per_text.argmax(dim=-1)
        r1_acc = (preds == ground_truth).float().mean().item()

    return total_loss, r1_acc


def setup_experiment_model(
    exp_name: str,
    base_model_name: str,
    lr: float,
    device: torch.device,
) -> Tuple[nn.Module, CLIPProcessor, torch.optim.Optimizer, Any]:
    """Configures the model architecture, parameter freezing, and optimizer for the given experiment."""
    processor = CLIPProcessor.from_pretrained(base_model_name)
    model = CLIPModel.from_pretrained(base_model_name, use_safetensors=True).to(device)
    loss_module = None

    if exp_name == "lora":
        if not PEFT_AVAILABLE:
            raise ImportError("peft package is required for LoRA experiment. Run `pip install peft`.")
        log("Configuring LoRA (r=16, alpha=32) on Multi-Head Attention projections...")
        peft_config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "v_proj"],
            lora_dropout=0.1,
            bias="none",
        )
        model = get_peft_model(model, peft_config)
        trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total_params = sum(p.numel() for p in model.parameters())
        log(f"LoRA Trainable Parameters: {trainable_params:,} / {total_params:,} ({trainable_params/total_params*100:.2f}%)")

        optimizer = torch.optim.AdamW(model.parameters(), lr=lr * 2.0, weight_decay=0.01)

    elif exp_name == "decoupled_lr":
        log("Freezing Vision Transformer Layers 0-5 and applying decoupled learning rates...")
        # Freeze first 6 layers of vision encoder
        for layer in model.vision_model.encoder.layers[:6]:
            for param in layer.parameters():
                param.requires_grad = False

        # Group parameters with differential learning rates
        vision_params = []
        text_params = []
        proj_params = []

        for name, param in model.named_parameters():
            if not param.requires_grad:
                continue
            if "visual_projection" in name or "text_projection" in name or "logit_scale" in name:
                proj_params.append(param)
            elif "vision_model" in name:
                vision_params.append(param)
            else:
                text_params.append(param)

        optimizer = torch.optim.AdamW(
            [
                {"params": vision_params, "lr": lr * 0.2},   # 1e-6 (conservative visual updates)
                {"params": text_params, "lr": lr * 1.0},     # 5e-6 (text domain adaptation)
                {"params": proj_params, "lr": lr * 2.0},     # 1e-5 (latent alignment heads)
            ],
            weight_decay=0.01,
        )

    elif exp_name == "siglip":
        log("Initializing SigLIP Loss module with learnable temperature & bias...")
        loss_module = SigLIPLoss().to(device)
        optimizer = torch.optim.AdamW(
            list(model.parameters()) + list(loss_module.parameters()),
            lr=lr,
            weight_decay=0.01,
        )

    elif exp_name == "decoupled_siglip":
        log("Freezing Vision Transformer Layers 0-5 and coupling with SigLIP Loss...")
        for layer in model.vision_model.encoder.layers[:6]:
            for param in layer.parameters():
                param.requires_grad = False

        vision_params, text_params, proj_params = [], [], []
        for name, param in model.named_parameters():
            if not param.requires_grad:
                continue
            if "visual_projection" in name or "text_projection" in name or "logit_scale" in name:
                proj_params.append(param)
            elif "vision_model" in name:
                vision_params.append(param)
            else:
                text_params.append(param)

        loss_module = SigLIPLoss().to(device)
        optimizer = torch.optim.AdamW(
            [
                {"params": vision_params, "lr": lr * 0.2},
                {"params": text_params, "lr": lr * 1.0},
                {"params": proj_params, "lr": lr * 2.0},
                {"params": list(loss_module.parameters()), "lr": lr},
            ],
            weight_decay=0.01,
        )

    else:  # baseline
        log("Setting up standard InfoNCE Baseline (all parameters trainable)...")
        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)

    return model, processor, optimizer, loss_module


def evaluate_test_set(
    model: nn.Module,
    processor: CLIPProcessor,
    test_items: List[Dict[str, Any]],
    batch_size: int,
    device: torch.device,
) -> Dict[str, float]:
    """Runs zero-shot cross-modal retrieval benchmark on test items."""
    model.eval()
    all_img_embeds = []
    all_txt_embeds = []

    t0 = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(test_items), batch_size):
            batch = test_items[i : i + batch_size]
            images = [Image.open(item["image_path"]).convert("RGB") for item in batch]
            captions = [item.get("caption") or item.get("title") or "fashion" for item in batch]

            inputs = processor(
                text=captions,
                images=images,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=64,
            ).to(device)

            img_feats, txt_feats = extract_normalized_features(
                model, inputs["input_ids"], inputs["attention_mask"], inputs["pixel_values"]
            )
            all_img_embeds.append(img_feats.cpu().numpy())
            all_txt_embeds.append(txt_feats.cpu().numpy())

    total_sec = time.perf_counter() - t0
    img_mat = np.vstack(all_img_embeds)
    txt_mat = np.vstack(all_txt_embeds)

    sim_matrix = txt_mat @ img_mat.T
    n = len(sim_matrix)

    r1, r5, r10, rr_sum = 0, 0, 0, 0.0
    for i in range(n):
        scores = sim_matrix[i]
        sorted_indices = np.argsort(-scores)
        rank = int(np.where(sorted_indices == i)[0][0]) + 1

        if rank == 1:
            r1 += 1
        if rank <= 5:
            r5 += 1
        if rank <= 10:
            r10 += 1
        rr_sum += 1.0 / rank

    return {
        "recall_at_1": round(r1 / n, 4),
        "recall_at_5": round(r5 / n, 4),
        "recall_at_10": round(r10 / n, 4),
        "mrr": round(rr_sum / n, 4),
        "latency_ms": round((total_sec / n) * 1000, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Run VLM CLIP Experiments")
    parser.add_argument(
        "--exp",
        type=str,
        default="lora",
        choices=["baseline", "lora", "decoupled_lr", "siglip", "decoupled_siglip"],
        help="Experiment variant to run",
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--save-root", default=str(DEFAULT_SAVE_ROOT))
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=5e-6)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--max-train-samples", type=int, default=0, help="Optional truncation for dry runs")
    args = parser.parse_args()

    device = torch.device(args.device)
    use_amp = (device.type == "cuda")

    log(f"============================================================")
    log(f"  STARTING EXPERIMENT: {args.exp.upper()}  ")
    log(f"============================================================")
    log(f"Device: {device} | Batch: {args.batch_size} | Epochs: {args.epochs} | Base LR: {args.lr}")

    data_dir = Path(args.data_dir)
    train_file = data_dir / "clip_train.json"
    val_file = data_dir / "clip_val.json"
    test_file = data_dir / "clip_test.json"

    with open(train_file, "r", encoding="utf-8") as f:
        train_data = json.load(f)
    with open(val_file, "r", encoding="utf-8") as f:
        val_data = json.load(f)
    with open(test_file, "r", encoding="utf-8") as f:
        test_data = json.load(f)

    if args.max_train_samples > 0:
        train_data = train_data[: args.max_train_samples]
        val_data = val_data[: min(len(val_data), args.max_train_samples // 4)]
        test_data = test_data[: min(len(test_data), args.max_train_samples // 2)]
        log(f"Truncated for dry run: train={len(train_data)}, val={len(val_data)}, test={len(test_data)}")

    model, processor, optimizer, loss_module = setup_experiment_model(
        args.exp, DEFAULT_MODEL_NAME, args.lr, device
    )

    train_dataset = FashionVLMDataset(train_data, processor)
    val_dataset = FashionVLMDataset(val_data, processor)

    train_sampler = CategoryAwareBatchSampler(train_data, batch_size=args.batch_size, shuffle=True)
    train_loader = DataLoader(
        train_dataset,
        batch_sampler=train_sampler,
        num_workers=2 if os.name != "nt" else 0,
        pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=2 if os.name != "nt" else 0,
        pin_memory=(device.type == "cuda"),
    )

    total_steps = len(train_loader) * args.epochs
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * 0.1),
        num_training_steps=total_steps,
    )
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    save_dir = Path(args.save_root) / args.exp
    save_dir.mkdir(parents=True, exist_ok=True)

    best_val_r1 = 0.0
    history = []
    start_time = time.perf_counter()

    for epoch in range(1, args.epochs + 1):
        model.train()
        if loss_module:
            loss_module.train()

        running_loss = 0.0
        running_r1 = 0.0
        steps = 0
        t0 = time.perf_counter()

        for step, batch in enumerate(train_loader, 1):
            optimizer.zero_grad()
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            pixel_values = batch["pixel_values"].to(device)

            with torch.amp.autocast(device_type="cuda", enabled=use_amp):
                img_feats, txt_feats = extract_normalized_features(
                    model, input_ids, attention_mask, pixel_values
                )

                if args.exp == "siglip":
                    loss, r1_acc = loss_module(img_feats, txt_feats)
                else:
                    loss, r1_acc = compute_infonce_loss(model, img_feats, txt_feats, device)

            if use_amp:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()

            scheduler.step()
            running_loss += loss.item()
            running_r1 += r1_acc
            steps += 1

            if step % 25 == 0 or step == len(train_loader):
                log(f"Epoch [{epoch}/{args.epochs}] Step [{step}/{len(train_loader)}] Loss: {running_loss/steps:.4f} | R@1: {running_r1/steps*100:.1f}%")

        train_loss = running_loss / max(steps, 1)
        train_r1 = running_r1 / max(steps, 1)

        # Validation Pass
        model.eval()
        if loss_module:
            loss_module.eval()

        val_loss, val_r1_sum, v_steps = 0.0, 0.0, 0
        with torch.no_grad():
            for v_batch in val_loader:
                v_ids = v_batch["input_ids"].to(device)
                v_mask = v_batch["attention_mask"].to(device)
                v_pixels = v_batch["pixel_values"].to(device)

                with torch.amp.autocast(device_type="cuda", enabled=use_amp):
                    v_img, v_txt = extract_normalized_features(model, v_ids, v_mask, v_pixels)
                    if args.exp == "siglip":
                        vl, vr1 = loss_module(v_img, v_txt)
                    else:
                        vl, vr1 = compute_infonce_loss(model, v_img, v_txt, device)

                val_loss += vl.item()
                val_r1_sum += vr1
                v_steps += 1

        val_loss /= max(v_steps, 1)
        val_r1 = val_r1_sum / max(v_steps, 1)
        elapsed = time.perf_counter() - t0

        log(f"--> Epoch {epoch} Done ({elapsed:.1f}s) | Train Loss: {train_loss:.4f}, Train R@1: {train_r1*100:.1f}% | Val Loss: {val_loss:.4f}, Val R@1: {val_r1*100:.1f}%")

        history.append({
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "train_r1": round(train_r1, 4),
            "val_loss": round(val_loss, 4),
            "val_r1": round(val_r1, 4),
        })

        if val_r1 > best_val_r1:
            best_val_r1 = val_r1
            log(f"★ New Best for '{args.exp}' (Val R@1 = {val_r1*100:.1f}%)! Saving model...")
            if hasattr(model, "save_pretrained"):
                model.save_pretrained(save_dir, safe_serialization=True)
            processor.save_pretrained(save_dir)

    # Final Benchmark on Held-Out Test Set
    log("\n" + "=" * 50)
    log(f"RUNNING HELD-OUT TEST BENCHMARK FOR: {args.exp.upper()}")
    log("=" * 50)
    test_metrics = evaluate_test_set(model, processor, test_data, args.batch_size, device)

    print(f"\n[RESULTS FOR EXPERIMENT: {args.exp.upper()}]")
    print(f"Recall@1  : {test_metrics['recall_at_1']*100:.2f}%")
    print(f"Recall@5  : {test_metrics['recall_at_5']*100:.2f}%")
    print(f"Recall@10 : {test_metrics['recall_at_10']*100:.2f}%")
    print(f"MRR       : {test_metrics['mrr']:.4f}")
    print(f"Latency   : {test_metrics['latency_ms']} ms/query\n")

    summary_file = save_dir / "experiment_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "experiment": args.exp,
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "learning_rate": args.lr,
                "best_val_r1": best_val_r1,
                "test_metrics": test_metrics,
                "history": history,
            },
            f,
            indent=2,
        )
    log(f"Saved experiment summary to: {summary_file}")


if __name__ == "__main__":
    main()
