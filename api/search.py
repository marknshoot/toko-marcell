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

# Deterministic Indonesian → English shopping vocabulary. Multi-word phrases are
# checked before single tokens so "celana jeans" cannot become "pants jeans".
_ID_EN_MULTI = {
    ("celana", "jeans"): "jeans",
    ("celana", "pendek"): "shorts",
    ("celana", "panjang"): "trousers",
    ("kaos", "kaki"): "socks",
    ("sepatu", "lari"): "running shoes",
    ("jaket", "kulit"): "leather jacket",
    ("ikat", "pinggang"): "belt",
    ("pakaian", "dalam"): "underwear",
    ("merah", "muda"): "pink",
    ("abu", "abu"): "gray",
    ("tahan", "air"): "waterproof",
}

_ID_EN_SINGLE = {
    # Garments / categories
    "sepatu": "shoes",
    "sandal": "sandals",
    "sendal": "sandals",
    "baju": "t-shirt",
    "kaos": "t-shirt",
    "kaus": "t-shirt",
    "kemeja": "shirt",
    "celana": "pants",
    "jaket": "jacket",
    "jas": "blazer",
    "gaun": "dress",
    "rok": "skirt",
    "topi": "hat",
    "kacamata": "sunglasses",
    "dompet": "wallet",
    "tas": "bag",
    "sabuk": "belt",
    # Colours
    "hitam": "black",
    "putih": "white",
    "merah": "red",
    "biru": "blue",
    "hijau": "green",
    "kuning": "yellow",
    "abu": "gray",
    "coklat": "brown",
    # Attributes / modifiers
    "pria": "men",
    "cowok": "men",
    "wanita": "women",
    "cewek": "women",
    "anak": "kids",
    "lari": "running",
    "olahraga": "athletic",
    "kerja": "work",
    "kantor": "work",
    "santai": "casual",
    "kasual": "casual",
    "hangat": "warm",
    "katun": "cotton",
    "kulit": "leather",
    "denim": "denim",
}

_ID_EN_PHRASES = sorted(_ID_EN_MULTI.items(), key=lambda item: len(item[0]), reverse=True)

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


def rewrite_id_to_en(query: str) -> str | None:
    """Deterministically translate common Indonesian shopping terms to English.

    Multi-word phrases win over single tokens, and unknown words are preserved in
    order. Returns ``None`` when no Indonesian term was translated, or when the
    translated query is identical to the original input. This deliberately has no
    model dependency: it is the cheap second chance after a zero-result search.
    """
    if not query or not query.strip():
        return None

    normalized = unicodedata.normalize("NFKD", query.lower())
    normalized = normalized.replace("’", "'").replace("-", " ")
    words = TOKEN_RE.findall(normalized)
    if not words:
        return None

    out: list[str] = []
    translated = False
    index = 0
    while index < len(words):
        match = None
        for phrase, target in _ID_EN_PHRASES:
            size = len(phrase)
            if tuple(words[index:index + size]) == phrase:
                match = (size, target)
                break

        if match:
            size, target = match
            out.extend(target.split())
            translated = True
            index += size
            continue

        word = words[index]
        target = _ID_EN_SINGLE.get(word)
        if target is None:
            out.append(word)
        else:
            out.extend(target.split())
            if target != word:
                translated = True
        index += 1

    if not translated:
        return None
    rewritten = " ".join(out)
    return rewritten if rewritten != query.strip() else None


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
    import contextlib

    import psycopg

    def _conn_cm():
        if not db_url:
            try:
                from db import get_conn
                return get_conn()
            except Exception:
                pass
        url = db_url
        if not url:
            try:
                from config import get_settings
                url = get_settings().DATABASE_URL
            except Exception:
                url = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
        return contextlib.contextmanager(lambda: (yield psycopg.connect(url)))()

    with _conn_cm() as conn:
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
    """Load the fastembed sentence transformer lazily.

    The model is selected by the ``TEXT_EMBED_MODEL`` env var / config setting.
    Defaults to ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``.
    """
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        with _EMBED_LOCK:
            if _EMBED_MODEL is None:
                from fastembed import TextEmbedding
                try:
                    from config import get_settings
                    s = get_settings()
                    default_model = s.TEXT_EMBED_MODEL
                    default_cache = s.FASTEMBED_CACHE_DIR
                except Exception:
                    default_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                    default_cache = "/tmp/fastembed_cache"
                model_name = os.environ.get("TEXT_EMBED_MODEL", default_model)
                cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", default_cache)
                _EMBED_MODEL = TextEmbedding(
                    model_name=model_name,
                    cache_dir=cache_dir,
                )
                logger.info("Loaded text embedding model: %s", model_name)
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


