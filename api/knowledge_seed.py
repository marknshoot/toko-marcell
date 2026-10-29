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

# Table DDL (store_knowledge, store_knowledge_questions) lives in the Alembic
# migrations under api/migrations/versions/; this module only writes rows.


def _hypothetical_questions(chunk: dict) -> list[tuple[str, str]]:
    """Generate 3–5 ID+EN hypothetical questions for a chunk (template-based, no LLM).

    Returns a list of (question, lang) tuples. Templates are keyed by category so
    the questions read like real shopper phrasings; the chunk title is woven in
    for topicality.
    """
    cat = chunk["category"]
    title = chunk["title"]
    title_lower = title.lower()
    qs: list[tuple[str, str]] = []

    if cat in ("sizing_baseline", "sizing"):
        if "bottom" in title_lower or "waist" in title_lower or "celana" in title_lower:
            qs += [
                ("Tabel ukuran celana berdasarkan pinggang dan berat badan?", "id"),
                ("Ukuran celana jeans atau chino kalau lingkar pinggang 80 cm?", "id"),
                ("What waist size in pants or bottoms should I get?", "en"),
                ("Konversi ukuran celana US ke cm pinggang?", "id"),
            ]
        elif "footwear" in title_lower or "sepatu" in title_lower:
            qs += [
                ("Tabel ukuran sepatu sandal konversi US EU panjang kaki cm?", "id"),
                ("Ukuran sepatu pria wanita kalau panjang kaki 26 cm atau EU 41?", "id"),
                ("Standard footwear shoe size conversion US to EU and foot length?", "en"),
                ("Panduan ukuran sepatu di toko gimana?", "id"),
            ]
        else:
            qs += [
                ("Ukuran saya apa kalau tinggi badan 170 berat 65?", "id"),
                ("Panduan ukuran baju atasan kaos jaket berdasarkan TB dan BB?", "id"),
                ("How do I pick the right top size by height and weight?", "en"),
                ("Tabel ukuran untuk atasan dan lingkar dada ada nggak?", "id"),
            ]
    elif cat == "brand_sizing":
        raw_brand = title.split("(")[0].strip()
        brand = re.sub(r"^\d+[a-z]?\.\s*", "", raw_brand).strip() or raw_brand
        qs += [
            (f"{brand} mending naik atau turun size? Ambil ukuran berapa?", "id"),
            (f"Ukuran {brand} apakah pas, kekecilan, atau kebesaran?", "id"),
            (f"Does {brand} run large, small, or true to size?", "en"),
            (f"Saran ukuran dan beda model {brand} gimana kak?", "id"),
            (f"How does {brand} sizing fit compared to standard size?", "en"),
        ]
    elif cat in ("payment_qris", "payment"):
        qs += [
            ("Cara bayarnya gimana ya pakai QRIS?", "id"),
            ("Apakah pembayaran QRIS ini cuma simulasi atau beneran?", "id"),
            ("How does the QRIS payment and checkout simulation work?", "en"),
            ("Metode pembayaran apa saja yang tersedia di toko?", "id"),
        ]
    elif cat == "shipping":
        qs += [
            ("Ongkir ke kota saya berapa dan berapa lama sampai?", "id"),
            ("Gratis ongkir mulai belanja berapa rupiah?", "id"),
            ("How long does shipping take and when is shipping free?", "en"),
            ("Estimasi pengiriman JNE SiCepat GoSend berapa hari?", "id"),
        ]
    elif cat == "return_policy":
        qs += [
            ("Kalau ukuran salah atau nggak pas bisa tukar?", "id"),
            ("Berapa lama batas waktu retur dan tukar barang?", "id"),
            ("What is the 7-day return and size exchange policy?", "en"),
            ("Syarat retur atau tukar ukuran gimana?", "id"),
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
        if "example" in header_lower:
            category = "sizing_example"
        elif any(brand in header_lower for brand in ["dickies", "levi", "carhartt", "birkenstock", "toms", "champion"]):
            category = "brand_sizing"
        elif "size" in header_lower or "tops" in header_lower or "bottoms" in header_lower or "footwear" in header_lower:
            category = "sizing_baseline"
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
    """Seed `store_knowledge` chunks if empty or forced (schema comes from Alembic)."""
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
                cur.execute(
                    "SELECT COUNT(*) FROM store_knowledge WHERE category = 'sizing_example'"
                )
                count = cur.fetchone()[0]
                if count > 0:
                    return count

            # Reaching here means either a forced reload, or the table holds only
            # legacy pre-Phase-7b rows (no 'sizing_example' category). Clear it so
            # the bilingual chunks don't duplicate stale content. CASCADE is required
            # because store_knowledge_questions has an FK onto store_knowledge.
            cur.execute("TRUNCATE TABLE store_knowledge CASCADE")

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
