"""
Search primitives: BM25, multilingual embeddings, RRF fusion, and query rewriting.

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

# The pruned repository reuses the architecture, tokenizer format and pooling
# of this upstream model; fastembed only needs it to resolve the model config.
_BASE_MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_DEFAULT_EMBED_MODEL = "Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx"

# Publishers whose repos are part of fastembed's built-in catalog (pinned
# fastembed==0.8.1). Everything else with an ``org/model`` id is treated as a
# custom Hugging Face snapshot and loaded with ``PooledEmbedding``.
_STANDARD_EMBED_PREFIXES = (
    "sentence-transformers/",
    "BAAI/",
    "Qdrant/",
    "Qwen/",
    "google/",
    "intfloat/",
    "jinaai/",
    "minishlab/",
    "mixedbread-ai/",
    "nomic-ai/",
    "snowflake/",
    "thenlper/",
)


def _is_custom_hf_repo(model_name: str) -> bool:
    """Whether *model_name* is a custom Hugging Face repo rather than a local path.

    fastembed ships metadata for a fixed catalog of upstream models. A repo id
    outside that catalog is downloaded as a full snapshot and loaded through
    ``PooledEmbedding`` with ``specific_model_path``; local directories are
    handled before this check.
    """
    if model_name.startswith("Marcell-Kristianto/"):
        return True
    if "/" not in model_name:
        return False
    return not model_name.startswith(_STANDARD_EMBED_PREFIXES)


def dense_search_enabled() -> bool:
    """Whether dense (embedding) retrieval is switched on via ``ENABLE_DENSE_SEARCH``.

    Read through :func:`config.get_settings` on every call so a cached settings
    object or a test monkeypatch is honoured; falls back to the raw env var when
    the settings module is unavailable (standalone pipeline use).
    """
    try:
        from config import get_settings
        return bool(get_settings().ENABLE_DENSE_SEARCH)
    except Exception:
        raw = os.environ.get("ENABLE_DENSE_SEARCH")
        if raw is None:
            return True
        return raw.strip().lower() in ("1", "true", "yes", "on")


def _load_embed_model():
    """Build the configured embedding model (caller holds ``_EMBED_LOCK``)."""
    try:
        from config import get_settings
        settings = get_settings()
        default_model = settings.TEXT_EMBED_MODEL
        default_cache = settings.FASTEMBED_CACHE_DIR
    except Exception:
        default_model = _DEFAULT_EMBED_MODEL
        default_cache = "/tmp/fastembed_cache"

    model_name = os.environ.get("TEXT_EMBED_MODEL", default_model)
    cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", default_cache)
    try:
        threads = int(os.environ.get("FASTEMBED_THREADS", "1"))
    except ValueError:
        threads = 1

    # Render's free tier caps the container at 512 MB; onnxruntime sizes its
    # intra-op pool from the core count unless we pin it down. The default
    # memory arena also reserves a large per-thread slab, which costs far more
    # than it saves for a handful of short query embeddings.
    session_kwargs = {"threads": threads, "enable_cpu_mem_arena": False}

    # 1. Local directory: useful for offline/CI runs and custom checkpoints.
    if os.path.isdir(model_name):
        from fastembed.text.pooled_embedding import PooledEmbedding
        logger.info("Loading local embedding model: %s (threads=%d)", model_name, threads)
        return PooledEmbedding(
            model_name=_BASE_MULTILINGUAL_MODEL,
            specific_model_path=model_name,
            **session_kwargs,
        )

    # 2. Custom Hugging Face repo: download the snapshot once, then point
    #    fastembed at it. This is how the pruned multilingual model (124 MB
    #    ONNX, ~380 MB RSS) loads without a fastembed catalog entry.
    if _is_custom_hf_repo(model_name):
        from fastembed.text.pooled_embedding import PooledEmbedding
        from huggingface_hub import snapshot_download
        model_dir = snapshot_download(repo_id=model_name, cache_dir=cache_dir)
        logger.info(
            "Loading Hugging Face embedding model: %s -> %s (threads=%d)",
            model_name,
            model_dir,
            threads,
        )
        return PooledEmbedding(
            model_name=_BASE_MULTILINGUAL_MODEL,
            specific_model_path=model_dir,
            **session_kwargs,
        )

    # 3. Standard fastembed catalog model (BAAI, sentence-transformers, ...).
    from fastembed import TextEmbedding
    logger.info("Loading FastEmbed catalog model: %s (threads=%d)", model_name, threads)
    return TextEmbedding(
        model_name=model_name,
        cache_dir=cache_dir,
        **session_kwargs,
    )


def get_embed_model():
    """Load and cache the configured text embedding model lazily.

    ``TEXT_EMBED_MODEL`` selects one of three paths:

    * a **local directory** — ``PooledEmbedding`` with ``specific_model_path``;
    * a **custom Hugging Face repo** — ``snapshot_download`` into
      ``FASTEMBED_CACHE_DIR`` followed by ``PooledEmbedding``;
    * a **standard fastembed catalog model** — ``TextEmbedding`` with its
      built-in downloader.

    The default is the vocabulary-pruned multilingual MiniLM, which is 100%
    numerically equivalent to the upstream model but uses 113,070 tokens
    instead of 250,002 (124 MB ONNX instead of 225 MB, ~380 MB RSS instead of
    ~630 MB). That keeps hybrid dense + lexical search inside Render's 512 MB
    free tier.

    The singleton is guarded by ``_EMBED_LOCK``; use :func:`reset_embed_model`
    to clear it in tests or after changing the model at runtime.
    """
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        with _EMBED_LOCK:
            if _EMBED_MODEL is None:
                _EMBED_MODEL = _load_embed_model()
    return _EMBED_MODEL


def reset_embed_model() -> None:
    """Clear the cached embedding model so the next call reloads it.

    Acquires ``_EMBED_LOCK`` so a reset cannot race an in-flight lazy load.
    Intended for tests and for reloads after changing ``TEXT_EMBED_MODEL``.
    """
    global _EMBED_MODEL
    with _EMBED_LOCK:
        _EMBED_MODEL = None


def embed_query(query: str) -> list[float] | None:
    """Generate 384-dim vector for search query. Returns None if embedding fails.

    Returns ``None`` immediately when ``ENABLE_DENSE_SEARCH`` is false, so a
    memory-constrained deployment never imports, downloads or loads fastembed.
    """
    if not dense_search_enabled():
        logger.debug("Dense search disabled (ENABLE_DENSE_SEARCH=false); skipping embed_query")
        return None
    try:
        model = get_embed_model()
        vectors = list(model.embed([query]))
        return [float(x) for x in vectors[0]]
    except Exception as e:
        logger.warning("embed_query failed: %s", e)
        return None
