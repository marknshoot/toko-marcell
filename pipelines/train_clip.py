#!/usr/bin/env python3
"""
PyTorch Vision-Language Model (VLM) Fine-Tuning Pipeline: Domain-Adapting CLIP on Fashion Catalog.

Target Alignment: AI Engineer (Multimodal AI / LLM) — PT. Kalbe Farma, Tbk.

Key Features:
- Contrastive dual-encoder fine-tuning using openai/clip-vit-base-patch32
- Symmetric InfoNCE loss with in-batch negatives: L = 0.5 * (L_i2t + L_t2i)
- Optional Category-Aware Hard Negative BatchSampler for fine-grained garment discrimination
- Mixed precision (torch.amp.autocast) + Gradient Scaling for memory efficiency
- Cosine Annealing learning rate schedule with 10% linear warmup
- Validation Recall@1 tracking and best-checkpoint saving
- Works on local GPU / CPU as well as Google Colab T4 GPU

Run:
  python manual/pipelines/train_clip.py --batch-size 32 --epochs 3 --lr 5e-6
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
from typing import List, Dict, Any

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

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data" / "processed"
DEFAULT_SAVE_DIR = BASE_DIR / "models" / "fashion_clip"
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
            # Fallback for transient read issues
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
    """
    Groups examples from similar fashion categories into the same mini-batch.
    Forces the contrastive loss to learn fine-grained garment discriminators (hard negatives)
    rather than trivial background or color cues.
    """

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

            # Slice into full batches
            for i in range(0, len(indices) - len(indices) % self.batch_size, self.batch_size):
                batches.append(indices[i : i + self.batch_size])

            # Remaining items that don't fill a full batch
            rem = len(indices) % self.batch_size
            if rem > 0:
                overflow.extend(indices[len(indices) - rem :])

        if self.shuffle:
            random.shuffle(overflow)

        for i in range(0, len(overflow) - len(overflow) % self.batch_size, self.batch_size):
            batches.append(overflow[i : i + self.batch_size])

        # If any leftovers remain, append them as a smaller final batch
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


def compute_infonce_loss(
    model: CLIPModel,
    input_ids: torch.Tensor,
    attention_mask: torch.Tensor,
    pixel_values: torch.Tensor,
    device: torch.device,
) -> tuple[torch.Tensor, float, float]:
    """Computes symmetric InfoNCE contrastive loss over dual normalized feature towers."""
    image_out = model.get_image_features(pixel_values=pixel_values)
    image_features = image_out.pooler_output if hasattr(image_out, "pooler_output") else image_out

    text_out = model.get_text_features(input_ids=input_ids, attention_mask=attention_mask)
    text_features = text_out.pooler_output if hasattr(text_out, "pooler_output") else text_out

    # Normalize vectors to unit hypersphere
    image_features = image_features / (image_features.norm(dim=-1, keepdim=True) + 1e-8)
    text_features = text_features / (text_features.norm(dim=-1, keepdim=True) + 1e-8)

    # Scaled cosine similarity
    logit_scale = model.logit_scale.exp().clamp(max=100.0)
    logits_per_image = logit_scale * torch.matmul(image_features, text_features.t())
    logits_per_text = logits_per_image.t()

    batch_size = image_features.shape[0]
    ground_truth = torch.arange(batch_size, dtype=torch.long, device=device)

    loss_i2t = F.cross_entropy(logits_per_image, ground_truth)
    loss_t2i = F.cross_entropy(logits_per_text, ground_truth)
    total_loss = (loss_i2t + loss_t2i) / 2.0

    # In-batch text-to-image Recall@1 accuracy
    with torch.no_grad():
        preds = logits_per_text.argmax(dim=-1)
        r1_acc = (preds == ground_truth).float().mean().item()

    return total_loss, loss_i2t.item(), r1_acc


def evaluate_val_set(
    model: CLIPModel,
    loader: DataLoader,
    device: torch.device,
    use_amp: bool,
) -> tuple[float, float]:
    """Runs evaluation on validation loader, returning average loss and Recall@1."""
    model.eval()
    total_loss = 0.0
    total_r1 = 0.0
    n_batches = 0

    with torch.no_grad():
        for batch in loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            pixel_values = batch["pixel_values"].to(device)

            with torch.amp.autocast(device_type="cuda", enabled=use_amp):
                loss, _, r1 = compute_infonce_loss(
                    model, input_ids, attention_mask, pixel_values, device
                )

            total_loss += loss.item()
            total_r1 += r1
            n_batches += 1

    avg_loss = total_loss / max(n_batches, 1)
    avg_r1 = total_r1 / max(n_batches, 1)
    return avg_loss, avg_r1


def main():
    parser = argparse.ArgumentParser(description="Fine-tune CLIP VLM on Toko Marcell Catalog")
    parser.add_argument("--data-dir", type=str, default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--save-dir", type=str, default=str(DEFAULT_SAVE_DIR))
    parser.add_argument("--model-name", type=str, default=DEFAULT_MODEL_NAME)
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size (e.g. 16/32 local, 64 Colab)")
    parser.add_argument("--epochs", type=int, default=3, help="Training epochs (typically 3-5)")
    parser.add_argument("--lr", type=float, default=5e-6, help="Learning rate (default 5e-6)")
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--hard-negatives", action="store_true", default=True, help="Use CategoryBatchSampler")
    parser.add_argument("--device", type=str, default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--max-train-samples", type=int, default=0, help="Optional limit for dry runs")
    args = parser.parse_args()

    device = torch.device(args.device)
    use_amp = (device.type == "cuda")

    log(f"=== Starting VLM Contrastive Fine-Tuning ===")
    log(f"Device: {device} | Mixed Precision (AMP): {use_amp}")
    log(f"Base Model: {args.model_name}")
    log(f"Hyperparameters: Batch={args.batch_size}, Epochs={args.epochs}, LR={args.lr}")

    data_dir = Path(args.data_dir)
    train_file = data_dir / "clip_train.json"
    val_file = data_dir / "clip_val.json"

    if not train_file.exists() or not val_file.exists():
        log(f"Error: {train_file} or {val_file} not found. Run prepare_clip_dataset.py first!")
        sys.exit(1)

    with open(train_file, "r", encoding="utf-8") as f:
        train_data = json.load(f)
    with open(val_file, "r", encoding="utf-8") as f:
        val_data = json.load(f)

    if args.max_train_samples > 0:
        train_data = train_data[: args.max_train_samples]
        val_data = val_data[: min(len(val_data), args.max_train_samples // 4)]
        log(f"Truncated for dry run: train={len(train_data)}, val={len(val_data)}")
    else:
        log(f"Loaded train pairs: {len(train_data)} | val pairs: {len(val_data)}")

    log("Loading CLIP Processor and Base Model...")
    processor = CLIPProcessor.from_pretrained(args.model_name)
    model = CLIPModel.from_pretrained(args.model_name, use_safetensors=True).to(device)

    train_dataset = FashionVLMDataset(train_data, processor)
    val_dataset = FashionVLMDataset(val_data, processor)

    if args.hard_negatives:
        log("Using Category-Aware BatchSampler for Hard Negative Mining...")
        train_sampler = CategoryAwareBatchSampler(train_data, batch_size=args.batch_size, shuffle=True)
        train_loader = DataLoader(
            train_dataset,
            batch_sampler=train_sampler,
            num_workers=2 if os.name != "nt" else 0,
            pin_memory=(device.type == "cuda"),
        )
    else:
        train_loader = DataLoader(
            train_dataset,
            batch_size=args.batch_size,
            shuffle=True,
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

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.lr,
        weight_decay=args.weight_decay,
        betas=(0.9, 0.98),
        eps=1e-6,
    )

    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_steps,
    )

    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    best_val_loss = float("inf")
    best_val_r1 = 0.0
    history = []

    log(f"Training across {args.epochs} epochs ({total_steps} total steps, {warmup_steps} warmup)...")
    start_time = time.perf_counter()

    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        running_r1 = 0.0
        step_count = 0
        t0 = time.perf_counter()

        for step, batch in enumerate(train_loader, 1):
            optimizer.zero_grad()

            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            pixel_values = batch["pixel_values"].to(device)

            with torch.amp.autocast(device_type="cuda", enabled=use_amp):
                loss, _, r1_acc = compute_infonce_loss(
                    model, input_ids, attention_mask, pixel_values, device
                )

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
            step_count += 1

            if step % 25 == 0 or step == len(train_loader):
                cur_lr = scheduler.get_last_lr()[0]
                log(
                    f"Epoch [{epoch}/{args.epochs}] Step [{step}/{len(train_loader)}] "
                    f"Loss: {running_loss/step_count:.4f} | Batch R@1: {running_r1/step_count*100:.1f}% | lr: {cur_lr:.2e}"
                )

        train_loss = running_loss / max(step_count, 1)
        train_r1 = running_r1 / max(step_count, 1)

        # Validation pass
        val_loss, val_r1 = evaluate_val_set(model, val_loader, device, use_amp)
        elapsed = time.perf_counter() - t0

        log(
            f"--> Epoch {epoch} Complete ({elapsed:.1f}s) | "
            f"Train Loss: {train_loss:.4f}, Train R@1: {train_r1*100:.1f}% | "
            f"Val Loss: {val_loss:.4f}, Val R@1: {val_r1*100:.1f}%"
        )

        history.append({
            "epoch": epoch,
            "train_loss": round(train_loss, 4),
            "train_r1": round(train_r1, 4),
            "val_loss": round(val_loss, 4),
            "val_r1": round(val_r1, 4),
            "time_seconds": round(elapsed, 1),
        })

        # Save checkpoint if best val metric
        if val_r1 > best_val_r1 or (val_r1 == best_val_r1 and val_loss < best_val_loss):
            best_val_r1 = val_r1
            best_val_loss = val_loss
            log(f"★ New best model achieved (Val R@1 = {val_r1*100:.1f}%)! Saving checkpoint...")
            model.save_pretrained(save_dir, safe_serialization=True)
            processor.save_pretrained(save_dir)

            torch.save(
                {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "val_loss": val_loss,
                    "val_r1": val_r1,
                    "args": vars(args),
                },
                save_dir / "fashion_clip_checkpoint.pt",
            )

    total_time = time.perf_counter() - start_time
    log(f"Training completed in {total_time/60:.2f} minutes.")
    log(f"Best Val R@1: {best_val_r1*100:.1f}% | Saved weights to {save_dir}")

    # Save training history summary
    history_file = save_dir / "training_history.json"
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "base_model": args.model_name,
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "learning_rate": args.lr,
                "best_val_r1": best_val_r1,
                "best_val_loss": best_val_loss,
                "total_time_seconds": round(total_time, 2),
                "history": history,
            },
            f,
            indent=2,
        )
    log(f"Saved training history to {history_file}")


if __name__ == "__main__":
    main()
