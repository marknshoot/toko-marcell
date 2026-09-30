# `pipelines/kaggle/` — running the VLM study on a free Kaggle GPU

This folder packages the multimodal contrastive fine-tuning study so it can be run end-to-end on
**Kaggle's free GPU tier (NVIDIA Tesla T4 16 GB or P100 16 GB)** from the command line, without
owning a GPU.

The study trains and evaluates four CLIP adaptation strategies against a zero-shot baseline and
produces the `experiment_summary` files and the [leaderboard](../multimodal_benchmark_results.json)
used in the top-level README:

- **Baseline / full fine-tune** — symmetric InfoNCE, uniform learning rate
- **LoRA** — PEFT adapters on `q_proj`, `v_proj` (r=16, α=32, dropout 0.1)
- **Decoupled LR** — ViT layers 0–5 frozen, asymmetric LRs per module (the champion)
- **SigLIP** — pairwise sigmoid loss replacing the batch softmax
- **WiSE-FT** — weight-space ensemble of the fine-tuned and zero-shot weights (α = 0.35), plus
  test-time prompt ensembling

---

## Files

| File | Purpose |
|---|---|
| `toko_marcell_vlm_experiments.ipynb` | The full notebook (problem framing, data pipeline, training, plots, qualitative demo) |
| `generate_notebook.py` | Programmatically regenerates the `.ipynb` (keeps it reviewable as code) |
| `kernel-metadata.json` | Kaggle kernel config: GPU on, internet on, dataset attached |
| `dataset_upload/dataset-metadata.json` | Kaggle dataset config |
| `dataset_upload/clip_{train,val,test}.json` | The deterministic 80/10/10 splits (committed) |
| `run_logs/` | Captured run logs (e.g. a torchao import conflict and its workaround) |

Current Kaggle IDs (already filled in — change them if you fork):

```json
// dataset_upload/dataset-metadata.json
{ "id": "mar096/toko-marcell-fashion-vlm" }        // title: "Toko Marcell Fashion VLM Dataset"

// kernel-metadata.json
{ "id": "mar096/toko-marcell-vlm-experiments" }    // GPU on, internet on, dataset attached
```

---

## 1. One-time authentication

If you already have the Kaggle CLI configured, skip this. Otherwise either:

**A. Browser login (simplest)**

```bash
kaggle auth login
```

**B. API token file**

1. Open [https://www.kaggle.com/settings/api](https://www.kaggle.com/settings/api) and click
   **Create New Token** (downloads `kaggle.json`).
2. Install it:

```bash
mkdir -p ~/.kaggle
mv ~/Downloads/kaggle.json ~/.kaggle/
chmod 600 ~/.kaggle/kaggle.json
```

> If the CLI lives in a conda env (e.g. `deep-learning`), call it by full path:
> `/path/to/miniconda3/envs/deep-learning/bin/kaggle …`.

---

## 2. Build the lightweight dataset (~165 MB)

The notebook needs the three JSON splits plus a tar of the cached images. From `manual/`:

```bash
mkdir -p pipelines/kaggle/dataset_upload
cp data/processed/clip_train.json pipelines/kaggle/dataset_upload/
cp data/processed/clip_val.json   pipelines/kaggle/dataset_upload/
cp data/processed/clip_test.json  pipelines/kaggle/dataset_upload/
tar -czf pipelines/kaggle/dataset_upload/images.tar.gz -C data/processed images
```

`dataset_upload/dataset-metadata.json` is already present. Create/update the dataset:

```bash
# first time
kaggle datasets create -p pipelines/kaggle/dataset_upload

# subsequent updates
kaggle datasets version -p pipelines/kaggle/dataset_upload -m "refresh splits"
```

The splits are deterministic (seed 42) and committed, so a reviewer can inspect them without running
the build. The images are cached locally by
[`../download_images.py`](../download_images.py) first.

---

## 3. Push and run the kernel

`kernel-metadata.json` already enables the GPU and the internet. Push the notebook:

```bash
kaggle kernels push -p pipelines/kaggle/
```

Watch progress:

```bash
kaggle kernels status mar096/toko-marcell-vlm-experiments
```

Or open it in the browser: [https://www.kaggle.com/code](https://www.kaggle.com/code).

---

## 4. Download the outputs

When the status reports `complete`, pull every output (checkpoints, `safetensors`,
`experiment_summary_3ep_final.json`, plots):

```bash
mkdir -p models/kaggle_outputs
kaggle kernels output mar096/toko-marcell-vlm-experiments -p models/kaggle_outputs/
```

Model **weights** are gitignored (large binaries); the small model cards and experiment summaries
under `models/experiments/` are tracked. The production artifact that matters — the
champion's ONNX **text** encoder — is published separately on Hugging Face Hub at
[`Marcell-Kristianto/toko-marcell-clip`](https://huggingface.co/Marcell-Kristianto/toko-marcell-clip)
and fetched by the API at runtime.

---

## 5. Running it in the Kaggle web UI instead

No CLI required:

1. Open [https://www.kaggle.com/code](https://www.kaggle.com/code) → **New Notebook**.
2. In **Settings** set **Accelerator = GPU T4 x2** (or P100) and **Internet = On**.
3. **File → Import Notebook** → choose `pipelines/kaggle/toko_marcell_vlm_experiments.ipynb`.
4. **+ Add Input** → attach the `toko-marcell-fashion-vlm` dataset.
5. **Run All**.

---

## Environment notes (things that will otherwise cost an hour)

- **torchao conflict.** Some Kaggle images ship an incompatible `torchao`, which makes `peft` fail at
  adapter injection with
  `ImportError: Found an incompatible version of torchao. Found version 0.10.0, but only versions above 0.16.0 are supported`.
  The notebook's first cell works around it by stubbing `sys.modules["torchao"] = None` and running
  `pip uninstall -y torchao` before importing `peft`. The failure is captured in
  [`run_logs/`](./run_logs/).
- **Batch size.** The study used batch 64 on a 16 GB T4 with AMP FP16. On a smaller card, lower the
  batch size — contrastive learning degrades with batch size, which is exactly why the study ran on
  Kaggle rather than the local 4 GB GPU.
- **Regenerating the notebook.** Edit `generate_notebook.py` and re-run it rather than hand-editing
  the `.ipynb`, so the notebook stays reviewable as code in a diff.

---

## What to read after a run

- [`../multimodal_benchmark_results.json`](../multimodal_benchmark_results.json) — the 5-model
  leaderboard (Recall@1/5/10, MRR, latency) and the champion's deltas.
- [`../failure_case_analysis.md`](../failure_case_analysis.md) — qualitative error analysis.
- [`../literature_review_clip_finetuning.md`](../literature_review_clip_finetuning.md) — the paper
  basis for each experiment.
- [`../../README.md`](../../README.md) — how the champion is served in production.
