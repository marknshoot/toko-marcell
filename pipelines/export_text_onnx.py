#!/usr/bin/env python3
"""
Shrink the champion CLIP **text** encoder ONNX to fp16 for low-memory serving.

`models/champion_text_encoder.onnx` (fp32, ~254 MB) is the largest single model the
API loads (trimodal text->image search). On the Render free tier (512 MB) it is the
one that makes vision + text coexist risky. This produces a ~127 MB fp16 copy that is
numerically identical for retrieval — measured cosine **1.00000** vs fp32 across a set
of queries — and runs on ONNX Runtime CPU.

No re-export from PyTorch is needed: the fp32 ONNX already exists (and is on HF Hub).

Why fp16 and not int8
---------------------
Dynamic int8 was evaluated and **rejected**: it collapsed text-embedding cosine to
~0.77 mean / 0.62 min (MatMul-only 0.77; all-ops 0.77; QUInt8 0.80). The token
embedding table is quantization-sensitive, and the gain was not worth a quality loss
that would change retrieval rankings. fp16 halves the file with no measurable loss.

Interface (unchanged from fp32):
    inputs : input_ids (int64), attention_mask (int64)
    output : text_features (float [batch, 512])

Outputs (default `models/`):
    champion_text_encoder_fp16.onnx
    champion_text_encoder.json      metadata + verification cosines

Usage
    /path/to/python pipelines/export_text_onnx.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
DEFAULT_FP32 = BASE / "models" / "champion_text_encoder.onnx"
DEFAULT_OUT_DIR = BASE / "models"
MAX_LEN = 77

TEST_QUERIES = [
    "heavyweight 8.5 oz twill flat-front loose straight rise utility work pant",
    "distressed raw blue denim with ripped knee accents and classic button fly straight leg",
    "waterproof hooded packable rain jacket nylon shell",
    "breathable moisture-wicking athletic tee",
    "black leather chelsea boots for men",
    "floral summer maxi dress",
    "sepatu sneakers putih wanita",
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cosine(a, b) -> float:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    ap = argparse.ArgumentParser(description="Convert the champion text encoder ONNX to fp16")
    ap.add_argument("--fp32", default=str(DEFAULT_FP32))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--tokenizer", default=str(HERE / "Best Model"))
    args = ap.parse_args()

    import onnx
    from onnxconverter_common import float16
    import onnxruntime as ort

    fp32_path = Path(args.fp32)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not fp32_path.exists():
        log(f"ERROR: fp32 text encoder not found: {fp32_path}")
        log("(it is published at Marcell-Kristianto/toko-marcell-clip/champion_text_encoder.onnx)")
        return 1

    fp16_path = out_dir / "champion_text_encoder_fp16.onnx"

    log(f"Converting {fp32_path.name} ({fp32_path.stat().st_size / 1e6:.1f} MB) -> fp16...")
    t = time.perf_counter()
    model = onnx.load(str(fp32_path))
    model_fp16 = float16.convert_float_to_float16(model, keep_io_types=True)
    onnx.save(model_fp16, str(fp16_path))
    log(f"Wrote {fp16_path.name} ({fp16_path.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t:.1f}s")

    # ── Verify against the fp32 encoder on real queries ──
    from transformers import CLIPTokenizer

    tok = CLIPTokenizer.from_pretrained(args.tokenizer)

    def embed(session, text):
        enc = tok(text, padding="max_length", max_length=MAX_LEN, truncation=True, return_tensors="np")
        return session.run(
            None,
            {
                "input_ids": enc["input_ids"].astype(np.int64),
                "attention_mask": enc["attention_mask"].astype(np.int64),
            },
        )[0][0]

    fp32 = ort.InferenceSession(str(fp32_path), providers=["CPUExecutionProvider"])
    fp16 = ort.InferenceSession(str(fp16_path), providers=["CPUExecutionProvider"])

    cosines = [cosine(embed(fp32, q), embed(fp16, q)) for q in TEST_QUERIES]
    sample = embed(fp16, TEST_QUERIES[0])

    report = {
        "source_fp32": str(fp32_path),
        "fp32_onnx_mb": round(fp32_path.stat().st_size / 1e6, 1),
        "fp16_onnx_mb": round(fp16_path.stat().st_size / 1e6, 1),
        "conversion": "float32 -> float16 (keep_io_types=True)",
        "output_dim": int(len(sample)),
        "fp16_output_norm": round(float(np.linalg.norm(sample)), 6),
        "cosine_fp32_vs_fp16_mean": round(float(np.mean(cosines)), 6),
        "cosine_fp32_vs_fp16_min": round(float(np.min(cosines)), 6),
        "num_verify_queries": len(TEST_QUERIES),
        "rejected_alternative": "dynamic int8 (cosine ~0.77 mean / 0.62 min; token embeddings are quantization-sensitive)",
    }
    log("Verification:")
    for k in ("output_dim", "fp16_output_norm", "cosine_fp32_vs_fp16_mean", "cosine_fp32_vs_fp16_min"):
        log(f"  {k}: {report[k]}")

    (out_dir / "champion_text_encoder.json").write_text(json.dumps(report, indent=2))
    log(f"Wrote {out_dir / 'champion_text_encoder.json'}")

    ok = report["cosine_fp32_vs_fp16_min"] >= 0.9999 and report["output_dim"] == 512
    log("RESULT: " + ("OK ✅" if ok else "FAILED ❌"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
