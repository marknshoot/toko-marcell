# `models/experiments/` — experiment artifacts (small files tracked, weights ignored)

This folder holds the **metadata** produced by the VLM contrastive fine-tuning study — model cards,
adapter configs and experiment summaries. Those are small, reviewable text files and are **tracked in
git**.

The **weights themselves are not tracked**: `adapter_model.safetensors`, `model.safetensors`,
`fashion_clip_checkpoint.pt` and any `*.onnx` stay local (`.gitignore`). The production text encoder is
published separately on Hugging Face Hub at
[`Marcell-Kristianto/toko-marcell-clip`](https://huggingface.co/Marcell-Kristianto/toko-marcell-clip)
and fetched by the API at runtime, so a fresh clone can serve search without shipping binaries.

## What is here

| Path | What it is |
|---|---|
| `lora/README.md` | Model card for the PEFT LoRA adapter (r=16, α=32) |
| `lora/adapter_config.json` | Exact PEFT/LoRA configuration |
| `lora/experiment_summary.json` | Local smoke-run metrics for the LoRA experiment |
| `siglip/experiment_summary.json` | Local smoke-run metrics for the SigLIP-loss experiment |
| `fashion_clip/training_history.json` | Local full fine-tune history (loss / validation Recall@1) |

> **Read this before quoting a number.** The JSON files here are **local smoke runs** (batch 8, 1
> epoch) used to sanity-check the training loop. Their test metrics are much higher than the real
> study because the evaluation slice was tiny. The **authoritative results** are the 539-pair Kaggle
> benchmark in [`../../pipelines/multimodal_benchmark_results.json`](../../pipelines/multimodal_benchmark_results.json)
> (Zero-shot 26.53% → Decoupled-LR champion 39.15% Recall@1), discussed in
> [`../../README.md`](../../README.md) and [`../../pipelines/README.md`](../../pipelines/README.md).

## How the weights are produced

```bash
# dataset
python3 pipelines/prepare_clip_dataset.py
# train (locally or on a free Kaggle GPU — see pipelines/kaggle/README.md)
python3 pipelines/experiments_clip.py --exp decoupled_lr --batch-size 64 --epochs 3
```

Outputs are written under `models/` (ignored); only the summaries above are committed.
