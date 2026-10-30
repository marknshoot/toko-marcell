"""Offline unit tests for the precomputed knowledge seed (api/knowledge_seed.py).

The Render free tier has a 512 MB ceiling, so startup must seed from
``knowledge_seed_cache.json`` and never load fastembed/ONNX. These tests pin that
contract without a database or a model download.
"""

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import knowledge_seed

CACHE_NAME = "knowledge_seed_cache.json"
KNOWLEDGE_DIR = API_DIR / "knowledge"
DATA_KNOWLEDGE_DIR = API_DIR.parent / "data" / "knowledge"
EMBED_DIM = 384


def _read_cache(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class _FakeCursor:
    """Minimal psycopg cursor double that records SQL and fakes the seed queries."""

    def __init__(self, log: list):
        self.log = log
        self.rows: list = []

    def execute(self, sql: str, params=None):
        normalized = " ".join(sql.split())
        self.log.append((normalized, params))
        if "information_schema.columns" in normalized:
            self.rows = [("id",)]
        elif "COUNT(*) FROM store_knowledge" in normalized:
            self.rows = [(0,)]
        elif "RETURNING id" in normalized:
            self.rows = [(len(self.log),)]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.log: list = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.log)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_connect(monkeypatch) -> _FakeConn:
    conn = _FakeConn()
    monkeypatch.setattr(knowledge_seed.psycopg, "connect", lambda *a, **k: conn)
    return conn


def test_cache_shipped_in_both_knowledge_dirs():
    api_cache = KNOWLEDGE_DIR / CACHE_NAME
    data_cache = DATA_KNOWLEDGE_DIR / CACHE_NAME
    assert api_cache.is_file(), "api/knowledge cache is required for Render startup"
    assert data_cache.is_file(), "data/knowledge cache must stay in parity"
    assert api_cache.read_bytes() == data_cache.read_bytes()


def test_cache_shape_and_vectors():
    entries = _read_cache(KNOWLEDGE_DIR / CACHE_NAME)
    assert len(entries) == 34
    questions = [q for entry in entries for q in entry["questions"]]
    assert len(questions) == 120
    for entry in entries:
        assert entry["category"] and entry["title"] and entry["content"]
        assert len(entry["embedding"]) == EMBED_DIM
        for question in entry["questions"]:
            assert question["question"]
            assert question["lang"] in {"id", "en"}
            assert len(question["embedding"]) == EMBED_DIM


def test_cache_matches_markdown_sources():
    entries = _read_cache(KNOWLEDGE_DIR / CACHE_NAME)
    live = knowledge_seed._chunk_markdown(str(KNOWLEDGE_DIR / "size_charts.md"), "sizing")
    live += knowledge_seed._chunk_markdown(str(KNOWLEDGE_DIR / "store_policies.md"), "policy")
    assert [(e["category"], e["title"], e["content"]) for e in entries] == [
        (c["category"], c["title"], c["content"]) for c in live
    ]


def test_load_seed_cache_prefers_knowledge_dir():
    entries = knowledge_seed._load_seed_cache(str(KNOWLEDGE_DIR))
    assert entries is not None
    assert len(entries) == 34


def test_load_seed_cache_falls_back_to_data_dir(tmp_path):
    entries = knowledge_seed._load_seed_cache(str(tmp_path))
    assert entries is not None
    assert len(entries) == 34


def test_load_seed_cache_returns_none_without_cache(monkeypatch):
    monkeypatch.setattr(knowledge_seed.os.path, "exists", lambda *_: False)
    assert knowledge_seed._load_seed_cache("/nonexistent/knowledge") is None


def test_skip_knowledge_seed_short_circuits_before_db(monkeypatch):
    monkeypatch.setenv("SKIP_KNOWLEDGE_SEED", "yes")

    def _fail_connect(*args, **kwargs):
        raise AssertionError("SKIP_KNOWLEDGE_SEED must not connect to the database")

    monkeypatch.setattr(knowledge_seed.psycopg, "connect", _fail_connect)
    assert knowledge_seed.seed_knowledge("postgresql://unreachable/none") == 0


def test_cached_seed_never_calls_embed_query(monkeypatch):
    import search

    conn = _patch_connect(monkeypatch)

    def _boom(*args, **kwargs):
        raise AssertionError("embed_query must not run when the seed cache exists")

    monkeypatch.setattr(search, "embed_query", _boom)

    assert knowledge_seed.seed_knowledge("postgresql://x/y", force_reload=True) == 34
    assert conn.committed

    chunk_inserts = [params for sql, params in conn.log if "INSERT INTO store_knowledge (" in sql]
    question_inserts = [params for sql, params in conn.log if "INSERT INTO store_knowledge_questions" in sql]
    assert len(chunk_inserts) == 34
    assert len(question_inserts) == 120
    assert all(isinstance(params[3], str) and params[3].startswith("[") for params in chunk_inserts)
    assert all(isinstance(params[3], str) and params[3].startswith("[") for params in question_inserts)


def test_missing_cache_falls_back_to_embeddings(monkeypatch):
    import search

    conn = _patch_connect(monkeypatch)
    monkeypatch.setattr(knowledge_seed, "_load_seed_cache", lambda _dir: None)
    embedded: list[str] = []

    def _fake_embed(text: str):
        embedded.append(text)
        return [0.5] * EMBED_DIM

    monkeypatch.setattr(search, "embed_query", _fake_embed)

    assert knowledge_seed.seed_knowledge("postgresql://x/y", force_reload=True) == 34
    assert conn.committed
    assert len(embedded) == 34 + 120  # one per chunk + one per hypothetical question


def test_build_seed_cache_from_markdown(monkeypatch):
    import search

    embedded: list[str] = []

    def _fake_embed(text: str):
        embedded.append(text)
        return [0.1] * EMBED_DIM

    monkeypatch.setattr(search, "embed_query", _fake_embed)
    entries = knowledge_seed.build_seed_cache(str(KNOWLEDGE_DIR))

    assert len(entries) == 34
    assert sum(len(entry["questions"]) for entry in entries) == 120
    assert len(embedded) == 34 + 120
    assert all(len(entry["embedding"]) == EMBED_DIM for entry in entries)
    assert all(len(q["embedding"]) == EMBED_DIM for entry in entries for q in entry["questions"])


def test_write_seed_cache_to_explicit_dir(monkeypatch, tmp_path):
    import search

    monkeypatch.setattr(search, "embed_query", lambda text: [0.2] * EMBED_DIM)
    for name in ("size_charts.md", "store_policies.md"):
        (tmp_path / name).write_bytes((KNOWLEDGE_DIR / name).read_bytes())

    written = knowledge_seed.write_seed_cache(str(tmp_path))
    assert written == [str(tmp_path / CACHE_NAME)]
    entries = _read_cache(tmp_path / CACHE_NAME)
    assert len(entries) == 34
    assert sum(len(entry["questions"]) for entry in entries) == 120


def test_write_seed_cache_refuses_empty_embeddings(monkeypatch, tmp_path):
    import search

    monkeypatch.setattr(search, "embed_query", lambda text: None)
    for name in ("size_charts.md", "store_policies.md"):
        (tmp_path / name).write_bytes((KNOWLEDGE_DIR / name).read_bytes())

    assert knowledge_seed.write_seed_cache(str(tmp_path)) == []
    assert not (tmp_path / CACHE_NAME).exists()
