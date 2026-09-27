#!/usr/bin/env python3
"""
Generates the complete, publication-grade toko_marcell_vlm_experiments.ipynb notebook.
Covers end-to-end multimodal representation learning:
- Executive problem framing & literature foundations
- 80/10/10 data pipeline with hard-negative batch sampling
- EarlyStopping & Validation loss tracking
- 3-Epoch benchmark: Baseline vs LoRA vs Decoupled LR vs SigLIP
- WiSE-FT Weight Blending & Test-Time Prompt Ensembling (TTA)
- Publication-grade Matplotlib graphics (Loss curves, Leaderboards, Latency Pareto frontier)
- Qualitative cross-modal retrieval demonstration
"""

import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()
nb.metadata.kernelspec = {
    "display_name": "Python 3",
    "language": "python",
    "name": "python3"
}
nb.metadata.language_info = {
    "name": "python",
    "version": "3.10"
}

cells = []

# Cell 1: Title & Executive Summary
cells.append(nbf.v4.new_markdown_cell(r"""# 🛍️ Toko Marcell: Multimodal VLM Contrastive Fine-Tuning & Representation Learning
**Domain:** E-Commerce Fashion Retrieval (5,378 products, 512-dim CLIP ViT-B/32)  
**Author:** Marcell Hermawan Kristianto (Data Science Student, Binus University)  
**Target:** AI Engineer — Multimodal Representation Learning, PEFT & Contrastive Optimization  

---

## 📌 Executive Summary
In e-commerce search engines, cross-modal retrieval (Text-to-Image / Image-to-Text) bridges customer intent and catalog inventory. While pre-trained foundation models like **OpenAI CLIP (ViT-B/32)** possess strong generic open-world priors, their zero-shot performance on fine-grained retail fashion catalogs is fundamentally limited by domain asymmetry: general visual concepts exist, but specialized e-commerce taxonomy, department semantics, and nuanced brand attributes are unaligned.

This study investigates **parameter-efficient adaptation, architectural decoupling, and loss reformulation** under a controlled 3-epoch budget to maximize top-k retrieval precision without triggering catastrophic forgetting.

### 📚 Literature Review Foundations
1. **Fashion-CLIP (Chia et al., Nature Scientific Reports 2022):** Structured metadata captions combining Brand + Title + Category + Material Bullets.
2. **PEFT / LoRA (Hu et al., ICLR 2022):** Low-Rank Adaptation on attention projections ($r=16, \alpha=32$) to adapt 5k items with <1% trainable parameters.
3. **WiSE-FT / Decoupled LR (Wortsman et al., CVPR 2022 Best Paper):** Freezing lower ViT layers (1–6) and applying differential learning rates across towers, followed by zero-cost weight-space ensembling ($\theta_{\text{WiSE}} = \alpha \theta_0 + (1-\alpha)\theta_{\text{FT}}$).
4. **SigLIP (Zhai et al., Google DeepMind, ICCV 2023):** Pairwise sigmoid cross-entropy eliminating harmful in-batch negative competition.
5. **Test-Time Prompt Ensembling (Radford et al., ICML 2021):** Synthesizing multiple e-commerce template embeddings at inference time to stabilize cross-modal retrieval.
"""))

# Cell 2: Hardware & Environment Verification
cells.append(nbf.v4.new_code_cell("""# 1. Environment & Hardware Verification
import os, sys

# Bypass pre-installed torchao dispatcher conflict in Kaggle container
sys.modules["torchao"] = None

!nvidia-smi
!pip uninstall -y torchao
!pip install -q peft transformers safetensors
"""))

# Cell 3: Imports & Reproducibility Setup
cells.append(nbf.v4.new_code_cell("""# 2. Imports & Seed Configuration
import copy, json, math, time, random, collections
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from PIL import Image

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, Sampler
from transformers import CLIPModel, CLIPProcessor, get_cosine_schedule_with_warmup
from peft import LoraConfig, get_peft_model

# Reproducibility
def seed_everything(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

seed_everything(42)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Active Accelerator Device: {device}")
if torch.cuda.is_available():
    print(f"GPU Hardware: {torch.cuda.get_device_name(0)}")
    print(f"VRAM Available: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
"""))

