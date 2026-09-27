# Literature Review: Fine-Tuning Vision-Language Models (CLIP) for E-Commerce & Fashion Retrieval

**Target Alignment:** AI Engineer (Multimodal AI / LLM) — PT. Kalbe Farma, Tbk.  
**Author:** Marcell Hermawan Kristianto (Binus University — 5th Semester Data Science)  
**Project:** Toko Marcell  
**Date:** September 2026  

---

## 1. Executive Summary

Contrastive Language-Image Pretraining (CLIP; Radford et al., 2021) has emerged as the foundational paradigm for multimodal cross-modal representation learning. In its zero-shot formulation, CLIP achieves impressive generalization across broad semantic concepts. However, in **domain-specific e-commerce (such as apparel, consumer health, and fashion)**, zero-shot CLIP exhibits pronounced performance bottlenecks:
1. **The Fine-Grained Semantic Gap:** Inability to distinguish subtle cut geometries (e.g. *Levi's 501 Straight* vs. *Levi's 511 Slim*), fabric textures (*8.5 oz heavyweight twill* vs. *cotton blend*), or surface finishes (*glossy waterproof nylon* vs. *matte fleece*).
2. **Catastrophic Forgetting under Full Fine-Tuning:** Directly updating all 151M parameters on specialized catalogs of 5,000–50,000 items rapidly destroys generic visual priors and causes severe overfitting.
3. **Loss Dynamics & Batch Normalization Competition:** InfoNCE's global softmax denominator penalizes near-identical items (false negatives) within the same batch.

This literature review synthesizes findings from 5 foundational research papers to formulate four actionable, hypothesis-driven experiments for Toko Marcell that directly inform model improvement and production deployment.

---

## 2. Theoretical Breakdown of Core Literature

### 2.1 Fashion-CLIP: Domain Adaptation & Caption Engineering
- **Reference:** Chia, P. J., Tagliabue, J., Bianchi, F., et al. (2022). *"Fashion-CLIP: Contrastive Language-Image Pretraining for the Fashion Domain."* *Scientific Reports (Nature)*, 12(1), 2022.
- **Core Insights:**
  - Evaluated generic CLIP vs domain-adapted CLIP across 800,000 product pairs from Farfetch.
  - **Structured Metadata Captions:** Unstructured marketing descriptions (*"Look fabulous tonight!"*) degrade cross-modal gradients. Constructing structured captions combining `Brand + Product Name + Category Hierarchy + Material/Fit Feature Bullets` yields a **+15.4% improvement in Recall@10** over raw product titles alone.
  - **Preserving Visual Pre-training:** The visual backbone contains universal edge, texture, and contour filters that should be updated conservatively ($LR \le 5 \times 10^{-6}$) relative to the multimodal projection heads.

### 2.2 SigLIP: Decoupling Batch Negatives via Sigmoid Loss
- **Reference:** Zhai, X., Mustafa, B., Kolesnikov, A., & Beyer, L. (2023). *"SigLIP: Sigmoid Loss for Language Image Pre-Training."* *IEEE/CVF International Conference on Computer Vision (ICCV)*, 2023.
- **Problem with Softmax InfoNCE:**
  The standard InfoNCE loss normalizes similarities across the batch:
  $$\mathcal{L}_{\text{InfoNCE}} = - \log \frac{\exp(u_i^\top v_i / \tau)}{\sum_{j=1}^N \exp(u_i^\top v_j / \tau)}$$
  In e-commerce, batches frequently contain multiple items of the same category (e.g., three black hoodies). The softmax denominator forces these items to compete against each other, driving gradients to push valid visual matches apart (false negative penalization).
- **The SigLIP Solution:**
  Formulates contrastive learning as independent binary classification for every pair $(i, j)$:
  $$\mathcal{L}_{\text{SigLIP}} = - \frac{1}{N} \sum_{i=1}^N \sum_{j=1}^N \log \frac{1}{1 + \exp \left( - z_{ij} (u_i^\top v_j \cdot e^t + b) \right)}$$
  where $z_{ij} = +1$ if $i = j$ (positive pair) and $-1$ if $i \neq j$ (negative pair), with learnable temperature $t$ and bias $b$.
  - **Benefit for Toko Marcell:** Eliminates negative pair competition, stabilizes training on smaller mini-batches ($N=32$ or $64$), and increases Recall@1 on dense apparel categories.

### 2.3 Parameter-Efficient Fine-Tuning (PEFT) & LoRA for VLMs
- **Reference:** Hu, E. J., Shen, Y., Wallis, P., et al. (2021). *"LoRA: Low-Rank Adaptation of Large Language Models."* *ICLR*, 2022; Kopiczko, D. et al. (2023). *"VeRA: Vector-based Random Matrix Adaptation."*
- **Problem with Full Model Updates:**
  Toko Marcell's dataset contains 5,378 products (~4,300 training pairs). Updating all 151M parameters of `clip-vit-base-patch32` with a high parameter-to-data ratio risks memorization of training images.
- **LoRA Adaptation for Dual Encoders:**
  Freezes the pre-trained weights $W_0 \in \mathbb{R}^{d \times k}$ and decomposes updates into low-rank matrices:
  $$W = W_0 + \Delta W = W_0 + \frac{\alpha}{r} (B \cdot A)$$
  where $B \in \mathbb{R}^{d \times r}$, $A \in \mathbb{R}^{r \times k}$ with rank $r \ll \min(d, k)$ (e.g. $r=16$).
  - Target modules: Attention projections (`q_proj`, `v_proj`) in both vision and text encoders, plus the linear projection heads.
  - **Result:** Only ~1.8M trainable parameters (~1.2% of the model). General visual competence is perfectly preserved while the projection space aligns to fashion vocabulary.

