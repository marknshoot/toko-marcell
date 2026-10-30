import sys
from pathlib import Path

import pytest

# Add manual/api to path so search can be imported directly
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from search import BM25Index, rewrite_id_to_en, stem, tokenize

# ── Tokenizer & Stemmer Edge Cases ──────────────────────────────────────────

def test_stem_basic_plurals():
    assert stem("sneakers") == "sneaker"
    assert stem("jeans") == "jean"
    assert stem("shoes") == "shoe"
    assert stem("boots") == "boot"
    assert stem("shirts") == "shirt"


def test_stem_sibilant_es():
    # Sibilant endings followed by -es should drop -es
    assert stem("watches") == "watch"
    assert stem("dresses") == "dress"
    assert stem("boxes") == "box"
    assert stem("brushes") == "brush"


def test_stem_ies():
    assert stem("batteries") == "battery"
    assert stem("accessories") == "accessory"


def test_stem_short_and_preserved_words():
    # Numbers must be preserved intact (e.g. Levi's 501)
    assert stem("501") == "501"
    assert stem("100") == "100"
    
    # Short words ending in s should not fold to empty/invalid
    assert stem("is") == "is"
    assert stem("as") == "as"
    assert stem("us") == "us"
    
    # Words ending in ss should not drop s
    assert stem("dress") == "dress"
    assert stem("boss") == "boss"
    assert stem("glass") == "glass"


def test_tokenize_empty_and_whitespace():
    assert tokenize("") == []
    assert tokenize("   ") == []
    assert tokenize(None) == []


def test_tokenize_punctuation_and_accents():
    # Normalized accents
    assert "cafe" in tokenize("café")
    # Punctuation stripped, numbers kept
    tokens = tokenize("Levi's 501 (Original-Fit): Men's Jeans!")
    assert "levi" in tokens
    assert "501" in tokens
    assert "original" in tokens
    assert "fit" in tokens
    assert "men" in tokens
    assert "jean" in tokens


def test_tokenize_stopwords_and_single_chars():
    # Single character tokens dropped (len > 1)
    assert tokenize("a b c") == []
    # Stopwords dropped
    assert tokenize("the and with for from") == []
    # Mixed sentence
    tokens = tokenize("A warm coat for the winter")
    assert tokens == ["warm", "coat", "winter"]


# ── Indonesian → English query rewriter ─────────────────────────────────────

def test_rewrite_single_words():
    assert rewrite_id_to_en("sepatu") == "shoes"
    assert rewrite_id_to_en("sandal") == "sandals"
    assert rewrite_id_to_en("sendal") == "sandals"
    assert rewrite_id_to_en("baju") == "t-shirt"
    assert rewrite_id_to_en("kaos") == "t-shirt"
    assert rewrite_id_to_en("kaus") == "t-shirt"
    assert rewrite_id_to_en("kemeja") == "shirt"
    assert rewrite_id_to_en("celana") == "pants"
    assert rewrite_id_to_en("jas") == "blazer"
    assert rewrite_id_to_en("gaun") == "dress"
    assert rewrite_id_to_en("rok") == "skirt"
    assert rewrite_id_to_en("topi") == "hat"
    assert rewrite_id_to_en("kacamata") == "sunglasses"
    assert rewrite_id_to_en("dompet") == "wallet"
    assert rewrite_id_to_en("tas") == "bag"
    assert rewrite_id_to_en("sabuk") == "belt"
    assert rewrite_id_to_en("hitam") == "black"
    assert rewrite_id_to_en("wanita") == "women"