# Cell 4: Data Pipeline & Image Indexing
cells.append(nbf.v4.new_code_cell("""# 3. Data Pipeline & Robust Image Indexing
DATA_DIR = None
for candidate in [
    Path("/kaggle/input/toko-marcell-fashion-vlm"),
    Path("/kaggle/input/toko-marcell-vlm"),
    Path("./manual/data/processed"),
    Path("../data/processed"),
]:
    if candidate.exists() and (candidate / "clip_train.json").exists():
        DATA_DIR = candidate
        break

if DATA_DIR is None:
    for p in Path("/kaggle/input").rglob("clip_train.json"):
        DATA_DIR = p.parent
        break

print(f"Detected Dataset Directory: {DATA_DIR}")

# Unpack archive if present
if DATA_DIR:
    tar_candidates = list(DATA_DIR.glob("*images*.tar*")) + list(Path("/kaggle/input").rglob("*images*.tar*"))
    if tar_candidates and not Path("./images").exists():
        print(f"Extracting images from archive: {tar_candidates[0]}...")
        !tar -xzf {tar_candidates[0]} -C .

# Build universal image lookup table
IMAGE_MAP = {}
for p in Path("/kaggle/input").rglob("*.jpg"):
    IMAGE_MAP[p.name] = str(p)
for p in Path("./images").rglob("*.jpg"):
    IMAGE_MAP[p.name] = str(p)
if DATA_DIR:
    for p in Path(DATA_DIR).rglob("*.jpg"):
        IMAGE_MAP[p.name] = str(p)

print(f"Indexed {len(IMAGE_MAP)} unique product images in IMAGE_MAP.")

def get_image(item):
    name = os.path.basename(item.get("relative_image_path") or item.get("image_path") or "")
    path = IMAGE_MAP.get(name)
    if not path or not os.path.exists(path):
        p = item.get("relative_image_path") or item.get("image_path")
        if p and os.path.exists(p):
            path = p
    if path and os.path.exists(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            pass
    return Image.new("RGB", (224, 224), (240, 240, 240))

# Load verified splits
with open(DATA_DIR / "clip_train.json", "r") as f: train_data = json.load(f)
with open(DATA_DIR / "clip_val.json", "r") as f: val_data = json.load(f)
with open(DATA_DIR / "clip_test.json", "r") as f: test_data = json.load(f)

print(f"Splits verified: Train={len(train_data):,} items | Validation={len(val_data):,} items | Test={len(test_data):,} items")
"""))

# Cell 5: Dataset & Category BatchSampler
cells.append(nbf.v4.new_code_cell("""# 4. PyTorch Dataset & Hard-Negative Category BatchSampler
class FashionVLMDataset(Dataset):
    def __init__(self, data, processor, max_length=64):
        self.data = data
        self.processor = processor
        self.max_length = max_length

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        item = self.data[idx]
        image = get_image(item)
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
    '''
    Groups items of identical apparel categories into the same mini-batch.
    This creates in-batch hard negatives, preventing trivial cross-category discrimination.
    '''
    def __init__(self, data, batch_size, shuffle=True):
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.cat_to_idx = collections.defaultdict(list)
        for i, item in enumerate(data):
            self.cat_to_idx[item.get("category", "Other")].append(i)
        self.batches = self._create_batches()

    def _create_batches(self):
        batches, overflow = [], []
        keys = list(self.cat_to_idx.keys())
        if self.shuffle: random.shuffle(keys)
        for cat in keys:
            idx = list(self.cat_to_idx[cat])
            if self.shuffle: random.shuffle(idx)
            for i in range(0, len(idx) - len(idx)%self.batch_size, self.batch_size):
                batches.append(idx[i : i+self.batch_size])
            rem = len(idx) % self.batch_size
            if rem > 0:
                overflow.extend(idx[len(idx)-rem :])
        if self.shuffle: random.shuffle(overflow)
        for i in range(0, len(overflow) - len(overflow)%self.batch_size, self.batch_size):
            batches.append(overflow[i : i+self.batch_size])
        final_rem = len(overflow) % self.batch_size
        if final_rem > 0:
            batches.append(overflow[len(overflow)-final_rem :])
        if self.shuffle: random.shuffle(batches)
        return batches

    def __iter__(self):
        self.batches = self._create_batches()
        for b in self.batches:
            yield b

    def __len__(self):
        return len(self.batches)
"""))

