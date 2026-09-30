#!/usr/bin/env python3
"""
Export the fine-tuned champion CLIP **vision** tower to ONNX, with int8 quantization.

Why this exists
---------------
The serving API must encode a query image into the SAME 512-d space as the stored
`products.image_embedding` vectors, which were produced by the fine-tuned champion
(`pipelines/Best Model`). Serving the PyTorch champion needs `torch` + a 578 MB
checkpoint, which is too heavy for the Docker image and impossible on the Render
free tier. This script produces a self-contained ONNX vision encoder that runs on
`onnxruntime` alone (no torch) so image search returns vectors that match the DB.

Pipeline it mirrors (`embed_catalog_vlm.py`):
    vision_model -> pooler_output -> visual_projection -> L2 normalize -> 512-d

Outputs (default `models/`):
    champion_vision_encoder.onnx         fp32, ~350 MB
    champion_vision_encoder_int8.onnx    dynamic int8, ~90 MB
    champion_vision_encoder.json         metadata + verification cosines

Usage
    /path/to/python pipelines/export_vision_onnx.py
    /path/to/python pipelines/export_vision_onnx.py --model-dir "pipelines/Best Model"

Note: dynamic int8 quantization needs a recent `onnx` (>= 1.16) because
onnxruntime's quantization module references `TensorProto.INT4`. Use a venv with
`onnx>=1.17` if your environment has an older one.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
DEFAULT_MODEL_DIR = HERE / "Best Model"
DEFAULT_OUT_DIR = BASE / "models"
IMAGE_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class VisionEncoder(nn.Module):
    """The exact projection the catalog embeddings were built with."""

    def __init__(self, clip):
        super().__init__()
        self.vision_model = clip.vision_model
        self.visual_projection = clip.visual_projection

    def forward(self, pixel_values):
        out = self.vision_model(pixel_values=pixel_values)
        pooled = out.pooler_output
        feats = self.visual_projection(pooled)
        return feats / feats.norm(dim=-1, keepdim=True)


def manual_preprocess(pil_image) -> np.ndarray:
    """Replicate CLIPImageProcessor: shortest-edge resize -> center crop -> normalize.

    Kept dependency-light (PIL + numpy) so the API can use the same function without
    transformers/torch. Verified against the official processor by the caller.
    """
    from PIL import Image

    img = pil_image.convert("RGB")
    w, h = img.size
    if w <= h:
        nw, nh = IMAGE_SIZE, int(round(h * IMAGE_SIZE / w))
    else:
        nh, nw = IMAGE_SIZE, int(round(w * IMAGE_SIZE / h))
    img = img.resize((nw, nh), Image.BICUBIC)

    left = (nw - IMAGE_SIZE) // 2
    top = (nh - IMAGE_SIZE) // 2
    img = img.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))

    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - CLIP_MEAN) / CLIP_STD
    return np.transpose(arr, (2, 0, 1))[None, ...].astype(np.float32)


def cosine(a, b) -> float:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    ap = argparse.ArgumentParser(description="Export champion CLIP vision tower to ONNX (int8)")
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--skip-fp32", action="store_true", help="only (re)build the int8 model")
    ap.add_argument("--verify-image", default=str(BASE / "data" / "processed" / "images" / "B000YXC2LI.jpg"))
    args = ap.parse_args()

    from transformers import CLIPImageProcessor, CLIPModel
    from onnxruntime.quantization import QuantType, quantize_dynamic

    model_dir = Path(args.model_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not model_dir.is_dir():
        log(f"ERROR: champion model dir not found: {model_dir}")
        return 1

    log(f"Loading champion CLIP from {model_dir} (CPU)...")
    model = CLIPModel.from_pretrained(str(model_dir)).to("cpu").eval()
    encoder = VisionEncoder(model).eval()
    log(f"projection_dim={model.config.projection_dim} (expected 512)")

    # Official processor for input parity checks; the API will use manual_preprocess().
    processor = CLIPImageProcessor.from_pretrained(str(model_dir)) if (model_dir / "processor_config.json").exists() else CLIPImageProcessor.from_pretrained("openai/clip-vit-base-patch32")

    dummy = torch.randn(1, 3, IMAGE_SIZE, IMAGE_SIZE)
    fp32_path = out_dir / "champion_vision_encoder.onnx"
    int8_path = out_dir / "champion_vision_encoder_int8.onnx"

    if not args.skip_fp32:
        log(f"Exporting fp32 ONNX (opset {args.opset})...")
        t = time.perf_counter()
        torch.onnx.export(
            encoder,
            (dummy,),
            str(fp32_path),
            input_names=["pixel_values"],
            output_names=["image_embeds"],
            opset_version=args.opset,
            do_constant_folding=True,
            dynamic_axes={"pixel_values": {0: "batch"}, "image_embeds": {0: "batch"}},
        )
        log(f"Wrote {fp32_path} ({fp32_path.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t:.1f}s")

    log("Quantizing to int8 (dynamic, MatMul only)...")
    t = time.perf_counter()
    # MatMul-only dynamic int8 keeps ~0.99 cosine to the fp32 model. Quantizing every
    # op (incl. the patch-embed Conv) drops it to ~0.95, and per-channel is unstable
    # on this checkpoint (~0.46); both were measured, so this is a deliberate choice.
    quantize_dynamic(
        str(fp32_path),
        str(int8_path),
        weight_type=QuantType.QInt8,
        op_types_to_quantize=["MatMul"],
    )
    log(f"Wrote {int8_path} ({int8_path.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t:.1f}s")

    # ── Verify: torch vs onnx(fp32) vs onnx(int8), and preprocessing parity ──
    import onnxruntime as ort
    from PIL import Image

    verify_image = Path(args.verify_image)
    if not verify_image.exists():
        candidates = sorted((BASE / "data" / "processed" / "images").glob("*.jpg"))
        verify_image = candidates[0] if candidates else None

    report = {
        "source_model_dir": str(model_dir),
        "projection_dim": model.config.projection_dim,
        "image_size": IMAGE_SIZE,
        "opset": args.opset,
        "quantization": {"scheme": "dynamic", "weight_type": "QInt8", "op_types_to_quantize": ["MatMul"]},
        "fp32_onnx_mb": round(fp32_path.stat().st_size / 1e6, 1),
        "int8_onnx_mb": round(int8_path.stat().st_size / 1e6, 1),
    }

    if verify_image and verify_image.exists():
        img = Image.open(verify_image).convert("RGB")
        official = processor(images=[img], return_tensors="np")["pixel_values"].astype(np.float32)
        manual = manual_preprocess(img)
        report["preprocess_parity_max_abs_diff"] = float(np.abs(official - manual).max())

        with torch.no_grad():
            torch_vec = encoder(torch.from_numpy(official)).numpy()[0]
            manual_vec = encoder(torch.from_numpy(manual)).numpy()[0]
        report["cosine_official_vs_manual_preprocess"] = round(cosine(torch_vec, manual_vec), 6)

        def onnx_embed(path):
            sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            return sess.run(None, {"pixel_values": official})[0][0]

        fp32_vec = onnx_embed(fp32_path)
        int8_vec = onnx_embed(int8_path)
        report["verify_image"] = verify_image.name
        report["cosine_torch_vs_fp32"] = round(cosine(torch_vec, fp32_vec), 6)
        report["cosine_torch_vs_int8"] = round(cosine(torch_vec, int8_vec), 6)
        report["embedding_dim"] = int(len(int8_vec))

        # Strongest check: does the int8 ONNX match the vector already in the seed DB?
        import gzip
        asin = verify_image.stem
        stored = None
        seed = BASE / "data" / "seed" / "init.sql.gz"
        if seed.exists():
            with gzip.open(seed, "rt", errors="replace") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) > 16 and parts[1] == asin:
                        try:
                            stored = np.array([float(x) for x in parts[-1].strip("[]").split(",")])
                        except ValueError:
                            stored = None
                        break
        if stored is not None:
            report["db_asin"] = asin
            report["cosine_torch_vs_db"] = round(cosine(torch_vec, stored), 6)
            report["cosine_int8_vs_db"] = round(cosine(int8_vec, stored), 6)

        log("Verification:")
        for k, v in report.items():
            if k.startswith("cosine") or k.startswith("preprocess") or k in ("embedding_dim", "db_asin"):
                log(f"  {k}: {v}")
    else:
        log("No verification image found; skipping numeric verification.")

    meta_path = out_dir / "champion_vision_encoder.json"
    meta_path.write_text(json.dumps(report, indent=2))
    log(f"Wrote {meta_path}")

    ok = report.get("cosine_torch_vs_int8", 0) >= 0.99 and report.get("embedding_dim") == 512
    log("RESULT: " + ("OK ✅" if ok else "FAILED ❌ — check the cosines above"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