def test_rewrite_multi_word_phrases_win_over_single_words():
    assert rewrite_id_to_en("celana jeans") == "jeans"
    assert rewrite_id_to_en("celana pendek") == "shorts"
    assert rewrite_id_to_en("celana panjang") == "trousers"
    assert rewrite_id_to_en("kaos kaki") == "socks"
    assert rewrite_id_to_en("ikat pinggang") == "belt"
    assert rewrite_id_to_en("pakaian dalam") == "underwear"
    assert rewrite_id_to_en("sepatu lari") == "running shoes"
    assert rewrite_id_to_en("jaket kulit") == "leather jacket"
    assert rewrite_id_to_en("merah muda") == "pink"
    assert rewrite_id_to_en("abu-abu") == "gray"
    assert rewrite_id_to_en("tahan air") == "waterproof"


def test_rewrite_mixed_terms_preserves_unknown_words_and_order():
    assert rewrite_id_to_en("jaket kulit hitam pria") == "leather jacket black men"
    assert rewrite_id_to_en("sepatu lari wanita") == "running shoes women"
    assert rewrite_id_to_en("kaos katun anak") == "t-shirt cotton kids"


def test_rewrite_is_case_insensitive():
    assert rewrite_id_to_en("SEPATU LARI PRIA") == "running shoes men"
    assert rewrite_id_to_en("Baju Hitam") == "t-shirt black"


def test_rewrite_returns_none_for_english_or_unchanged_queries():
    assert rewrite_id_to_en("running shoes") is None
    assert rewrite_id_to_en("black leather jacket") is None
    assert rewrite_id_to_en("denim") is None
    assert rewrite_id_to_en("") is None
    assert rewrite_id_to_en("   ") is None


# ── BM25Index Unit Tests ─────────────────────────────────────────────────────

def test_bm25_empty_index():
    index = BM25Index()
    index.build()
    assert index.size == 0
    assert index.score("shoes") == {}
    assert index.rank("shoes") == []


def test_bm25_exact_match_and_ranking():
    index = BM25Index()
    # Doc 0: title has running shoe
    index.add(101, {"title": "Nike Air Zoom Running Shoe", "description": "Good shoe"})
    # Doc 1: description has running shoe, title is unrelated
    index.add(102, {"title": "Casual Leather Loafer", "description": "Can be used as a running shoe"})
    # Doc 2: completely unrelated
    index.add(103, {"title": "Silk Necktie", "description": "Formal business wear"})
    index.build()

    ranked = index.rank("running shoe")
    assert len(ranked) == 2
    # Title match has higher field weight (3.0 vs 0.5) so Doc 101 should score higher
    doc_ids = [doc_id for doc_id, _ in ranked]
    assert doc_ids[0] == 101
    assert doc_ids[1] == 102
    assert ranked[0][1] > ranked[1][1]


def test_bm25_deduplication():
    index = BM25Index()
    # Two variants sharing the exact same title dedupe key
    index.add(201, {"title": "Levi's Men's 501 Original Jean"}, dedupe_key="levi's men's 501 original jean")
    index.add(202, {"title": "Levi's Men's 501 Original Jean"}, dedupe_key="levi's men's 501 original jean")
    index.add(203, {"title": "Levi's Men's 505 Regular Jean"}, dedupe_key="levi's men's 505 regular jean")
    index.build()

    # Without dedupe: all 3 match 'jean' (201 and 202 also match '501')
    ranked_no_dedupe = index.rank("501 jean", dedupe=False)
    assert len(ranked_no_dedupe) == 3

    # With dedupe: only 2 unique titles survive (one 501 variant and 505)
    ranked_dedupe = index.rank("501 jean", dedupe=True)
    assert len(ranked_dedupe) == 2
    # The top result must be one of the 501 variants
    assert ranked_dedupe[0][0] in (201, 202)
    assert ranked_dedupe[1][0] == 203


def test_bm25_allowed_filter():
    index = BM25Index()
    index.add(301, {"title": "Black Running Shoes"})
    index.add(302, {"title": "White Running Shoes"})
    index.build()

    # Restrict allowed to only 302
    ranked = index.rank("running shoes", allowed={302})
    assert len(ranked) == 1
    assert ranked[0][0] == 302

    # Restrict to empty set
    assert index.rank("running shoes", allowed=set()) == []