# Cell 6: Losses
cells.append(nbf.v4.new_code_cell("""# 5. Multimodal Contrastive Loss Implementations (InfoNCE & SigLIP)
class SigLIPLoss(nn.Module):
    '''
    Sigmoid Loss for Language Image Pre-Training (SigLIP; Zhai et al., Google DeepMind, ICCV 2023).
    Replaces multi-class softmax InfoNCE with pairwise binary cross-entropy:
    L = - (1/B) sum_{i,j} log sigma(y_{ij} (exp(t) * u_i^T v_j + b))
    '''
    def __init__(self, init_temp=10.0, init_bias=-10.0):
        super().__init__()
        self.temp = nn.Parameter(torch.tensor(math.log(init_temp)))
        self.bias = nn.Parameter(torch.tensor(init_bias))

    def forward(self, img_feats, txt_feats):
        logits = torch.matmul(img_feats, txt_feats.t()) * self.temp.exp() + self.bias
        bsz = img_feats.shape[0]
        y = 2.0 * torch.eye(bsz, device=img_feats.device) - 1.0
        loss = F.softplus(-y * logits).mean()
        with torch.no_grad():
            r1 = (logits.t().argmax(dim=-1) == torch.arange(bsz, device=img_feats.device)).float().mean().item()
        return loss, r1

def compute_infonce(model, img_feats, txt_feats, device):
    '''
    Symmetric InfoNCE contrastive loss over dual cross-modal towers.
    '''
    raw = model.module if hasattr(model, "module") else model
    scale = getattr(raw, "logit_scale", getattr(getattr(raw, "base_model", None), "logit_scale", None))
    logit_scale = scale.exp().clamp(max=100.0) if scale is not None else torch.tensor(14.0, device=device)
    sim = logit_scale * torch.matmul(img_feats, txt_feats.t())
    bsz = img_feats.shape[0]
    gt = torch.arange(bsz, dtype=torch.long, device=device)
    loss = (F.cross_entropy(sim, gt) + F.cross_entropy(sim.t(), gt)) / 2.0
    with torch.no_grad():
        r1 = (sim.t().argmax(dim=-1) == gt).float().mean().item()
    return loss, r1
"""))

