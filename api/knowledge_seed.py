"""
Knowledge ingestion for Toko Marcell:
Parses `api/knowledge/*.md` (size charts, brand deviations, store policies, FAQ)
and seeds the `store_knowledge` table in PostgreSQL with 384-dim fastembed vectors.
"""

import logging
import os
import re

import psycopg
from search import embed_query

logger = logging.getLogger(__name__)

KNOWLEDGE_SCHEMA = [
    ("id", "SERIAL PRIMARY KEY"),
    ("category", "TEXT NOT NULL"),
    ("title", "TEXT NOT NULL"),
    ("content", "TEXT NOT NULL"),
    ("embedding", "vector(384)"),
]

KNOWLEDGE_INDEXES = [
    ("store_knowledge_category_idx", "store_knowledge (category)"),
    ("store_knowledge_embedding_idx", "store_knowledge USING hnsw (embedding vector_cosine_ops)"),
]

# Hypothetical-questions table (design §6): each row is one ID/EN question whose
# embedding points at a parent store_knowledge chunk. Retrieval matches the
# question and returns the parent chunk (deduped).
KNOWLEDGE_QUESTIONS_SCHEMA = [
    ("id", "SERIAL PRIMARY KEY"),
    ("chunk_id", "INTEGER NOT NULL REFERENCES store_knowledge(id) ON DELETE CASCADE"),
    ("question", "TEXT NOT NULL"),
    ("lang", "TEXT NOT NULL DEFAULT 'id'"),
    ("embedding", "vector(384)"),
]

KNOWLEDGE_QUESTIONS_INDEXES = [
    ("store_knowledge_q_chunk_idx", "store_knowledge_questions (chunk_id)"),
    ("store_knowledge_q_embedding_idx", "store_knowledge_questions USING hnsw (embedding vector_cosine_ops)"),
]


def _hypothetical_questions(chunk: dict) -> list[tuple[str, str]]:
    """Generate 3–5 ID+EN hypothetical questions for a chunk (template-based, no LLM).

    Returns a list of (question, lang) tuples. Templates are keyed by category so
    the questions read like real shopper phrasings; the chunk title is woven in
    for topicality.
    """
    cat = chunk["category"]
    title = chunk["title"]
    qs: list[tuple[str, str]] = []

    if cat in ("sizing_baseline", "sizing"):
        qs += [
            ("Ukuran saya apa kalau tinggi badan 170 berat 65?", "id"),
            ("Panduan ukuran baju dan celana di toko gimana?", "id"),
            ("How do I pick the right size by height and weight?", "en"),
            ("Tabel ukuran untuk atasan dan bawahan ada nggak?", "id"),
        ]
    elif cat == "brand_sizing":
        brand = title.split("(")[0].strip()
        qs += [
            (f"Ukuran {brand} apakah pas atau perlu naik/turun size?", "id"),
            (f"Apakah {brand} runs small atau large?", "id"),
            (f"Does {brand} run true to size?", "en"),
            (f"Saran ukuran untuk {brand} gimana kak?", "id"),
        ]
    elif cat in ("payment_qris", "payment"):
        qs += [
            ("Cara bayar pakai QRIS gimana?", "id"),
            ("Apakah pembayaran QRIS ini sungguhan atau simulasi?", "id"),
            ("How does the QRIS payment work?", "en"),
            ("Metode pembayaran apa saja yang tersedia?", "id"),
        ]
    elif cat == "shipping":
        qs += [
            ("Ongkir ke kota saya berapa dan berapa lama?", "id"),
            ("Estimasi pengiriman berapa hari?", "id"),
            ("How long does shipping take and is it free?", "en"),
            ("Ada gratis ongkir nggak?", "id"),
        ]
    elif cat == "return_policy":
        qs += [
            ("Kalau ukuran nggak pas bisa tukar nggak?", "id"),
            ("Kebijakan retur dan tukar barang gimana?", "id"),
            ("What is the return and exchange policy?", "en"),
            ("Berapa lama batas waktu retur?", "id"),
        ]
    elif cat == "faq":
        qs += [
            (f"Pertanyaan soal {title} gimana jawabannya?", "id"),
            (f"Question about {title}?", "en"),
        ]
    else:
        qs += [
            (f"Info soal {title} dong kak", "id"),
            (f"Tell me about {title}", "en"),
        ]

    return qs[:5]