# ── Copilot search_catalog retry wiring ─────────────────────────────────────

def test_search_catalog_retries_once_with_rewritten_query(monkeypatch):
    import agent_tools

    calls = []

    def fake_once(query, **kwargs):
        calls.append(query)
        if len(calls) == 1:
            return []
        return [{"asin": "B000TEST"}]

    monkeypatch.setattr(agent_tools, "_search_catalog_once", fake_once)
    monkeypatch.setattr(agent_tools, "rewrite_id_to_en", lambda query: "running shoes")
    assert agent_tools.search_catalog("sepatu lari", limit=3) == [{"asin": "B000TEST"}]
    assert calls == ["sepatu lari", "running shoes"]


def test_search_catalog_does_not_retry_when_first_query_has_results(monkeypatch):
    import agent_tools

    calls = []

    def fake_once(query, **kwargs):
        calls.append(query)
        return [{"asin": "B000TEST"}]

    monkeypatch.setattr(agent_tools, "_search_catalog_once", fake_once)
    monkeypatch.setattr(agent_tools, "rewrite_id_to_en", lambda query: "running shoes")
    assert agent_tools.search_catalog("sepatu", limit=3) == [{"asin": "B000TEST"}]
    assert calls == ["sepatu"]


# ── ENABLE_DENSE_SEARCH feature flag ────────────────────────────────────────

def _settings_with_dense(enabled: bool):
    import config

    return config.Settings(ENABLE_DENSE_SEARCH=enabled)


def test_embed_query_returns_none_without_loading_model_when_dense_disabled(monkeypatch):
    import config
    import search

    monkeypatch.setattr(config, "get_settings", lambda: _settings_with_dense(False))

    def _must_not_load():
        raise AssertionError("get_embed_model() must not run when ENABLE_DENSE_SEARCH=false")

    monkeypatch.setattr(search, "get_embed_model", _must_not_load)

    assert search.embed_query("sepatu lari") is None


def test_embed_query_uses_model_when_dense_enabled(monkeypatch):
    import config
    import search

    monkeypatch.setattr(config, "get_settings", lambda: _settings_with_dense(True))

    class FakeModel:
        def embed(self, texts):
            assert list(texts) == ["sepatu lari"]
            return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(search, "get_embed_model", lambda: FakeModel())

    assert search.embed_query("sepatu lari") == [0.1, 0.2, 0.3]


class _FakeCursor:
    def __init__(self):
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, *args, **kwargs):
        self._rows = [(1,), (2,)]

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def cursor(self):
        return _FakeCursor()


class _FakeIndex:
    def __init__(self):
        self.rank_calls = 0

    def rank(self, query, allowed=None, dedupe=False):
        self.rank_calls += 1
        return [(1, 1.5), (2, 1.0)]

    def dedupe(self, ranked):
        return ranked


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_search_endpoint_falls_back_to_bm25_when_dense_disabled(monkeypatch, mode):
    """With ENABLE_DENSE_SEARCH=false, /search still answers from the BM25 index."""
    import routers.search as search_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(search_router, "get_settings", lambda: _settings_with_dense(False))
    fake_index = _FakeIndex()
    monkeypatch.setattr(search_router, "get_index", lambda builder=None: fake_index)
    monkeypatch.setattr(search_router, "get_conn", lambda: _FakeConn())
    monkeypatch.setattr(search_router, "row_to_product", lambda row: {"id": row[0]})

    def _must_not_embed(*args, **kwargs):
        raise AssertionError("embed_query() must not run when ENABLE_DENSE_SEARCH=false")

    monkeypatch.setattr(search_router, "embed_query", _must_not_embed)

    app = FastAPI()
    app.include_router(search_router.router)
    response = TestClient(app).get("/search", params={"q": "jeans", "mode": mode})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == mode
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [1, 2]
    assert fake_index.rank_calls >= 1