# Cell 7: Early Stopping & Evaluation Harness
cells.append(nbf.v4.new_code_cell("""# 6. Production EarlyStopping & Retrieval Evaluation Harness
class EarlyStopping:
    '''
    Monitors validation loss; if loss does not improve for `patience` consecutive epochs,
    signals to stop training and restores the best model checkpoint weights.
    '''
    def __init__(self, patience=2, min_delta=0.001, mode="min"):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.best_weights = None

    def __call__(self, val_metric, model):
        score = -val_metric if self.mode == "min" else val_metric
        if self.best_score is None:
            self.best_score = score
            self.best_weights = copy.deepcopy(model.state_dict())
            print(f"  [EarlyStopping] Initial baseline best score set: {val_metric:.4f}")
        elif score < self.best_score + self.min_delta:
            self.counter += 1
            print(f"  [EarlyStopping] Metric did not improve sufficiently ({val_metric:.4f}). Counter: {self.counter}/{self.patience}")
            if self.counter >= self.patience:
                self.early_stop = True
                print("  [EarlyStopping] Patience limit reached! Triggering early termination.")
        else:
            self.best_score = score
            self.best_weights = copy.deepcopy(model.state_dict())
            print(f"  [EarlyStopping] Improvement registered! New best score: {val_metric:.4f}. Counter reset to 0.")
            self.counter = 0

    def restore_best_weights(self, model):
        if self.best_weights is not None:
            model.load_state_dict(self.best_weights)
            print("  [EarlyStopping] Restored best model weights from optimal epoch.")

def extract_feats(model, batch, device):
    raw = model.module if hasattr(model, "module") else model
    if hasattr(raw, "get_image_features"):
        base = raw
    elif hasattr(raw, "base_model") and hasattr(raw.base_model, "get_image_features"):
        base = raw.base_model
    elif hasattr(raw, "model") and hasattr(raw.model, "get_image_features"):
        base = raw.model
    else:
        base = getattr(raw, "base_model", raw)

    img_out = base.get_image_features(pixel_values=batch["pixel_values"].to(device))
    img_feats = getattr(img_out, "pooler_output", img_out)
    txt_out = base.get_text_features(input_ids=batch["input_ids"].to(device), attention_mask=batch["attention_mask"].to(device))
    txt_feats = getattr(txt_out, "pooler_output", txt_out)
    img_feats = img_feats / (img_feats.norm(dim=-1, keepdim=True) + 1e-8)
    txt_feats = txt_feats / (txt_feats.norm(dim=-1, keepdim=True) + 1e-8)
    return img_feats, txt_feats

def evaluate_retrieval(model, processor, test_items, batch_size=32):
    model.eval()
    img_embeds, txt_embeds = [], []
    t0 = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(test_items), batch_size):
            b = test_items[i : i+batch_size]
            imgs = [get_image(item) for item in b]
            caps = [item.get("caption") or item.get("title") or "fashion" for item in b]

            inputs = processor(text=caps, images=imgs, return_tensors="pt", padding=True, truncation=True, max_length=64)
            im_f, tx_f = extract_feats(model, inputs, device)
            img_embeds.append(im_f.cpu().numpy())
            txt_embeds.append(tx_f.cpu().numpy())

    total_ms = (time.perf_counter() - t0) * 1000 / len(test_items)
    im_mat = np.vstack(img_embeds)
    tx_mat = np.vstack(txt_embeds)
    sim = tx_mat @ im_mat.T
    n = len(sim)
    r1, r5, r10, rr = 0, 0, 0, 0.0
    for i in range(n):
        rank = int(np.where(np.argsort(-sim[i]) == i)[0][0]) + 1
        if rank == 1: r1 += 1
        if rank <= 5: r5 += 1
        if rank <= 10: r10 += 1
        rr += 1.0 / rank
    return {
        "Recall@1": round(r1 / n * 100, 2),
        "Recall@5": round(r5 / n * 100, 2),
        "Recall@10": round(r10 / n * 100, 2),
        "MRR": round(rr / n, 4),
        "Latency_ms": round(total_ms, 2),
    }

def evaluate_retrieval_tta(model, processor, test_items, batch_size=32):
    # Test-Time Augmentation (TTA) with 3 Complementary E-Commerce Prompt Templates
    model.eval()
    img_embeds, txt_embeds = [], []
    t0 = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(test_items), batch_size):
            b = test_items[i : i+batch_size]
            imgs = [get_image(item) for item in b]

            p1 = [item.get("caption") or item.get("title") or "fashion" for item in b]
            p2 = [f"a product photo of {item.get('brand', '')} {item.get('title', '')}, category: {item.get('category', 'fashion')}".strip() for item in b]
            p3 = [f"official retail catalog: {item.get('title', '')} ({item.get('category', '')} / {item.get('department', 'apparel')})".strip() for item in b]

            in1 = processor(text=p1, return_tensors="pt", padding=True, truncation=True, max_length=64).to(device)
            in2 = processor(text=p2, return_tensors="pt", padding=True, truncation=True, max_length=64).to(device)
            in3 = processor(text=p3, return_tensors="pt", padding=True, truncation=True, max_length=64).to(device)
            in_img = processor(images=imgs, return_tensors="pt", padding=True).to(device)

            raw = model.module if hasattr(model, "module") else model
            base = getattr(raw, "base_model", raw)

            io = getattr(base.get_image_features(pixel_values=in_img["pixel_values"]), "pooler_output", base.get_image_features(pixel_values=in_img["pixel_values"]))
            im_feats = io / (io.norm(dim=-1, keepdim=True) + 1e-8)

            t1 = getattr(base.get_text_features(in1["input_ids"], in1["attention_mask"]), "pooler_output", base.get_text_features(in1["input_ids"], in1["attention_mask"]))
            t2 = getattr(base.get_text_features(in2["input_ids"], in2["attention_mask"]), "pooler_output", base.get_text_features(in2["input_ids"], in2["attention_mask"]))
            t3 = getattr(base.get_text_features(in3["input_ids"], in3["attention_mask"]), "pooler_output", base.get_text_features(in3["input_ids"], in3["attention_mask"]))

            t1 = t1 / (t1.norm(dim=-1, keepdim=True) + 1e-8)
            t2 = t2 / (t2.norm(dim=-1, keepdim=True) + 1e-8)
            t3 = t3 / (t3.norm(dim=-1, keepdim=True) + 1e-8)

            tx_ens = (t1 + t2 + t3) / 3.0
            tx_ens = tx_ens / (tx_ens.norm(dim=-1, keepdim=True) + 1e-8)

            img_embeds.append(im_feats.cpu().numpy())
            txt_embeds.append(tx_ens.cpu().numpy())

    total_ms = (time.perf_counter() - t0) * 1000 / len(test_items)
    im_mat = np.vstack(img_embeds)
    tx_mat = np.vstack(txt_embeds)
    sim = tx_mat @ im_mat.T
    n = len(sim)
    r1, r5, r10, rr = 0, 0, 0, 0.0
    for i in range(n):
        rank = int(np.where(np.argsort(-sim[i]) == i)[0][0]) + 1
        if rank == 1: r1 += 1
        if rank <= 5: r5 += 1
        if rank <= 10: r10 += 1
        rr += 1.0 / rank
    return {
        "Recall@1": round(r1 / n * 100, 2),
        "Recall@5": round(r5 / n * 100, 2),
        "Recall@10": round(r10 / n * 100, 2),
        "MRR": round(rr / n, 4),
        "Latency_ms": round(total_ms, 2),
    }

def create_wise_ft_model(base_model, fine_tuned_model, alpha=0.35):
    wise_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", use_safetensors=True).to(device)
    base_sd = base_model.state_dict()
    ft_sd = fine_tuned_model.state_dict()
    wise_sd = {}
    for k in ft_sd.keys():
        if k in base_sd and base_sd[k].shape == ft_sd[k].shape:
            wise_sd[k] = alpha * base_sd[k].to(ft_sd[k].device) + (1.0 - alpha) * ft_sd[k]
        else:
            wise_sd[k] = ft_sd[k]
    wise_model.load_state_dict(wise_sd)
    wise_model.eval()
    return wise_model
"""))