def _chunk_markdown(filepath: str, default_category: str) -> list[dict]:
    """Parse markdown file into coherent chunks by top-level section headers."""
    if not os.path.exists(filepath):
        return []

    with open(filepath, encoding="utf-8") as f:
        text = f.read()

    raw_sections = re.split(r"\n(?=#{2,3}\s+)", text)
    chunks = []

    for section in raw_sections:
        lines = section.strip().split("\n")
        if not lines or not lines[0].startswith("#"):
            continue

        header = lines[0].lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
        if not body or len(body) < 30:
            continue

        category = default_category
        header_lower = header.lower()
        if "size" in header_lower or "tops" in header_lower or "bottoms" in header_lower or "footwear" in header_lower:
            category = "sizing_baseline"
        elif any(brand in header_lower for brand in ["dickies", "levi", "carhartt", "birkenstock", "toms", "champion"]):
            category = "brand_sizing"
        elif "qris" in header_lower or "payment" in header_lower:
            category = "payment_qris"
        elif "shipping" in header_lower or "delivery" in header_lower:
            category = "shipping"
        elif "return" in header_lower or "guarantee" in header_lower:
            category = "return_policy"
        elif "faq" in header_lower or "q:" in header_lower:
            category = "faq"

        chunks.append({
            "category": category,
            "title": header,
            "content": f"{header}\n\n{body}",
        })

    return chunks


def seed_knowledge(database_url: str, force_reload: bool = False) -> int:
    """Ensure `store_knowledge` table exists, and seed chunks if empty or forced."""
    knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge")
    if not os.path.isdir(knowledge_dir):
        alt_dir = os.path.join(os.path.dirname(__file__), "..", "data", "knowledge")
        if os.path.isdir(alt_dir):
            knowledge_dir = alt_dir

    size_file = os.path.join(knowledge_dir, "size_charts.md")
    policy_file = os.path.join(knowledge_dir, "store_policies.md")

    chunks = []
    chunks.extend(_chunk_markdown(size_file, default_category="sizing"))
    chunks.extend(_chunk_markdown(policy_file, default_category="policy"))

    if not chunks:
        logger.info("No knowledge chunks found to seed.")
        return 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'store_knowledge'"
            )
            existing_cols = {row[0] for row in cur.fetchall()}
            if existing_cols and not force_reload:
                cur.execute("SELECT COUNT(*) FROM store_knowledge")
                count = cur.fetchone()[0]
                if count > 0:
                    return count

            if force_reload:
                cur.execute("TRUNCATE TABLE store_knowledge")

            # Ensure the questions table exists (idempotent).
            q_cols = ",\n                ".join(f"{n} {d}" for n, d in KNOWLEDGE_QUESTIONS_SCHEMA)
            cur.execute(f"CREATE TABLE IF NOT EXISTS store_knowledge_questions (\n                {q_cols}\n            )")
            for idx_name, idx_target in KNOWLEDGE_QUESTIONS_INDEXES:
                cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_target}")

            logger.info("Embedding and inserting %d knowledge chunks...", len(chunks))
            inserted = 0
            total_questions = 0
            for chunk in chunks:
                vec = embed_query(f"{chunk['title']}\n{chunk['content'][:500]}")
                vec_str = "[" + ",".join(f"{x:.6f}" for x in vec) + "]" if vec else None

                cur.execute(
                    """
                    INSERT INTO store_knowledge (category, title, content, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    RETURNING id
                    """,
                    (chunk["category"], chunk["title"], chunk["content"], vec_str),
                )
                chunk_id = cur.fetchone()[0]
                inserted += 1

                # Hypothetical questions (design §6): embed each, point at parent.
                for question, lang in _hypothetical_questions(chunk):
                    qvec = embed_query(question)
                    qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]" if qvec else None
                    cur.execute(
                        """
                        INSERT INTO store_knowledge_questions (chunk_id, question, lang, embedding)
                        VALUES (%s, %s, %s, %s::vector)
                        """,
                        (chunk_id, question, lang, qvec_str),
                    )
                    total_questions += 1

            conn.commit()
            logger.info(
                "Seeded %d knowledge chunks and %d hypothetical questions.",
                inserted, total_questions,
            )
            return inserted


if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
    seed_knowledge(db_url, force_reload=True)
