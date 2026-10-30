"""
Knowledge ingestion for Toko Marcell:
Parses `api/knowledge/*.md` (size charts, brand deviations, store policies, FAQ)
and seeds the `store_knowledge` table in PostgreSQL with 384-dim fastembed vectors.
"""

import json
import logging
import os
import re

import psycopg

logger = logging.getLogger(__name__)

# Table DDL (store_knowledge, store_knowledge_questions) lives in the Alembic
# migrations under api/migrations/versions/; this module only writes rows.


def _is_skip_seed() -> bool:
    """True when the operator asked to skip knowledge seeding entirely."""
    return os.environ.get("SKIP_KNOWLEDGE_SEED", "").lower() in ("1", "true", "yes")


def _vector_literal(vector) -> str | None:
    """Format a vector for pgvector's text input (``[0.1,0.2,...]``)."""
    if not vector:
        return None
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"


def _load_seed_cache(knowledge_dir: str) -> list[dict] | None:
    """Load precomputed chunk/question embeddings, or None when no usable cache exists.

    Deployments with a tight memory budget (Render free tier: 512 MB) cannot afford
    to load fastembed inside the web process just to embed 34 chunks at boot, so the
    vectors are precomputed into ``knowledge_seed_cache.json``. The file is looked up
    next to the markdown sources and in the shared ``data/knowledge`` directory.
    """
    candidates = [os.path.join(knowledge_dir, "knowledge_seed_cache.json")]
    data_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "knowledge"))
    candidate = os.path.join(data_dir, "knowledge_seed_cache.json")
    if os.path.normpath(candidate) not in {os.path.normpath(p) for p in candidates}:
        candidates.append(candidate)

    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read knowledge seed cache %s: %s", path, exc)
            continue

        if not isinstance(entries, list) or not entries:
            logger.warning("Knowledge seed cache %s is not a non-empty list; ignoring.", path)
            continue

        usable = [
            entry for entry in entries
            if isinstance(entry, dict)
            and entry.get("category")
            and entry.get("title")
            and entry.get("content")
            and entry.get("embedding")
        ]
        if not usable:
            logger.warning("Knowledge seed cache %s has no chunks with embeddings; ignoring.", path)
            continue

        logger.info("Loaded %d precomputed knowledge chunks from %s", len(usable), path)
        return usable

    return None


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
        elif any(
            re.search(rf"\b{re.escape(brand)}\b", header_lower)
            for brand in ["dickies", "levi", "carhartt", "birkenstock", "toms", "champion"]
        ):
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


def build_seed_cache(knowledge_dir: str | None = None) -> list[dict]:
    """Embed the markdown sources from scratch into cache-shaped chunk entries.

    Only this explicit rebuild path loads fastembed for seeding; the web process
    reads the resulting JSON instead (see ``_load_seed_cache``). Use it via
    ``python -m knowledge_seed --write-cache`` after editing ``knowledge/*.md``.
    """
    from search import embed_query

    knowledge_dir = knowledge_dir or os.path.join(os.path.dirname(__file__), "knowledge")
    chunks = _chunk_markdown(os.path.join(knowledge_dir, "size_charts.md"), default_category="sizing")
    chunks.extend(_chunk_markdown(os.path.join(knowledge_dir, "store_policies.md"), default_category="policy"))

    entries = []
    for chunk in chunks:
        entries.append({
            "category": chunk["category"],
            "title": chunk["title"],
            "content": chunk["content"],
            "embedding": embed_query(f"{chunk['title']}\n{chunk['content'][:500]}"),
            "questions": [
                {"question": question, "lang": lang, "embedding": embed_query(question)}
                for question, lang in _hypothetical_questions(chunk)
            ],
        })
    return entries


def write_seed_cache(knowledge_dir: str | None = None) -> list[str]:
    """Rebuild the cache and write it next to the markdown sources and in ``data/knowledge``."""
    entries = build_seed_cache(knowledge_dir)
    if not entries:
        logger.warning("No knowledge chunks to cache; leaving any existing cache untouched.")
        return []
    missing = sum(1 for entry in entries if not entry["embedding"])
    missing += sum(1 for entry in entries for q in entry["questions"] if not q["embedding"])
    if missing:
        logger.warning("Embedding model returned %d empty vector(s); not writing a partial cache.", missing)
        return []

    base_dir = os.path.dirname(__file__)
    if knowledge_dir:
        targets = [os.path.join(knowledge_dir, "knowledge_seed_cache.json")]
    else:
        targets = [
            os.path.join(base_dir, "knowledge", "knowledge_seed_cache.json"),
            os.path.normpath(os.path.join(base_dir, "..", "data", "knowledge", "knowledge_seed_cache.json")),
        ]
    written = []
    for path in dict.fromkeys(targets):
        if not os.path.isdir(os.path.dirname(path)) or path in written:
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        written.append(path)
        logger.info("Wrote %d cached knowledge chunks to %s", len(entries), path)
    return written


def seed_knowledge(database_url: str, force_reload: bool = False) -> int:
    """Seed `store_knowledge` chunks if empty or forced (schema comes from Alembic)."""
    if _is_skip_seed():
        logger.info("SKIP_KNOWLEDGE_SEED is set; skipping knowledge seeding.")
        return 0

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

    # Precomputed embeddings win: they avoid loading ONNX/fastembed at web startup.
    cached_chunks = _load_seed_cache(knowledge_dir)
    if cached_chunks is not None:
        if chunks and [c["title"] for c in chunks] != [c["title"] for c in cached_chunks]:
            logger.warning(
                "knowledge_seed_cache.json does not match the current markdown sources; "
                "regenerate it after editing knowledge/*.md."
            )
        chunks = cached_chunks
    elif not chunks:
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

            mode = "precomputed" if cached_chunks is not None else "on-the-fly"
            logger.info("Inserting %d knowledge chunks (%s embeddings)...", len(chunks), mode)
            inserted = 0
            total_questions = 0
            for chunk in chunks:
                if cached_chunks is not None:
                    vec_str = _vector_literal(chunk.get("embedding"))
                    cached_questions = [
                        (q.get("question"), q.get("lang") or "id", q.get("embedding"))
                        for q in chunk.get("questions") or []
                        if isinstance(q, dict) and q.get("question") and q.get("embedding")
                    ]
                    skipped = len(chunk.get("questions") or []) - len(cached_questions)
                    if skipped:
                        logger.warning(
                            "Skipping %d cached question(s) without embeddings for chunk %r.",
                            skipped, chunk["title"],
                        )
                    questions = cached_questions
                else:
                    # Imported lazily so the cached path never even loads `search`.
                    from search import embed_query

                    vec_str = _vector_literal(embed_query(f"{chunk['title']}\n{chunk['content'][:500]}"))
                    questions = [
                        (question, lang, embed_query(question))
                        for question, lang in _hypothetical_questions(chunk)
                    ]

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
                for question, lang, qvec in questions:
                    qvec_str = _vector_literal(qvec)
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

    # Cached policy chunks are now stale; drop them outside the DB transaction.
    try:
        from agent_tools import clear_policy_cache
        clear_policy_cache()
    except Exception as exc:
        logger.warning("Could not clear policy cache after seeding: %s", exc)
    return inserted


if __name__ == "__main__":
    import sys

    if "--write-cache" in sys.argv:
        write_seed_cache()

    db_url = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
    seed_knowledge(db_url, force_reload=True)