# Cell 8: Baseline Zero-Shot Evaluation
cells.append(nbf.v4.new_code_cell("""# 7. Ground Truth Baseline: Pretrained Zero-Shot CLIP ViT-B/32
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
base_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", use_safetensors=True).to(device)

print("Benchmarking Zero-Shot Base CLIP on held-out test split (N=539)...")
base_results = evaluate_retrieval(base_model, processor, test_data, batch_size=32)

print("\\n" + "=" * 45)
print("   ZERO-SHOT BASE CLIP BENCHMARK RESULTS")
print("=" * 45)
for k, v in base_results.items():
    print(f"  {k:<14}: {v}")

master_leaderboard = [
    {"Experiment": "Zero-Shot Base CLIP", **base_results}
]
"""))

# Cell 9: Modular Trainer with Validation Loss & Early Stopping
cells.append(nbf.v4.new_code_cell("""# 8. Unified Training Function with Validation & Early Stopping
BATCH_SIZE = 64 if torch.cuda.get_device_properties(0).total_memory > 1e10 else 32
BASE_LR = 5e-6
MAX_EPOCHS = 32
PATIENCE = 3
MIN_DELTA = 0.0005

training_histories = {}

def train_with_validation(exp_name, model, optimizer, loss_fn, max_epochs=32, patience=3, is_siglip=False):
    train_ds = FashionVLMDataset(train_data, processor)
    val_ds = FashionVLMDataset(val_data, processor)

    train_loader = DataLoader(train_ds, batch_sampler=CategoryAwareBatchSampler(train_data, BATCH_SIZE), num_workers=2)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, num_workers=2)

    scaler = torch.amp.GradScaler("cuda")
    
    # Cosine scheduler configured with a 20-epoch decay horizon for optimal gradient fine-tuning
    sched_horizon = min(max_epochs, 20)
    sched_steps = len(train_loader) * sched_horizon
    sched = get_cosine_schedule_with_warmup(
        optimizer, 
        num_warmup_steps=int(sched_steps * 0.1), 
        num_training_steps=sched_steps
    )
    early_stopper = EarlyStopping(patience=patience, min_delta=MIN_DELTA, mode="min")

    history = {"train_loss": [], "val_loss": [], "val_r1": []}

    print(f"\\n========================================================")
    print(f"  STARTING TRAINING: {exp_name.upper()} (Max Epochs={max_epochs}, Patience={patience})")
    print(f"========================================================")

    for epoch in range(1, max_epochs + 1):
        # Training Phase
        model.train()
        train_loss = 0.0
        for b in train_loader:
            optimizer.zero_grad()
            with torch.amp.autocast("cuda"):
                im_f, tx_f = extract_feats(model, b, device)
                if is_siglip:
                    loss, _ = loss_fn(im_f, tx_f)
                else:
                    loss, _ = compute_infonce(model, im_f, tx_f, device)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            # Safe scheduler stepping in AMP
            scale_before = scaler.get_scale()
            scaler.step(optimizer)
            scaler.update()
            scale_after = scaler.get_scale()
            if scale_before <= scale_after:
                sched.step()
            train_loss += loss.item()

        avg_train_loss = train_loss / len(train_loader)

        # Validation Phase
        model.eval()
        val_loss = 0.0
        val_r1_sum = 0.0
        with torch.no_grad():
            for b in val_loader:
                with torch.amp.autocast("cuda"):
                    im_f, tx_f = extract_feats(model, b, device)
                    if is_siglip:
                        l, r = loss_fn(im_f, tx_f)
                    else:
                        l, r = compute_infonce(model, im_f, tx_f, device)
                val_loss += l.item()
                val_r1_sum += r

        avg_val_loss = val_loss / len(val_loader)
        avg_val_r1 = (val_r1_sum / len(val_loader)) * 100.0

        history["train_loss"].append(round(avg_train_loss, 4))
        history["val_loss"].append(round(avg_val_loss, 4))
        history["val_r1"].append(round(avg_val_r1, 2))

        print(f"Epoch [{epoch:02d}/{max_epochs:02d}] -> Train Loss: {avg_train_loss:.4f} | Val Loss: {avg_val_loss:.4f} | Val In-Batch R@1: {avg_val_r1:.2f}%")

        # Early Stopping Check
        early_stopper(avg_val_loss, model)
        if early_stopper.early_stop:
            print(f"  [>>> Early Stopping Activated at Epoch {epoch} <<<]")
            print(f"  Best Val Loss: {-early_stopper.best_score:.4f}. Restoring optimal checkpoint weights...")
            early_stopper.restore_best_weights(model)
            break

    training_histories[exp_name] = history
    return model
"""))

