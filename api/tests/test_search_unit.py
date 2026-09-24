import pytest
import sys
from pathlib import Path

# Add manual/api to path so search can be imported directly
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from search import tokenize, stem, BM25Index, STOPWORDS, FIELD_WEIGHTS


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