_CLIP_VISION_MODEL = None
_CLIP_VISION_FAILED = False
_CLIP_VISION_LOCK = threading.Lock()


def _fastembed_cache_dir() -> str:
    try:
        from config import get_settings
        default_cache = get_settings().FASTEMBED_CACHE_DIR
    except Exception:
        default_cache = "/tmp/fastembed_cache"
    return os.environ.get("FASTEMBED_CACHE_DIR", default_cache)


def get_clip_vision_model():
    """Load the pre-trained fastembed CLIP vision model lazily (Qdrant/clip-ViT-B-32-vision, 512-dim)."""
    global _CLIP_VISION_MODEL, _CLIP_VISION_FAILED
    if _CLIP_VISION_MODEL is not None:
        return _CLIP_VISION_MODEL
    if _CLIP_VISION_FAILED:
        return None
    with _CLIP_VISION_LOCK:
        if _CLIP_VISION_MODEL is not None:
            return _CLIP_VISION_MODEL
        if _CLIP_VISION_FAILED:
            return None
        try:
            from fastembed import ImageEmbedding
            _CLIP_VISION_MODEL = ImageEmbedding(
                model_name="Qdrant/clip-ViT-B-32-vision",
                cache_dir=_fastembed_cache_dir(),
            )
            logger.info("Loaded CLIP vision model: Qdrant/clip-ViT-B-32-vision")
        except Exception as e:
            logger.warning("Could not initialize CLIP vision model: %s", e)
            _CLIP_VISION_FAILED = True
    return _CLIP_VISION_MODEL


def vision_encoder_ready() -> bool:
    """True if the pre-trained CLIP vision encoder is available."""
    return get_clip_vision_model() is not None


def embed_image_bytes(image_bytes: bytes) -> list[float] | None:
    """Generate a 512-dim CLIP vision embedding for an uploaded image using fastembed."""
    try:
        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        model = get_clip_vision_model()
        if model is None:
            return None
        vectors = list(model.embed([img]))
        if not vectors:
            return None
        vec = np.asarray(vectors[0], dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return [float(x) for x in vec]
    except Exception as e:
        logger.warning("embed_image_bytes failed: %s", e)
        return None


_CLIP_TEXT_MODEL = None
_CLIP_TEXT_LOCK = threading.Lock()


def get_clip_text_model():
    """Load the pre-trained fastembed CLIP text model lazily (Qdrant/clip-ViT-B-32-text, 512-dim)."""
    global _CLIP_TEXT_MODEL
    if _CLIP_TEXT_MODEL is None:
        with _CLIP_TEXT_LOCK:
            if _CLIP_TEXT_MODEL is None:
                from fastembed import TextEmbedding
                _CLIP_TEXT_MODEL = TextEmbedding(
                    model_name="Qdrant/clip-ViT-B-32-text",
                    cache_dir=_fastembed_cache_dir(),
                )
                logger.info("Loaded CLIP text model: Qdrant/clip-ViT-B-32-text")
    return _CLIP_TEXT_MODEL


def embed_query_clip_text(query: str) -> list[float] | None:
    """Generate 512-dim CLIP text embedding to query products.image_embedding cross-modally."""
    try:
        model = get_clip_text_model()
        vectors = list(model.embed([query]))
        return [float(x) for x in vectors[0]]
    except Exception as e:
        logger.warning("embed_query_clip_text failed: %s", e)
        return None