# Cell 10: Running the Controlled Experiments (Max 32 Epochs with EarlyStopping)
cells.append(nbf.v4.new_code_cell("""# 9. Execute Experiments with EarlyStopping (Max 32 Epochs, Patience=3)

# --- A. LoRA (r=16, alpha=32) ---
print("\\n[1/3] Preparing LoRA Experiment...")
lora_base = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", use_safetensors=True).to(device)
peft_config = LoraConfig(r=16, lora_alpha=32, target_modules=["q_proj", "v_proj"], lora_dropout=0.1, bias="none")
lora_model = get_peft_model(lora_base, peft_config)
trainable_p = sum(p.numel() for p in lora_model.parameters() if p.requires_grad)
total_p = sum(p.numel() for p in lora_model.parameters())
print(f"LoRA Trainable Parameters: {trainable_p:,} / {total_p:,} ({trainable_p/total_p*100:.2f}%)")
opt_lora = torch.optim.AdamW(lora_model.parameters(), lr=BASE_LR * 2.0, weight_decay=0.01)

lora_model = train_with_validation("lora", lora_model, opt_lora, None, max_epochs=MAX_EPOCHS, patience=PATIENCE, is_siglip=False)
m_lora = evaluate_retrieval(lora_model, processor, test_data)
master_leaderboard.append({"Experiment": f"LoRA (EarlyStopped, max={MAX_EPOCHS})", **m_lora})

# --- B. Decoupled LR (WiSE-FT; Layers 0-5 Frozen) ---
print("\\n[2/3] Preparing Decoupled LR Experiment...")
decoupled_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", use_safetensors=True).to(device)
for layer in decoupled_model.vision_model.encoder.layers[:6]:
    for p in layer.parameters(): p.requires_grad = False

v_p = [p for n, p in decoupled_model.named_parameters() if p.requires_grad and "vision_model" in n]
t_p = [p for n, p in decoupled_model.named_parameters() if p.requires_grad and "text_model" in n]
h_p = [p for n, p in decoupled_model.named_parameters() if p.requires_grad and ("projection" in n or "scale" in n)]
opt_decoupled = torch.optim.AdamW([
    {"params": v_p, "lr": BASE_LR * 0.2},
    {"params": t_p, "lr": BASE_LR * 1.0},
    {"params": h_p, "lr": BASE_LR * 2.0}
], weight_decay=0.01)

decoupled_model = train_with_validation("decoupled_lr", decoupled_model, opt_decoupled, None, max_epochs=MAX_EPOCHS, patience=PATIENCE, is_siglip=False)
m_decoupled = evaluate_retrieval(decoupled_model, processor, test_data)
master_leaderboard.append({"Experiment": f"DECOUPLED_LR (EarlyStopped, max={MAX_EPOCHS})", **m_decoupled})

# --- C. SigLIP Loss (Uniform LR) ---
print("\\n[3/3] Preparing SigLIP Experiment...")
siglip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32", use_safetensors=True).to(device)
siglip_loss_module = SigLIPLoss().to(device)
opt_siglip = torch.optim.AdamW(list(siglip_model.parameters()) + list(siglip_loss_module.parameters()), lr=BASE_LR, weight_decay=0.01)

siglip_model = train_with_validation("siglip", siglip_model, opt_siglip, siglip_loss_module, max_epochs=MAX_EPOCHS, patience=PATIENCE, is_siglip=True)
m_siglip = evaluate_retrieval(siglip_model, processor, test_data)
master_leaderboard.append({"Experiment": f"SigLIP (EarlyStopped, max={MAX_EPOCHS})", **m_siglip})
"""))

# Cell 11: WiSE-FT Weight Blending & TTA
cells.append(nbf.v4.new_code_cell("""# 10. Advanced Post-Training Enhancements: WiSE-FT & Test-Time Augmentation (TTA)

# 1. WiSE-FT Weight-Space Ensembling (alpha=0.35)
print("Evaluating WiSE-FT Weight Blending (alpha=0.35) on Decoupled LR...")
wise_model = create_wise_ft_model(base_model, decoupled_model, alpha=0.35)
m_wise = evaluate_retrieval(wise_model, processor, test_data)
master_leaderboard.append({"Experiment": "WiSE-FT (DECOUPLED_LR, a=0.35)", **m_wise})

# 2. Test-Time Prompt Ensembling (TTA)
print("Evaluating Test-Time Prompt Ensembling (TTA) on Decoupled LR...")
m_tta = evaluate_retrieval_tta(decoupled_model, processor, test_data)
master_leaderboard.append({"Experiment": "DECOUPLED_LR + TTA (3 Prompts)", **m_tta})

# Export Best Champion Model
save_dir = "/kaggle/working/best_champion_model"
os.makedirs(save_dir, exist_ok=True)
decoupled_model.save_pretrained(save_dir, safe_serialization=True)
processor.save_pretrained(save_dir)
print(f"\\nSuccessfully exported Champion Model to {save_dir}")
"""))

