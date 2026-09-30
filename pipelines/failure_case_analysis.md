# Multimodal Vision-Language Model (VLM) Research & Failure Case Analysis

**Target Alignment:** AI Engineer (Multimodal AI / LLM) — PT. Kalbe Farma, Tbk.  
**Project:** Toko Marcell  
**Author:** Marcell Hermawan Kristianto (5th Semester Data Science, Binus University)  
**Date:** September 2026  

---

## 1. Executive Summary & Objective

In modern retail and healthcare e-commerce search, users describe products using rich, multi-attribute semantic phrases (e.g. *"distressed vintage wash straight-leg jeans"*, *"breathable moisture-wicking athletic tee"*, or *"water-resistant nylon windbreaker"*). 

General-purpose pre-trained Vision-Language Models (such as OpenAI's canonical `clip-vit-base-patch32`) exhibit significant cross-modal alignment on broad concepts (e.g. separating a *dog* from a *car*), but frequently fail on **fine-grained domain nuances**:
1. Conflating clean, dressy cotton chinos with rugged, heavyweight workwear pants.
2. Ignoring specific cut silhouettes (confusing relaxed straight-fit with slim taper).
3. Mistaking fabric texture and surface sheen (treating glossy waterproof nylon as standard matte fleece).

To overcome these failures, we implemented an end-to-end PyTorch contrastive fine-tuning pipeline on **5,378 curated Amazon Fashion pairs** using **Symmetric InfoNCE Loss**, **Category-Aware Hard Negative Mining**, and mixed-precision gradient scaling. This report details the benchmark methodology, empirical results, and systematic qualitative error analysis.

---

## 2. Theoretical Framework & Contrastive Optimization

### 2.1 Dual-Encoder Metric Learning
The architecture projects visual images $x_v$ and tokenized text descriptions $x_t$ into a shared unit hypersphere $\mathbb{S}^{511} \subset \mathbb{R}^{512}$:

$$u = \frac{f_v(x_v)}{\|f_v(x_v)\|_2}, \quad v = \frac{f_t(x_t)}{\|f_t(x_t)\|_2}$$

Pairwise affinity is computed as the temperature-scaled cosine similarity:

$$S_{i,j} = \exp(\tau) \cdot (u_i^\top v_j)$$

where $\tau$ is a learnable logit scale parameter clamped to prevent numerical divergence.

### 2.2 In-Batch Negatives vs. Category-Aware Hard Negatives
- **Standard In-Batch Negatives:** For a batch of size $N$, the diagonal entries $(u_i, v_i)$ represent positive pairs, while the $N(N - 1)$ off-diagonal entries $(u_i, v_j)_{i \neq j}$ serve as free negatives. However, when a batch contains random items (e.g., contrasting a *sneaker* against a *floral dress*), the loss gradient is dominated by trivial color or background cues.
- **Category-Aware BatchSampler:** We group items by their catalog category (e.g. 32 jeans or 32 jackets per batch). The model is forced to discriminate between subtle design features (stitching patterns, pocket shapes, denim washes, and fly closures), creating high-gradient hard negatives without manual annotation.

### 2.3 Symmetric InfoNCE Objective
The model is trained by minimizing the bidirected cross-entropy loss:

$$\mathcal{L} = \frac{1}{2} \left( \mathcal{L}_{\text{image}\to\text{text}} + \mathcal{L}_{\text{text}\to\text{image}} \right)$$

$$\mathcal{L}_{\text{image}\to\text{text}} = - \frac{1}{N} \sum_{i=1}^N \log \frac{\exp(S_{i,i})}{\sum_{j=1}^N \exp(S_{i,j})}$$

$$\mathcal{L}_{\text{text}\to\text{image}} = - \frac{1}{N} \sum_{i=1}^N \log \frac{\exp(S_{i,i})}{\sum_{j=1}^N \exp(S_{j,i})}$$

---

## 3. Quantitative Benchmark Scoreboard

Evaluated on the held-out test split of **539 verified image-text pairs** (never seen during training),
batch size 64, 3 epochs on an NVIDIA Tesla T4 16 GB. This is the **authoritative leaderboard**; its
machine-readable form is [`multimodal_benchmark_results.json`](./multimodal_benchmark_results.json).

| Experiment | Architecture / approach | Recall@1 | Recall@5 | Recall@10 | MRR | Latency (T4) |
|---|---|:---:|:---:|:---:|:---:|:---:|
| Zero-shot base | `openai/clip-vit-base-patch32` (frozen) | 26.53% | 54.92% | 70.69% | 0.4033 | 27.80 ms |
| PEFT LoRA | LoRA (early-stopped, r=16, α=32 on `q_proj, v_proj`) | 33.40% | 71.61% | 83.49% | 0.5038 | 15.50 ms |
| SigLIP | Pairwise sigmoid loss (learned temperature/bias) | 35.25% | 71.43% | 82.19% | 0.5091 | 19.38 ms |
| WiSE-FT | Weight-space ensemble (decoupled LR + zero-shot, α=0.35) | 37.85% | 73.28% | 85.53% | 0.5363 | 9.76 ms |
| **🏆 Champion — Decoupled LR** | ViT 0–5 frozen · vision 0.2× · text 1.0× · projection 2.0× | **39.15%** | **78.11%** | **87.20%** | **0.5592** | **14.01 ms** |

> **Key finding:** domain adaptation lifts Recall@1 from **26.53% to 39.15%** — **+12.62 points
> absolute, +47.57% relative** — and MRR from 0.4033 to 0.5592. The counter-intuitive result is that
> *decoupled learning rates* beat the structurally cleverer alternatives (LoRA, SigLIP), while the
> weight-space ensemble is the fastest at inference (9.76 ms) and is therefore the better choice when
> throughput matters more than top-1 precision.

The qualitative cases in §4 come from a separate error-mining pass over individual queries. They
illustrate *phenomena* that fine-tuning changes (fabric texture, cut geometry, surface sheen); they are
not a disaggregation of the aggregate numbers above.

---

## 4. Deep-Dive Qualitative Failure Case Analysis

To surface the inductive biases that domain fine-tuning changes, we reviewed queries whose top-ranked
results shifted after adaptation. Cases 1–2 use real catalog items (ASINs verified against
`data/processed/products.jsonl`); Cases 3–4 are stated as **general failure modes**, not tied to a
specific product. The exact rank numbers from the original local error-mining pass are
**illustrative** and are not reproduced by the committed 539-pair leaderboard in
[`multimodal_benchmark_results.json`](./multimodal_benchmark_results.json), so they are described
qualitatively here.

### Case 1: Specificity on Distressed & Frayed Denim
- **Test Item:** *Levi's Men's 501 Original-Fit Jean* (`ASIN: B000YXC2LI`, present in the catalog)
- **Query:** `"distressed raw blue denim with ripped knee accents and classic button fly straight leg"`
- **Base CLIP behaviour (ranked well below the top):**
  Base CLIP assigned higher affinity to dark-wash, pristine dress chinos and clean denim because the
  general token *"denim"* dominated the visual embedding while the sub-token modifier *"distressed/ripped"*
  was largely ignored.
- **Fine-tuned behaviour (moved into the top ranks):**
  Category-aware jeans batches pushed the vision encoder toward high-frequency edge textures (frayed
  cotton threads and localised wash contrasts), lifting the true positive sharply.

### Case 2: Structural Cut & Silhouette Geometry
- **Test Item:** *Dickies Men's Original 874 Work Pant* (`ASIN: B00028AVDG`, present in the catalog)
- **Query:** `"heavyweight 8.5 oz twill flat-front loose straight rise utility work pant"`
- **Base CLIP behaviour (confused with slim slacks sharing its khaki palette):**
  Base CLIP confused the structured 874 silhouette with slim-tapered modern dress slacks, as both share
  a neutral tan/khaki colour profile.
- **Fine-tuned behaviour (elevated into the top ranks):**
  The domain-adapted model associated *"8.5 oz twill"* with stiff, non-tapered geometric drape lines.

### Case 3: Outerwear Surface Sheen & Materiality (general failure mode)
- **Query:** `"waterproof hooded packable rain jacket nylon shell"`
- **Observed behaviour:** retrieval returned cotton fleece hoodies and softshell casual jackets that
  matched the query's navy colour profile, failing to separate matte cotton absorption from specular
  nylon reflection. Colour tends to dominate sheen unless the caption explicitly carries
  textile-composition bullets.

### Case 4: Remaining Failure Mode — Sub-brand Logo Invariance (general failure mode)
- **Observed limitation:** when several grey-heather sweatshirts are in the candidate pool, both base and
  fine-tuned CLIP struggle to discriminate a small embroidered brand logo from an otherwise identical
  unbranded garment. The failure is visual and local: the logo occupies a tiny fraction of the image.
- **Mitigation / next iteration:** high-resolution patch cropping (e.g. RoI Align or multi-crop
  augmentation) during training, to raise sensitivity to localised logos and embroidery.

---

## 5. Production Serving & Tri-Modal Architecture in Toko Marcell

To deploy these research gains into Toko Marcell's production stack without incurring costly GPU compute on every keystroke:

1. **Offline Pre-computation:**
   The catalog's product images have their visual embeddings pre-computed into PostgreSQL 16 using `pgvector` (`products.image_embedding vector(512)`), indexed with an **HNSW cosine index** ($M=16, \text{efConstruction}=64$).
2. **Online Query Encoding:**
   FastAPI exposes `embed_query_clip_text(query)` via ONNX runtime / PyTorch, taking ~15 ms per search.
3. **Tri-Modal Reciprocal Rank Fusion (RRF)** — equal weights over the three rankers, `k=60`,
   implemented in `manual/api/search.py` (and reused by `agent_tools.search_catalog`):
   ```python
   score(doc) = (
       1.0 / (60 + rank_bm25(doc)) +
       1.0 / (60 + rank_dense_minilm(doc)) +
       1.0 / (60 + rank_fashion_clip(doc))
   )
   ```
   This guarantees that exact product codes (e.g. *"501"*, *"874"*) are caught by BM25, broad semantics are caught by MiniLM, and visual styling cues are retrieved by Fine-Tuned CLIP.

---

## 6. Reproducibility & Code Artifacts

| Component | Path in Repository | Purpose |
|---|---|---|
| Dataset Preparation | `manual/pipelines/prepare_clip_dataset.py` | Validates 5,378 images, builds rich captions, generates 80/10/10 split |
| PyTorch Training Loop | `manual/pipelines/train_clip.py` | InfoNCE, category hard negative sampling, mixed precision AMP |
| Comparative Benchmark | `manual/pipelines/eval_multimodal.py` | Zero-shot vs fine-tuned evaluation (Recall@1, 5, 10, MRR, error mining) |
| Benchmark Output | `manual/pipelines/multimodal_benchmark_results.json` | Machine-readable metrics and top rank migration deltas |
| Model Weights | `manual/models/fashion_clip/` and `manual/pipelines/Best Model/` | Local champion/experiment checkpoints (gitignored — large binaries). The production **text encoder** is exported to ONNX and published at [`Marcell-Kristianto/toko-marcell-clip`](https://huggingface.co/Marcell-Kristianto/toko-marcell-clip), then fetched at runtime by the API |

---
*Authored for technical evaluation at PT. Kalbe Farma, Tbk. All benchmarks are reproducible on the Toko Marcell codebase.*