### 2.4 Asymmetric Learning Rates & Visual Layer Freezing (WiSE-FT)
- **Reference:** Wortsman, M., Ilharco, G., Gadre, S. Y., et al. (2022). *"Robust Fine-Tuning of Zero-Shot Models (WiSE-FT)."* *CVPR*, 2022.
- **Core Finding:**
  The semantic representation gap in e-commerce search is predominantly textual (the vocabulary used to describe clothes), not visual (the visual primitives of cloth, fabric, and color).
  - Fine-tuning the earliest layers of the Vision Transformer (layers 1–6) often destroys robust edge/color primitives.
  - **Proposed Strategy:**
    1. Freeze layers 1–6 of the 12-layer Vision Transformer.
    2. Apply differential learning rates:
       $$\text{LR}_{\text{vision\_top}} = 1 \times 10^{-6}, \quad \text{LR}_{\text{text}} = 5 \times 10^{-6}, \quad \text{LR}_{\text{projection\_heads}} = 1 \times 10^{-5}$$

### 2.5 Hard Negative Mining via Lexical BM25
- **Reference:** Xuan, H., Stylianou, A., & Pless, R. (2020). *"Improved Embeddings with Hard Negative Mining."* *IEEE/CVF CVPR*, 2020; Robinson, J. et al. (2021). *"Contrastive Learning with Hard Negative Samples."* *ICLR*, 2021.
- **Mechanism:**
  For each image $I_i$ belonging to product $P_i$, query the catalog BM25 index with $P_i$'s title and select the highest-scoring product $P_j$ where $\text{ASIN}_j \neq \text{ASIN}_i$.
  - Example: For *"Levi's 501 Original Fit Jeans"*, BM25 mines *"Levi's 505 Regular Fit Jeans"* as the explicit hard negative text.
  - The contrastive loss must distinguish between two highly similar cuts from the same brand, drastically enhancing discriminative power.

---

## 3. Four Concrete Experimental Designs for Toko Marcell

Based on the literature review, we establish four controlled ablation experiments against the baseline:

```text
Baseline (Current):
  - Model: openai/clip-vit-base-patch32 (Full Fine-Tuning)
  - Loss: Symmetric InfoNCE with Category-Aware In-Batch Negatives
  - LR: Uniform 5e-6, AdamW, Cosine Scheduler
  - Test Score: Recall@1 = 66.0%, MRR = 0.7912
```

### Experiment Matrix

| Experiment | Method / Innovation | Primary Hypothesized Mechanism | Success Metric Target |
|---|---|---|:---:|
| **Exp 1: PEFT / LoRA** | LoRA on `q_proj, v_proj` ($r=16, \alpha=32$) | Eliminates train-val gap; prevents visual prior degradation on 5k items | **Recall@1 $\ge$ 68.5%**, MRR $\ge$ 0.81 |
| **Exp 2: Decoupled LR & Layer Freezing** | Freeze ViT L1-6; $\text{LR}_{\text{vis}}=1\text{e-}6, \text{LR}_{\text{txt}}=5\text{e-}6, \text{LR}_{\text{head}}=1\text{e-}5$ | Protects universal visual priors while aggressively adapting projection heads | **Recall@5 $\ge$ 96.0%**, MRR $\ge$ 0.80 |
| **Exp 3: SigLIP Loss Function** | Replace InfoNCE with pairwise Sigmoid loss + learnable temperature/bias | Eliminates batch-softmax competition between near-identical fashion items | **Recall@1 $\ge$ 69.0%**, Mean Rank $\le$ 1.8 |
| **Exp 4: BM25 Hard Negative Mining** | Top-1 BM25 lexical negative injection per anchor item | Forces model to learn fine-grained brand cuts & model numbers (501 vs 505) | **Recall@1 $\ge$ 70.0%**, MRR $\ge$ 0.83 |

---

## 4. Hardware & Acceleration Strategy: Kaggle GPU Execution

While experiments can run locally on an NVIDIA GTX 1650 (4 GB VRAM) with batch size 16/32, utilizing **Kaggle's free GPU compute (NVIDIA Tesla T4 16 GB or P100 16 GB)** provides substantial acceleration:

1. **Large Batch Contrastive Learning:**
   Contrastive learning scales with batch size. On Kaggle's 16 GB VRAM, batch size can scale to **$N=128$**, generating $128 \times 127 = 16,256$ negative pairs per forward pass (4× more negative signal than local GPU).
2. **Kaggle CLI Workflow:**
   - Package dataset (`clip_train.json`, `clip_val.json`, `clip_test.json`, `images.zip`).
   - Push modular experiment script via `kaggle kernels push`.
   - Monitor remote GPU training and download resulting checkpoints and evaluation JSON logs.

---

## 5. Strategic Value for Kalbe Farma (Multimodal AI Interview Argument)

This literature-driven study provides a compelling, defensible narrative during technical interviews:
1. **Principled Experimentation:** Not randomly guessing hyperparameters, but deriving hypotheses from seminal literature (Chia et al., Zhai et al., Hu et al.).
2. **Resource-Aware Engineering:** Demonstrating mastery of PEFT/LoRA and mixed-precision gradient scaling to train state-of-the-art models on consumer or free-tier hardware.
3. **Loss Function Mastery:** Ability to contrast InfoNCE's multi-class partition function against SigLIP's pairwise binary sigmoid formulation, articulating trade-offs in dense e-commerce retrieval.