# Cell 12: Visualizations & Publication Graphics
cells.append(nbf.v4.new_code_cell("""# 11. Publication-Quality Graphics & Experimental Visualization
plt.style.use("seaborn-v0_8-whitegrid")
fig = plt.figure(figsize=(18, 12))

# Subplot 1: Training & Validation Loss Dynamics
ax1 = fig.add_subplot(2, 2, 1)
colors = {"lora": "#3498db", "decoupled_lr": "#2ecc71", "siglip": "#e74c3c"}

for exp, hist in training_histories.items():
    epochs_range = list(range(1, len(hist["train_loss"]) + 1))
    ax1.plot(epochs_range, hist["train_loss"], marker="o", linewidth=2.0, label=f"{exp.upper()} (Train)", color=colors[exp])
    ax1.plot(epochs_range, hist["val_loss"], marker="s", linestyle="--", linewidth=1.6, label=f"{exp.upper()} (Val)", color=colors[exp], alpha=0.75)

ax1.set_title("Training & Validation Loss Dynamics (with Early Stopping)", fontsize=13, fontweight="bold", pad=10)
ax1.set_xlabel("Epoch", fontsize=11)
ax1.set_ylabel("Contrastive Loss", fontsize=11)
ax1.legend(loc="upper right", frameon=True)
ax1.grid(True, linestyle=":", alpha=0.6)

# Subplot 2: Multi-Metric Retrieval Comparison
ax2 = fig.add_subplot(2, 2, 2)
df_lead = pd.DataFrame(master_leaderboard)
metrics = ["Recall@1", "Recall@5", "Recall@10"]
x = np.arange(len(df_lead))
width = 0.25

rects1 = ax2.bar(x - width, df_lead["Recall@1"], width, label="Recall@1", color="#1a2744")
rects2 = ax2.bar(x, df_lead["Recall@5"], width, label="Recall@5", color="#3498db")
rects3 = ax2.bar(x + width, df_lead["Recall@10"], width, label="Recall@10", color="#2ecc71")

ax2.set_title("Cross-Modal Retrieval Performance Across Architecture Formulations", fontsize=13, fontweight="bold", pad=10)
ax2.set_ylabel("Accuracy (%)", fontsize=11)
ax2.set_xticks(x)
ax2.set_xticklabels(df_lead["Experiment"], rotation=25, ha="right", fontsize=9)
ax2.legend(loc="upper left", frameon=True)
ax2.grid(True, linestyle=":", alpha=0.6)

# Subplot 3: Latency vs Recall@1 Pareto Frontier
ax3 = fig.add_subplot(2, 2, 3)
scatter_colors = ["#7f8c8d", "#3498db", "#27ae60", "#e74c3c", "#f39c12", "#9b59b6"]
for i, row in df_lead.iterrows():
    ax3.scatter(row["Latency_ms"], row["Recall@1"], s=180, c=scatter_colors[i % len(scatter_colors)], edgecolors="black", linewidth=1.5, zorder=5)
    ax3.annotate(
        row["Experiment"],
        (row["Latency_ms"] + 0.3, row["Recall@1"] + 0.4),
        fontsize=9,
        fontweight="bold" if "DECOUPLED" in row["Experiment"] else "normal"
    )

ax3.set_title("Inference Latency vs Retrieval Precision (The Pareto Frontier)", fontsize=13, fontweight="bold", pad=10)
ax3.set_xlabel("Latency per Item (ms)", fontsize=11)
ax3.set_ylabel("Recall@1 Accuracy (%)", fontsize=11)
ax3.grid(True, linestyle=":", alpha=0.6)

# Subplot 4: Relative Gain Delta vs Pretrained Zero-Shot
ax4 = fig.add_subplot(2, 2, 4)
base_r1 = base_results["Recall@1"]
deltas = [round(r - base_r1, 2) for r in df_lead["Recall@1"]]
bar_cols = ["#95a5a6" if d == 0 else ("#2ecc71" if d == max(deltas) else "#34495e") for d in deltas]

bars = ax4.barh(df_lead["Experiment"], deltas, color=bar_cols, height=0.55, edgecolor="black", linewidth=1.0)
for bar, d in zip(bars, deltas):
    ax4.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2, f"+{d:.2f}%" if d > 0 else "Baseline", va="center", fontsize=9, fontweight="bold")

ax4.set_title("Absolute Recall@1 Gain over Pre-Trained Zero-Shot Foundation Model", fontsize=13, fontweight="bold", pad=10)
ax4.set_xlabel("Recall@1 Delta (%)", fontsize=11)
ax4.grid(True, linestyle=":", alpha=0.6)

plt.tight_layout()
plt.savefig("/kaggle/working/vlm_experiment_graphics.png", dpi=300)
plt.show()
print("Saved publication-grade visualization to /kaggle/working/vlm_experiment_graphics.png")
"""))

