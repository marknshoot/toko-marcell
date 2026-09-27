"""
Knowledge ingestion for Toko Marcell:
Parses `api/knowledge/*.md` (size charts, brand deviations, store policies, FAQ)
and seeds the `store_knowledge` table in PostgreSQL with 384-dim fastembed vectors.
"""

import os
import re
import psycopg
from search import embed_query

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


def _chunk_markdown(filepath: str, default_category: str) -> list[dict]:
    """Parse markdown file into coherent chunks by top-level section headers."""
    if not os.path.exists(filepath):
        return []

    with open(filepath, "r", encoding="utf-8") as f:
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
        print("[knowledge] No knowledge chunks found to seed.")
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

            print(f"[knowledge] Embedding and inserting {len(chunks)} knowledge chunks...")
            inserted = 0
            for chunk in chunks:
                vec = embed_query(f"{chunk['title']}\n{chunk['content'][:500]}")
                vec_str = "[" + ",".join(f"{x:.6f}" for x in vec) + "]" if vec else None

                cur.execute(
                    """
                    INSERT INTO store_knowledge (category, title, content, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (chunk["category"], chunk["title"], chunk["content"], vec_str),
                )
                inserted += 1

            conn.commit()
            print(f"[knowledge] Successfully seeded {inserted} knowledge chunks into PostgreSQL.")
            return inserted


if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
    seed_knowledge(db_url, force_reload=True)
