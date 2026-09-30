---
base_model: openai/clip-vit-base-patch32
library_name: peft
pipeline_tag: zero-shot-image-classification
tags:
  - base_model:adapter:openai/clip-vit-base-patch32
  - lora
  - transformers
  - clip
  - vision-language
  - multimodal
  - fashion
  - retrieval
---

# LoRA adapter — Toko Marcell fashion CLIP (experiment artifact)

A **PEFT LoRA adapter** for `openai/clip-vit-base-patch32`, domain-adapted to a 4,670-product fashion
catalog so that cross-modal text↔image retrieval works on fine-grained apparel attributes (cut
silhouette, fabric texture, surface sheen, model numbers) where zero-shot CLIP is weak.

This directory holds the **local smoke-run** adapter produced while developing the training loop. It is
a real, loadable artifact — but it is **not** the study's winning model. Read
[Metrics](#metrics) before quoting a number.

> ⚠️ **Repository note:** this model card, `adapter_config.json` and `experiment_summary.json` are
> **tracked in git** (they are small and reviewable). The adapter **weights**
> (`adapter_model.safetensors`) are large binaries and are **not** tracked. The production-sized
> champion text encoder is published at
> [HF Hub: Marcell-Kristianto/toko-marcell-clip](https://huggingface.co/Marcell-Kristianto/toko-marcell-clip)
> and fetched by the API at runtime — see [`pipelines/README.md`](../../../pipelines/README.md).

---

## Model details

| | |
|---|---|
| **Developed by** | Marcell Hermawan Kristianto (Data Science, BINUS University) |
| **Project** | Toko Marcell — multimodal hybrid retrieval e-commerce engine |
| **Model type** | PEFT LoRA adapter on a CLIP dual encoder |
| **Base model** | `openai/clip-vit-base-patch32` |
| **Library** | `peft` 0.21.0 + `transformers` |
| **Adapter config** | `r=16`, `lora_alpha=32`, `lora_dropout=0.1`, `bias="none"`, `inference_mode=true` |
| **Target modules** | `q_proj`, `v_proj` (attention projections, both towers + projection heads via PEFT) |
| **Trainable parameters** | ~1.2% of the base model (LoRA matrices only) |
| **Language** | English (fashion catalog captions) |
| **Licence** | Research / non-commercial (inherits the Amazon Reviews 2018 dataset terms) |

The **idea** behind this adapter (from the literature review): full fine-tuning of a 151 M-parameter
model on ~4,300 image–text pairs risks catastrophic forgetting of generic visual priors. LoRA freezes
the base weights and learns low-rank updates
$W = W_0 + \frac{\alpha}{r}(BA)$, so generic visual competence is preserved while the shared embedding
space is nudged toward fashion vocabulary.

---

## Uses

### Direct use

Encode fashion product images and natural-language queries into a shared 512-d space and rank items by
cosine similarity — i.e. **text→image and image→text retrieval**. Load the base CLIP model, attach the
adapter from this directory:

```python
from transformers import CLIPModel, CLIPProcessor
from peft import PeftModel

BASE = "openai/clip-vit-base-patch32"
ADAPTER = "models/experiments/lora"          # this directory

model = CLIPModel.from_pretrained(BASE)
model = PeftModel.from_pretrained(model, ADAPTER)
model.eval()

processor = CLIPProcessor.from_pretrained(BASE)

texts = ["heavyweight 8.5 oz twill work pant, loose straight fit"]
images = [pil_image]                         # product photo(s)

inputs = processor(text=texts, images=images, return_tensors="pt", padding=True)
out = model(**inputs)

# CLIP returns cosine logits; take the diagonal/argmax for retrieval
logits_per_image = out.logits_per_image      # (n_images, n_texts)
```

### Downstream use

This is the architectural shape of the **visual-search** and **text→image** paths in Toko Marcell:
product images are embedded offline into PostgreSQL/pgvector (512-d, HNSW cosine), and queries are
encoded online. See [`api/README.md`](../../../api/README.md) for the serving implementation and
[`pipelines/README.md`](../../../pipelines/README.md) for the study.

### Out-of-scope use

- **Not** a general-purpose CLIP replacement; it is adapted to one fashion catalog.
- **Not** trained for image generation, captioning, OCR, or face recognition.
- Do not use to make claims about real people, or on medical/legal imagery.
- Product images remain Amazon's property; do not redistribute them.

---

## Bias, risks, and limitations

- **Domain specificity.** Improvements are measured on this catalog's categories (lingerie, jeans,
  sneakers, workwear, …) and may not transfer to unrelated domains.
- **Sub-brand logo invariance.** Both the zero-shot and fine-tuned models struggle to separate
  otherwise-identical garments by a small embroidered logo/patch — a documented failure mode with a
  proposed multi-crop/RoI-Align fix.
- **Caption dependence.** Captions are structured (`Brand + Title + Category/Department + fabric
  features`); the model learns that vocabulary. Free-form marketing prose is out of distribution.
- **Dataset licence.** Training data derives from Amazon Reviews 2018 (research/non-commercial).
- **Small local artifact.** This particular adapter was trained for one epoch at batch size 8 as a
  pipeline sanity check; treat its test metrics as a smoke test, not a benchmark (see below).

---

## Training details

### Training data

- Source: Toko Marcell catalog built from **Amazon Reviews 2018, Clothing, Shoes & Jewelry**
  (McAuley Lab, UCSD).
- **5,378 verified image–text pairs**, deterministic split (seed 42): **4,302 train / 537 val / 539 test**.
- Captions combine brand, title, category path, department and the first fabric feature bullets.
- 622 pairs were skipped for a missing cached image.
- Dataset prepared by [`pipelines/prepare_clip_dataset.py`](../../../pipelines/prepare_clip_dataset.py);
  summary in `data/processed/clip_dataset_summary.json`.

### Training procedure

| Setting | This local artifact | Kaggle study (authoritative) |
|---|---|---|
| Epochs | 1 | 3 (with EarlyStopping) |
| Batch size | 8 | 64 |
| Learning rate | 5e-6 | 5e-6 |
| Precision | fp32 | AMP FP16 |
| Hardware | local GPU/CPU | NVIDIA Tesla T4 16 GB |
| Loss | symmetric InfoNCE | symmetric InfoNCE |
| Adaption | LoRA r=16, α=32 | LoRA r=16, α=32 |

Category-aware hard-negative batching, a 10% warmup + cosine schedule, and validation Recall@1
best-checkpointing are implemented in
[`pipelines/train_clip.py`](../../../pipelines/train_clip.py) and exercised in
`pipelines/experiments_clip.py`.

---

## Metrics

**This local smoke run** (`experiment_summary.json` in this directory, 1 epoch, batch 8):

| Metric (on the smoke-run eval slice) | Value |
|---|:---:|
| Best validation Recall@1 | 0.875 |
| Test Recall@1 | 0.8125 |
| Test Recall@5 | 1.000 |
| Test Recall@10 | 1.000 |
| Test MRR | 0.8854 |
| Latency / query | 21.63 ms |

These numbers are **high because the evaluation slice was tiny** (a small batch at batch size 8), which
is why they are not the headline result.

**The authoritative study result** for LoRA — 539 held-out pairs, batch 64, 3 epochs, Tesla T4 — is
**Recall@1 33.40% / Recall@5 71.61% / Recall@10 83.49% / MRR 0.5038**, versus a **26.53% Recall@1
zero-shot baseline**. That full 5-model leaderboard (including the Decoupled-LR champion at 39.15%
Recall@1) lives in
[`pipelines/multimodal_benchmark_results.json`](../../../pipelines/multimodal_benchmark_results.json)
and is discussed in the top-level [`README.md`](../../../README.md).

### Why report both

A portfolio artifact should not show only the flattering number. The smoke run proves the adapter is
loadable and the loop works; the Kaggle benchmark is the defensible comparison. The gap between them
(81% vs 33% Recall@1) is itself a lesson about how much an "evaluation slice" can flatter a model.

---

## Evaluation

- Protocol: text→image retrieval on 539 unseen pairs; metrics Recall@1/5/10, MRR, mean rank, latency.
- Harness: [`pipelines/eval_multimodal.py`](../../../pipelines/eval_multimodal.py) (writes
  `multimodal_benchmark_results.json`, mines biggest rank improvements and regressions).
- Qualitative findings: [`pipelines/failure_case_analysis.md`](../../../pipelines/failure_case_analysis.md).

---

## Technical specifications

- **Architecture:** CLIP dual encoder (ViT-B/32 vision, 12-layer transformer text), shared 512-d
  projection; LoRA adapters on attention `q_proj`/`v_proj`.
- **Objective:** symmetric InfoNCE, $\mathcal{L} = \tfrac12(\mathcal{L}_{i\to t}+\mathcal{L}_{t\to i})$.
- **Compute:** local GPU for this artifact; Kaggle Tesla T4 (AMP FP16) for the study.

## Citation

If referencing this adapter, cite the base model and the dataset:

```bibtex
@inproceedings{ni2019justifying,
  title     = {Justifying Recommendations using Distantly-Labeled Reviews and Fine-Grained Aspects},
  author    = {Ni, Jianmo and Li, Jiacheng and McAuley, Julian},
  booktitle = {EMNLP-IJCNLP},
  year      = {2019}
}
```

## Model card authors

Marcell Hermawan Kristianto — [marcellkristianto.ai@gmail.com](mailto:marcellkristianto.ai@gmail.com)

---

### Sibling artifacts

| Path | What it is |
|---|---|
| `../lora/` | this adapter (PEFT LoRA) |
| `../siglip/` | local SigLIP-loss smoke run |
| `models/fashion_clip/` | local full fine-tune checkpoint + `training_history.json` |
| `pipelines/Best Model/` | the champion Decoupled-LR checkpoint (gitignored) |
| `pipelines/multimodal_benchmark_results.json` | the authoritative leaderboard |