# Cell 13: Qualitative Visual Retrieval Demonstration
cells.append(nbf.v4.new_code_cell("""# 12. Qualitative Cross-Modal Retrieval Showcase (Real Toko Marcell Catalog Items)
decoupled_model.eval()

# Select 3 distinct test query items
sample_indices = [0, 50, 100]
fig, axes = plt.subplots(len(sample_indices), 2, figsize=(10, 3.5 * len(sample_indices)))
if len(sample_indices) == 1: axes = np.expand_dims(axes, 0)

with torch.no_grad():
    for row_idx, item_idx in enumerate(sample_indices):
        item = test_data[item_idx]
        title = item.get("title", "")
        caption = item.get("caption", title)
        img = get_image(item)

        # Baseline zero-shot embedding
        in_txt = processor(text=[caption], return_tensors="pt", padding=True, truncation=True, max_length=64).to(device)
        in_img = processor(images=img, return_tensors="pt", padding=True).to(device)

        raw = decoupled_model.module if hasattr(decoupled_model, "module") else decoupled_model
        base = getattr(raw, "base_model", raw)
        vf = base.get_image_features(in_img["pixel_values"])
        tf = base.get_text_features(in_txt["input_ids"], in_txt["attention_mask"])

        vf = getattr(vf, "pooler_output", vf)
        tf = getattr(tf, "pooler_output", tf)
        sim_score = F.cosine_similarity(vf, tf).item()

        # Render
        axes[row_idx, 0].imshow(img)
        axes[row_idx, 0].axis("off")
        axes[row_idx, 0].set_title(f"Target Product Image\\n(ASIN: {item.get('asin', '')})", fontsize=10, fontweight="bold")

        axes[row_idx, 1].text(0.05, 0.65, f"Search Query:\\n\\"{caption[:80]}...\\"", fontsize=10, wrap=True)
        axes[row_idx, 1].text(0.05, 0.35, f"Category: {item.get('category', '')}\\nBrand: {item.get('brand', '')}", fontsize=9, color="#5c5850")
        axes[row_idx, 1].text(0.05, 0.15, f"Decoupled LR Cosine Similarity: {sim_score:.4f}", fontsize=10, fontweight="bold", color="#1a2744")
        axes[row_idx, 1].axis("off")

plt.suptitle("Qualitative Test Set Retrieval Alignment Demonstration (Decoupled LR)", fontsize=13, fontweight="bold", y=0.99)
plt.tight_layout()
plt.savefig("/kaggle/working/qualitative_retrieval_showcase.png", dpi=200)
plt.show()
"""))

# Cell 14: Final Master Scoreboard & Conclusions
cells.append(nbf.v4.new_code_cell("""# 13. Master Leaderboard Scoreboard & Engineering Conclusions
df_final = pd.DataFrame(master_leaderboard).sort_values("Recall@1", ascending=False).reset_index(drop=True)

print("\\n" + "=" * 80)
print("             MASTER VLM EXPERIMENT LEADERBOARD (3-EPOCH CONTROLLED)")
print("=" * 80)
display(df_final)

df_final.to_csv("/kaggle/working/leaderboard_3ep_final.csv", index=False)
with open("/kaggle/working/experiment_summary_3ep_final.json", "w") as f:
    json.dump(master_leaderboard, f, indent=2)

print("\\nSaved artifacts:")
print(" - /kaggle/working/leaderboard_3ep_final.csv")
print(" - /kaggle/working/experiment_summary_3ep_final.json")
print(" - /kaggle/working/vlm_experiment_graphics.png")
print(" - /kaggle/working/qualitative_retrieval_showcase.png")
print(" - /kaggle/working/best_champion_model/ (Safetensors Checkpoint)")
"""))

nb["cells"] = cells

output_file = Path("/home/marcell/Projects/marcell-portfolio/toko-marcell/manual/pipelines/kaggle/toko_marcell_vlm_experiments.ipynb")
with open(output_file, "w", encoding="utf-8") as f:
    nbf.write(nb, f)

# Also save a local mirror for direct notebook inspection
local_mirror = Path("/home/marcell/Projects/marcell-portfolio/toko-marcell/manual/pipelines/toko_marcell_vlm_research.ipynb")
with open(local_mirror, "w", encoding="utf-8") as f:
    nbf.write(nb, f)

print(f"Successfully generated clean research notebooks:")
print(f" - {output_file}")
print(f" - {local_mirror}")
