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

import math
import os
import re
import threading
import unicodedata
from collections import Counter, defaultdict

TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "this to was were will with you your".split()
)

# Field -> weight. See the module docstring for why these are applied to term
# frequencies rather than as separate indexes.
FIELD_WEIGHTS = {
    "title": 3.0,
    "brand": 2.0,
    "category": 1.5,
    "department": 1.0,
    "features": 1.0,
    "description": 0.5,
}


def stem(token: str) -> str:
    """Fold the common English plurals. Not a real stemmer — see docstring.

    The subtle case is `-es`: English adds it only after a sibilant (watches,
    dresses, boxes), otherwise the plural is just `-s` (shoes, houses). Stripping
    `es` unconditionally turns "shoes" into "sho", which matches nothing, since
    the singular "shoe" stays "shoe".

    Genuinely ambiguous words are left alone: "potatoes" folds to "potatoe".
    A real stemmer or a lemmatiser is the fix, and it is not worth a dependency
    for a fashion catalog where the ambiguous cases barely occur.
    """
    if not token.isalpha():
        return token  # keep numbers intact: "501" is a real query (Levi's 501)
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"          # batteries -> battery
    if len(token) > 4 and token.endswith("es") and token[:-2].endswith(("ch", "sh", "ss", "x", "z")):
        return token[:-2]                  # watches -> watch, dresses -> dress
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]                  # shoes -> shoe, jeans -> jean
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
        self.doc_keys: list = []  # optional dedupe key per document (the title)
        self.id_to_key: dict = {}  # doc_id -> dedupe_key lookup
        self.doc_lengths: dict = {}
        self.postings: dict[str, dict] = defaultdict(dict)  # term -> {doc_index: weighted tf}
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
    print(f"[search] BM25 index built: {index.size} documents")
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
        print(f"[search] embed_query failed: {e}")
        return None


_IMAGE_EMBED_MODEL = None
_IMAGE_EMBED_LOCK = threading.Lock()


def get_image_embed_model():
    """Load the fastembed CLIP image model lazily."""
    global _IMAGE_EMBED_MODEL
    if _IMAGE_EMBED_MODEL is None:
        with _IMAGE_EMBED_LOCK:
            if _IMAGE_EMBED_MODEL is None:
                from fastembed import ImageEmbedding
                cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", "/tmp/fastembed_cache")
                specific_path = None
                for candidate in [
                    os.environ.get("CLIP_MODEL_PATH"),
                    "/tmp/fastembed_cache/models--Qdrant--clip-ViT-B-32-vision",
                    os.path.expanduser("~/.cache/fastembed/models--Qdrant--clip-ViT-B-32-vision"),
                ]:
                    if candidate and os.path.exists(candidate):
                        specific_path = candidate
                        break

                kwargs = {"model_name": "Qdrant/clip-ViT-B-32-vision", "cache_dir": cache_dir}
                if specific_path:
                    kwargs["specific_model_path"] = specific_path

                _IMAGE_EMBED_MODEL = ImageEmbedding(**kwargs)
    return _IMAGE_EMBED_MODEL


def embed_image_bytes(image_bytes: bytes) -> list[float] | None:
    """Generate 512-dim CLIP vision embedding for an uploaded image. Returns None if fails."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        model = get_image_embed_model()
        vectors = list(model.embed([img]))
        return [float(x) for x in vectors[0]]
    except Exception as e:
        print(f"[search] embed_image_bytes failed: {e}")
        return None

