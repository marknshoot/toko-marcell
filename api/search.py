"""
Lexical search: BM25 over the product catalog.

Why hand-written and in-process
    The plan (PLAN §6) calls for an in-process BM25 baseline, and a baseline is
    only useful if it is a *real* implementation — the hybrid ranker has to beat
    something honest. At 6,000 documents the whole index lives comfortably in
    memory and rebuilds in well under a second, so there is nothing to gain from
    an external search service and a dependency to avoid.

Scoring
    Robertson & Zaragoza, "The Probabilistic Relevance Framework: BM25 and
    Beyond" (2009), with the usual k1=1.5, b=0.75 and the +1 inside the IDF log
    so that a term appearing in every document scores 0 rather than negative.

Field weighting
    A product's title matters far more than its long marketing description, so
    each field's term frequencies are scaled before scoring (title 3.0, brand
    2.0, category 1.5, features 1.0, description 0.5). Weighted counts are not
    "counts" in the textbook sense, but BM25's saturation handles fractional term
    frequencies fine, and this is a standard practical alternative to running a
    separate index per field.

Known simplifications (deliberate, and worth stating out loud)
    * `stem()` is a small suffix folder, not a real stemmer (no Porter/Snowball).
      It handles the plurals that dominate shopping queries — sneakers/sneaker,
      jeans/jean, shoes/shoe, watches/watch — and nothing more.
    * No phrase matching, no typo tolerance, no synonym expansion. Typo tolerance
      and semantics are exactly what the embedding half of hybrid search adds.
"""

import logging
import math
import os
import re
import threading
import unicodedata
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "this to was were will with you your".split()
)

FIELD_WEIGHTS = {
    "title": 3.0,
    "brand": 2.0,
    "category": 1.5,
    "department": 1.0,
    "features": 1.0,
    "description": 0.5,
}


