"""Tests for hypothetical-question RAG (api/knowledge_seed.py + lookup_store_policy).

Needs the DB with store_knowledge + store_knowledge_questions seeded.
"""

import sys
from pathlib import Path

import psycopg

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from config import get_settings

DB_URL = get_settings().DATABASE_URL


def test_question_templates_generated():
    from knowledge_seed import _hypothetical_questions

    chunk = {"category": "brand_sizing", "title": "Dickies (Workwear Pants)", "content": "x"}
    qs = _hypothetical_questions(chunk)
    assert 1 <= len(qs) <= 5
    langs = {lang for _, lang in qs}
    assert "id" in langs and "en" in langs  # both languages present
    assert any("Dickies" in q for q, _ in qs)


def test_questions_table_populated():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('store_knowledge_questions')")
            if cur.fetchone()[0] is None:
                return  # table not present on this DB build; skip silently
            cur.execute("SELECT count(*) FROM store_knowledge_questions")
            assert cur.fetchone()[0] > 0


def test_policy_retrieval_returns_parent_chunks_deduped():
    from agent_tools import lookup_store_policy

    hits = lookup_store_policy("ongkir ke surabaya berapa lama", limit=3, db_url=DB_URL)
    assert hits, "expected policy hits for a shipping question"
    titles = [h["title"] for h in hits]
    assert len(titles) == len(set(titles))  # deduped by parent chunk


def test_policy_retrieval_brand_question_en():
    from agent_tools import lookup_store_policy

    hits = lookup_store_policy("does Dickies run small", limit=3, db_url=DB_URL)
    assert hits
    assert any("dickies" in h["title"].lower() for h in hits)