def stem(token: str) -> str:
    """Fold the common English plurals."""
    if not token.isalpha():
        return token
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and token[:-2].endswith(("ch", "sh", "ss", "x", "z")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """lowercase -> fold accents -> alphanumeric tokens -> drop stopwords -> fold plurals."""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKD", text.lower())
    tokens = TOKEN_RE.findall(normalized)
    return [stem(t) for t in tokens if t not in STOPWORDS and len(t) > 1]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: list = []
        self.doc_keys: list = []
        self.id_to_key: dict = {}
        self.doc_lengths: dict = {}
        self.postings: dict[str, dict] = defaultdict(dict)
        self.avg_length = 0.0
        self._built = False

    def add(self, doc_id, fields: dict, dedupe_key: str | None = None) -> None:
        """Index one document. `fields` maps a FIELD_WEIGHTS key to text.

        `dedupe_key` groups documents that should not both appear in a result
        list — 842 of the 6,000 products are variants that share a title with a
        different ASIN, and showing the same title twice is noise, not choice.
        """
        index = len(self.doc_ids)
        self.doc_ids.append(doc_id)
        self.doc_keys.append(dedupe_key)
        self.id_to_key[doc_id] = dedupe_key

        weighted = Counter()
        length = 0.0
        for field, text in fields.items():
            weight = FIELD_WEIGHTS.get(field, 1.0)
            tokens = tokenize(text)
            if not tokens:
                continue
            for token, count in Counter(tokens).items():
                weighted[token] += count * weight
            length += len(tokens)

        self.doc_lengths[index] = length
        for term, tf in weighted.items():
            self.postings[term][index] = tf

    def build(self) -> None:
        total = sum(self.doc_lengths.values())
        self.avg_length = total / len(self.doc_ids) if self.doc_ids else 0.0
        self._built = True

    @property
    def size(self) -> int:
        return len(self.doc_ids)

    def _idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        if df == 0:
            return 0.0
        n = len(self.doc_ids)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> dict:
        """BM25 score for every document matching at least one query term."""
        if not self._built:
            self.build()

        scores: dict[int, float] = defaultdict(float)
        k1, b, avg = self.k1, self.b, self.avg_length or 1.0

        for term in set(tokenize(query)):
            postings = self.postings.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            for doc_index, tf in postings.items():
                length_norm = 1.0 - b + b * (self.doc_lengths[doc_index] / avg)
                scores[doc_index] += idf * (tf * (k1 + 1.0)) / (tf + k1 * length_norm)

        return scores

    def rank(self, query: str, allowed=None, dedupe: bool = False):
        """Full ranked list of (doc_id, score), best first.

        `allowed` optionally restricts the result to a set of doc ids (a facet
        filter). `dedupe` keeps only the best-scoring document per dedupe_key.
        Pagination is left to the caller, because dedupe has to happen before the
        page is cut — otherwise "total" would count rows the shopper never sees.
        """
        scores = self.score(query)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

        results = []
        seen = set()
        for doc_index, value in ranked:
            doc_id = self.doc_ids[doc_index]
            if allowed is not None and doc_id not in allowed:
                continue
            if dedupe:
                key = self.doc_keys[doc_index]
                if key:
                    if key in seen:
                        continue
                    seen.add(key)
            results.append((doc_id, round(value, 4)))

        return results

    def dedupe(self, items: list[tuple[int, float]]) -> list[tuple[int, float]]:
        """Deduplicate a list of (doc_id, score) by dedupe_key, preserving rank order."""
        seen = set()
        deduped = []
        for doc_id, score in items:
            key = self.id_to_key.get(doc_id)
            if key:
                if key in seen:
                    continue
                seen.add(key)
            deduped.append((doc_id, score))
        return deduped


_INDEX = None
_INDEX_LOCK = threading.Lock()


def build_search_index(db_url: str | None = None) -> BM25Index:
    """Read the catalog from PostgreSQL and build the in-process BM25 index."""
    import psycopg
    url = db_url or os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, brand, category, department, features, description
                FROM products
                """
            )
            rows = cur.fetchall()

    index = BM25Index()
    for product_id, title, brand, category, department, features, description in rows:
        index.add(
            product_id,
            {
                "title": title or "",
                "brand": brand or "",
                "category": category or "",
                "department": department or "",
                "features": " ".join(features or []),
                "description": description or "",
            },
            dedupe_key=(title or "").strip().lower(),
        )
    index.build()
    logger.info("BM25 index built: %d documents", index.size)
    return index


def get_index(builder=None):
    """Build the index once, on first use.

    Lazy on purpose: a database problem must not stop the API from booting, and
    browsing the catalog does not need a search index at all.
    """
    global _INDEX
    if _INDEX is None:
        with _INDEX_LOCK:
            if _INDEX is None:
                b = builder or build_search_index
                _INDEX = b()
    return _INDEX


def reset_index():
    """Forget the cached index so the next search rebuilds it (after a reseed)."""
    global _INDEX
    with _INDEX_LOCK:
        _INDEX = None


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int, float]]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion (RRF).

    RRF score = sum(1 / (k + rank)) for each ranker where the document appears.
    k=60 is the standard constant from Cormack, Clarke & Buettcher (2009).
    It avoids the need to calibrate or normalize disparate score distributions
    (e.g., BM25 scores are unbounded positive numbers, cosine similarity is -1 to 1).
    """
    scores = defaultdict(float)
    for rank_list in rankings:
        for rank, (doc_id, _) in enumerate(rank_list):
            scores[doc_id] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


_EMBED_MODEL = None
_EMBED_LOCK = threading.Lock()


def get_embed_model():
    """Load the fastembed sentence transformer lazily."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        with _EMBED_LOCK:
            if _EMBED_MODEL is None:
                from fastembed import TextEmbedding
                cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", "/tmp/fastembed_cache")
                _EMBED_MODEL = TextEmbedding(
                    model_name="sentence-transformers/all-MiniLM-L6-v2",
                    cache_dir=cache_dir,
                )
    return _EMBED_MODEL


def embed_query(query: str) -> list[float] | None:
    """Generate 384-dim vector for search query. Returns None if embedding fails."""
    try:
        model = get_embed_model()
        vectors = list(model.embed([query]))
        return [float(x) for x in vectors[0]]
    except Exception as e:
        logger.warning("embed_query failed: %s", e)
        return None


_CHAMPION_CLIP_MODEL = None
_CHAMPION_CLIP_PROCESSOR = None
_CHAMPION_CLIP_FAILED = False
_CHAMPION_CLIP_LOCK = threading.Lock()


def get_champion_clip():
    """Load local fine-tuned Champion CLIP model if present."""
    global _CHAMPION_CLIP_MODEL, _CHAMPION_CLIP_PROCESSOR, _CHAMPION_CLIP_FAILED
    if _CHAMPION_CLIP_MODEL is None and not _CHAMPION_CLIP_FAILED:
        with _CHAMPION_CLIP_LOCK:
            if _CHAMPION_CLIP_MODEL is None and not _CHAMPION_CLIP_FAILED:
                here = os.path.dirname(os.path.abspath(__file__))
                candidates = [
                    os.environ.get("CLIP_MODEL_DIR"),
                    os.path.join(here, "models", "best_champion_model"),
                    os.path.join(here, "..", "pipelines", "Best Model"),
                    os.path.join(here, "..", "models", "best_champion_model"),
                    os.path.join(here, "..", "models", "fashion_clip"),
                ]
                for candidate in candidates:
                    if candidate and os.path.isdir(candidate):
                        try:
                            import torch
                            from transformers import CLIPModel, CLIPProcessor
                            device = "cuda" if torch.cuda.is_available() else "cpu"
                            model = CLIPModel.from_pretrained(candidate).to(device)
                            model.eval()
                            processor = CLIPProcessor.from_pretrained(candidate)
                            _CHAMPION_CLIP_MODEL = model
                            _CHAMPION_CLIP_PROCESSOR = processor
                            logger.info("Loaded Champion CLIP Model from %s on %s", candidate, device)
                            break
                        except Exception as e:
                            logger.warning("Could not load champion CLIP from %s: %s", candidate, e)
                if _CHAMPION_CLIP_MODEL is None:
                    _CHAMPION_CLIP_FAILED = True
    return _CHAMPION_CLIP_MODEL, _CHAMPION_CLIP_PROCESSOR


def vision_encoder_ready() -> bool:
    """True if a fine-tuned champion vision encoder is available (ONNX or torch).

    Image search has **no** zero-shot fallback on purpose: the stored
    `products.image_embedding` vectors come from the fine-tuned champion, so mixing
    in a different encoder's vectors would silently return wrong results.
    """
    session, _ = get_onnx_champion_vision()
    if session is not None:
        return True
    model, _ = get_champion_clip()
    return model is not None


_CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
_CLIP_STD = (0.26862954, 0.26130258, 0.27577711)
_CLIP_IMAGE_SIZE = 224


def _preprocess_clip_image(img):
    """Replicate CLIPImageProcessor: shortest-edge resize -> center crop -> normalize.

    Matches the processor used to build the stored `image_embedding` vectors; verified
    to yield identical embeddings (cosine 1.0) to transformers' CLIPImageProcessor.
    Uses only PIL + numpy so no torch/transformers is needed at serving time.
    """
    import numpy as np
    from PIL import Image

    img = img.convert("RGB")
    w, h = img.size
    if w <= h:
        nw, nh = _CLIP_IMAGE_SIZE, int(round(h * _CLIP_IMAGE_SIZE / w))
    else:
        nh, nw = _CLIP_IMAGE_SIZE, int(round(w * _CLIP_IMAGE_SIZE / h))
    img = img.resize((nw, nh), Image.BICUBIC)

    left = (nw - _CLIP_IMAGE_SIZE) // 2
    top = (nh - _CLIP_IMAGE_SIZE) // 2
    img = img.crop((left, top, left + _CLIP_IMAGE_SIZE, top + _CLIP_IMAGE_SIZE))

    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - np.array(_CLIP_MEAN, dtype=np.float32)) / np.array(_CLIP_STD, dtype=np.float32)
    return np.transpose(arr, (2, 0, 1))[None, ...].astype(np.float32)


_ONNX_VISION_SESSION = None
_ONNX_VISION_PATH = None
_ONNX_VISION_FAILED = False
_ONNX_VISION_LOCK = threading.Lock()


def get_onnx_champion_vision():
    """Load the champion ONNX **vision** encoder (int8 preferred) — no torch required.

    This is the correct encoder to match the stored `products.image_embedding`
    vectors, which were produced by the fine-tuned champion. Falls back to Hugging
    Face Hub (`HF_MODEL_REPO`) when no local copy is mounted.
    """
    global _ONNX_VISION_SESSION, _ONNX_VISION_PATH, _ONNX_VISION_FAILED
    if _ONNX_VISION_SESSION is not None:
        return _ONNX_VISION_SESSION, _ONNX_VISION_PATH
    if _ONNX_VISION_FAILED:
        return None, None
    with _ONNX_VISION_LOCK:
        if _ONNX_VISION_SESSION is not None:
            return _ONNX_VISION_SESSION, _ONNX_VISION_PATH
        if _ONNX_VISION_FAILED:
            return None, None
        try:
            import onnxruntime as ort

            here = os.path.dirname(os.path.abspath(__file__))
            prefer = os.environ.get("CLIP_VISION_ONNX_PREFER", "int8").lower()
            names = (
                ["champion_vision_encoder_int8.onnx", "champion_vision_encoder.onnx"]
                if prefer != "fp32"
                else ["champion_vision_encoder.onnx", "champion_vision_encoder_int8.onnx"]
            )

            candidates = []
            if os.environ.get("CLIP_VISION_ONNX_PATH"):
                candidates.append(os.environ["CLIP_VISION_ONNX_PATH"])
            for name in names:
                candidates += [
                    os.path.join("/models", name),
                    os.path.join(here, "..", "models", name),
                    os.path.join(here, "models", name),
                    os.path.join(here, name),
                ]
            path = next((c for c in candidates if c and os.path.exists(c)), None)

            if not path:
                try:
                    from huggingface_hub import hf_hub_download
                    repo_id = os.environ.get("HF_MODEL_REPO", "Marcell-Kristianto/toko-marcell-clip")
                    for name in names:
                        try:
                            path = hf_hub_download(repo_id=repo_id, filename=name)
                            break
                        except Exception:
                            continue
                except Exception as e:
                    logger.info("HF vision encoder download note: %s", e)

            if path and os.path.exists(path):
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                opts.inter_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                _ONNX_VISION_SESSION = ort.InferenceSession(
                    path, opts, providers=["CPUExecutionProvider"]
                )
                _ONNX_VISION_PATH = path
                logger.info("Loaded champion ONNX vision encoder: %s", os.path.basename(path))
            else:
                logger.info("No champion ONNX vision encoder found; trying torch/fastembed")
                _ONNX_VISION_FAILED = True
        except Exception as e:
            logger.warning("Could not initialize ONNX vision encoder: %s", e)
            _ONNX_VISION_FAILED = True
    return _ONNX_VISION_SESSION, _ONNX_VISION_PATH


def embed_image_bytes(image_bytes: bytes) -> list[float] | None:
    """Generate a 512-dim CLIP vision embedding for an uploaded image.

    Order matters: the champion ONNX vision encoder runs first because it matches the
    fine-tuned vectors stored in the database. It needs no torch. The torch champion
    is used in dev environments, and the zero-shot fastembed model is only a
    last-resort fallback (its space differs from the stored champion embeddings).
    """
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        # 0. Champion ONNX vision encoder (correct space, no torch)
        session, _ = get_onnx_champion_vision()
        if session is not None:
            try:
                import numpy as np

                pixel_values = _preprocess_clip_image(img)
                feats = session.run(None, {"pixel_values": pixel_values})[0][0]
                vec = np.asarray(feats, dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                if norm > 0:
                    vec = vec / norm
                return [float(x) for x in vec]
            except Exception as e:
                logger.warning("champion ONNX vision failed: %s", e)

        # 1. Champion PyTorch model (dev / self-hosted with torch)
        champ_model, champ_proc = get_champion_clip()
        if champ_model is not None and champ_proc is not None:
            import torch
            device = next(champ_model.parameters()).device
            inputs = champ_proc(images=[img], return_tensors="pt").to(device)
            with torch.no_grad():
                out = champ_model.get_image_features(**inputs)
                feats = getattr(out, "pooler_output", out)
                norm_feats = feats / feats.norm(dim=-1, keepdim=True)
                return [float(x) for x in norm_feats[0].cpu().numpy()]
    except Exception as e:
        logger.warning("Champion image embedding failed: %s", e)

    # No zero-shot fallback: a different vector space must never be mixed with the
    # fine-tuned champion embeddings stored in the database. Fail instead.
    return None


_ONNX_TEXT_SESSION = None
_ONNX_TOKENIZER = None
_ONNX_TEXT_FAILED = False
_ONNX_LOCK = threading.Lock()

_TEXT_ONNX_NAMES = ["champion_text_encoder_fp16.onnx", "champion_text_encoder.onnx"]


def get_onnx_champion_encoder():
    """Load the champion ONNX text encoder lazily (fp16 preferred), local or from HF Hub.

    fp16 is preferred: it is half the size (127 MB vs 254 MB) with identical embeddings
    (cosine 1.0), which matters on a 512 MB instance. Falls back to the fp32 model.
    """
    global _ONNX_TEXT_SESSION, _ONNX_TOKENIZER, _ONNX_TEXT_FAILED
    if _ONNX_TEXT_SESSION is not None:
        return _ONNX_TEXT_SESSION, _ONNX_TOKENIZER
    if _ONNX_TEXT_FAILED:
        return None, None
    with _ONNX_LOCK:
        if _ONNX_TEXT_SESSION is not None:
            return _ONNX_TEXT_SESSION, _ONNX_TOKENIZER
        if _ONNX_TEXT_FAILED:
            return None, None
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from transformers import CLIPTokenizer

            here = os.path.dirname(os.path.abspath(__file__))
            candidates = []
            if os.environ.get("CLIP_TEXT_ONNX_PATH"):
                candidates.append(os.environ["CLIP_TEXT_ONNX_PATH"])
            for name in _TEXT_ONNX_NAMES:
                candidates += [
                    os.path.join("/models", name),
                    os.path.join(here, "..", "models", name),
                    os.path.join(here, "models", name),
                    os.path.join(here, name),
                ]
            onnx_path = next((c for c in candidates if c and os.path.exists(c)), None)

            repo_id = os.environ.get("HF_MODEL_REPO", "Marcell-Kristianto/toko-marcell-clip")
            if not onnx_path:
                for name in _TEXT_ONNX_NAMES:
                    try:
                        logger.info("Downloading ONNX text encoder (%s) from %s...", name, repo_id)
                        onnx_path = hf_hub_download(repo_id=repo_id, filename=name)
                        break
                    except Exception as e:
                        logger.info("HF hub download note (%s): %s", name, e)

            if onnx_path and os.path.exists(onnx_path):
                sess_opts = ort.SessionOptions()
                sess_opts.intra_op_num_threads = 2
                sess_opts.inter_op_num_threads = 1
                sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                _ONNX_TEXT_SESSION = ort.InferenceSession(
                    onnx_path, sess_opts, providers=["CPUExecutionProvider"]
                )
                try:
                    _ONNX_TOKENIZER = CLIPTokenizer.from_pretrained(repo_id)
                except Exception:
                    _ONNX_TOKENIZER = CLIPTokenizer.from_pretrained("openai/clip-vit-base-patch32")
                logger.info("Loaded champion ONNX text encoder from %s", os.path.basename(onnx_path))
            else:
                _ONNX_TEXT_FAILED = True
        except Exception as e:
            logger.warning("Could not initialize ONNX text encoder: %s", e)
            _ONNX_TEXT_FAILED = True
    return _ONNX_TEXT_SESSION, _ONNX_TOKENIZER


_CLIP_TEXT_MODEL = None
_CLIP_TEXT_LOCK = threading.Lock()


def get_clip_text_model():
    """Load the fastembed CLIP text model lazily (Qdrant/clip-ViT-B-32-text, 512-dim)."""
    global _CLIP_TEXT_MODEL
    if _CLIP_TEXT_MODEL is None:
        with _CLIP_TEXT_LOCK:
            if _CLIP_TEXT_MODEL is None:
                from fastembed import TextEmbedding
                cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", "/tmp/fastembed_cache")
                _CLIP_TEXT_MODEL = TextEmbedding(
                    model_name="Qdrant/clip-ViT-B-32-text",
                    cache_dir=cache_dir,
                )
    return _CLIP_TEXT_MODEL


def embed_query_clip_text(query: str) -> list[float] | None:
    """Generate 512-dim CLIP text embedding to query products.image_embedding cross-modally."""
    # 1. Try Champion ONNX model (<150MB RAM, ultra fast)
    try:
        session, tokenizer = get_onnx_champion_encoder()
        if session is not None and tokenizer is not None:
            tokens = tokenizer(query, padding="max_length", max_length=77, truncation=True, return_tensors="np")
            import numpy as np
            outputs = session.run(None, {
                "input_ids": tokens["input_ids"].astype(np.int64),
                "attention_mask": tokens["attention_mask"].astype(np.int64),
            })
            return [float(x) for x in outputs[0][0]]
    except Exception as e:
        logger.info("Champion ONNX embed_query_clip_text note: %s", e)

    # 2. Try Champion PyTorch model (if PyTorch environment available)
    try:
        champ_model, champ_proc = get_champion_clip()
        if champ_model is not None and champ_proc is not None:
            import torch
            device = next(champ_model.parameters()).device
            inputs = champ_proc(text=[query], return_tensors="pt", truncation=True, max_length=64).to(device)
            with torch.no_grad():
                out = champ_model.get_text_features(**inputs)
                feats = getattr(out, "pooler_output", out)
                norm_feats = feats / feats.norm(dim=-1, keepdim=True)
                return [float(x) for x in norm_feats[0].cpu().numpy()]
    except Exception:
        pass

    # 3. Fallback to fastembed zero-shot CLIP
    try:
        model = get_clip_text_model()
        vectors = list(model.embed([query]))
        return [float(x) for x in vectors[0]]
    except Exception as e:
        logger.warning("embed_query_clip_text failed: %s", e)
        return None




