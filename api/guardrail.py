"""Deterministic pre-agent guardrail (design §1).

Runs BEFORE any LLM call. Classifies the shopper's latest message into:

  - "greeting"  — hi / thanks / "who are you" / "what can you do"
  - "off_topic" — code, SQL, math, science, politics, medical/legal, general trivia
  - "injection" — prompt-injection / jailbreak / "ignore previous instructions"
  - None        — ambiguous or clearly on-topic → hand to the agent

For the three non-None classes we return a canned Admin Toko Marcell reply and
make **zero** LLM calls. Ambiguous input (anything not confidently matched) is
passed through to the agent, where the prompt-level guardrails still apply.

Pure logic, no I/O — fully unit-testable and CI-safe.
"""

import re
import unicodedata

GuardrailResult = tuple[str | None, str | None]  # (guardrail_label, canned_reply)


# ── Canned replies (Admin Toko Marcell voice) ────────────────────────────────

_GREETING_REPLY = (
    "Halo kak! 👋 Mimin Admin Toko Marcell, siap bantu kakak soal fashion. "
    "Mimin bisa carikan baju, celana, jaket, atau sepatu, kasih rekomendasi outfit "
    "sesuai budget, bantu panduan ukuran (tinggi/berat badan), sampai info pengiriman "
    "& pembayaran. Lagi cari apa nih kak?"
)

_OFF_TOPIC_REPLY = (
    "Halo kak! Maaf ya, mimin asisten belanja khusus Toko Marcell, jadi mimin cuma bisa "
    "bantu seputar koleksi fashion, rekomendasi outfit, panduan ukuran (TB/BB), dan pesanan "
    "di toko kami. Yuk tanyakan seputar baju, celana, atau sepatu impian kakak! 😊"
)

_INJECTION_REPLY = (
    "Halo kak! Mimin tetap jadi Admin Toko Marcell yang bantu seputar fashion dan toko ya 😊 "
    "Mimin nggak bisa mengubah peran atau keluar dari tugas itu. Tapi mimin senang banget "
    "bantu kakak cari outfit, ukuran, atau produk impian. Lagi cari apa kak?"
)


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


# ── Pattern sets ──────────────────────────────────────────────────────────────

# Greetings / thanks / identity — only when the message is SHORT (otherwise a
# real question that happens to start with "hi min, ada celana..." must pass).
_GREETING_RE = re.compile(
    r"^(?:"
    r"h[ae]i+|h[ae]llo+|halo+|hola|hi+\s*min|hai\s*min|"
    r"selamat\s+(?:pagi|siang|sore|malam)|"
    r"good\s+(?:morning|afternoon|evening)|"
    r"met\s+(?:pagi|siang|sore|malam)|"
    r"pagi|siang|sore|malam|"
    r"assalamu?'?alaikum|"
    r"terima\s*kasih|makasih|thanks?|thank\s+you|thx|tengkyu|"
    r"ok(?:e|ay)?|sip|mantap|keren|"
    r"test|tes|ping"
    r")"
    r"(?:\s+(?:kak|ya|yaa|min|mimin|gan|bang|bro|sis|kakak|dong|yah|ka))*"
    r"[\s!.?,~]*$",
    re.IGNORECASE,
)

_IDENTITY_RE = re.compile(
    r"\b(?:"
    r"(?:kamu|kmu|km|anda|lu|lo|mimin|admin|bot|kalian)\s+(?:siapa|itu\s+(?:apa|siapa)|apa)|"
    r"siapa\s+(?:kamu|kmu|anda|mimin|admin|kalian|ini)|"
    r"who\s+are\s+you|what\s+are\s+you|"
    r"bisa\s+(?:apa\s*(?:aja|saja)?|ngapain)|"
    r"(?:kamu|mimin|admin)\s+bisa\s+(?:apa|ngapain)|"
    r"what\s+can\s+you\s+do|"
    r"fungsi\s*(?:mu|kamu)?|gunanya?\s+apa"
    r")\b",
    re.IGNORECASE,
)

# Prompt-injection / jailbreak.
_INJECTION_RE = re.compile(
    r"(?:"
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instruction|prompt|message)|"
    r"abaikan\s+(?:semua\s+)?(?:instruksi|perintah|prompt)\s+(?:sebelum|di\s*atas)|"
    r"disregard\s+(?:the\s+)?(?:above|previous|system)|"
    r"forget\s+(?:everything|your\s+(?:instruction|prompt|rule))|"
    r"lupakan\s+(?:semua\s+)?(?:instruksi|aturan|perintah)|"
    r"you\s+are\s+now\s+(?:a|an|the)\b|"
    r"kamu\s+sekarang\s+(?:adalah\s+)?(?:seorang|sebuah)?|"
    r"act\s+as\s+(?:a|an|if)|pretend\s+(?:to\s+be|you)|"
    r"berperan\s+(?:sebagai|menjadi)|"
    r"system\s+prompt|prompt\s+(?:kamu|sistem|asli)|"
    r"reveal\s+(?:your\s+)?(?:prompt|instruction|system)|"
    r"tunjukkan\s+(?:prompt|instruksi|sistem)|"
    r"developer\s+mode|mode\s+pengembang|"
    r"jailbreak|dan?\s+mode|do\s+anything\s+now|"
    r"bypass\s+(?:your\s+)?(?:rule|restriction|guardrail)|"
    r"abaikan\s+(?:peran|aturan)"
    r")",
    re.IGNORECASE,
)

# Off-topic domains. Each entry is a compiled pattern; a match → off_topic.
_OFF_TOPIC_RES = [
    # code / software / SQL / databases
    re.compile(r"\b(?:write|debug|fix|explain|generate|refactor)\s+(?:me\s+)?(?:a\s+)?(?:code|program|script|function|query)\b", re.IGNORECASE),
    re.compile(r"\b(?:python|javascript|java|c\+\+|golang|rust|typescript|php|ruby|kotlin|swift)\b", re.IGNORECASE),
    re.compile(r"\b(?:sql|select\s+\*|from\s+\w+\s+where|join\s+|database\s+schema|postgres|mysql|mongodb)\b", re.IGNORECASE),
    re.compile(r"\b(?:buatkan|tuliskan|jelaskan|perbaiki)\s+(?:kode|program|script|fungsi|query)\b", re.IGNORECASE),
    re.compile(r"\b(?:api|regex|algorithm|algoritma|compile|runtime|stack\s*trace|git\s+)\b", re.IGNORECASE),
    # math / science / academic
    re.compile(r"\b(?:solve|calculate|integral|derivative|equation|persamaan|turunan|hitunglah)\b", re.IGNORECASE),
    re.compile(r"\b(?:calculus|algebra|physics|chemistry|biology|fisika|kimia|biologi|kalkulus|aljabar)\b", re.IGNORECASE),
    re.compile(r"\b(?:homework|essay|makalah|skripsi|tugas\s+(?:kuliah|sekolah)|pr\s+matematika)\b", re.IGNORECASE),
    re.compile(r"\d+\s*[\+\-\*/x×÷]\s*\d+\s*=?\s*\??$", re.IGNORECASE),
    # politics / news / trivia / people
    re.compile(r"\b(?:presiden|president|pemilu|election|politik|politics|partai\s+politik)\b", re.IGNORECASE),
    re.compile(r"\b(?:berita|news\s+(?:today|hari\s+ini)|ibu\s*kota|capital\s+of|siapa\s+penemu|who\s+invented)\b", re.IGNORECASE),
    # medical / legal / financial advice
    re.compile(r"\b(?:diagnosa|diagnosis|obat\s+untuk|resep\s+dokter|gejala\s+penyakit|symptom|prescribe)\b", re.IGNORECASE),
    re.compile(r"\b(?:hukum\s+pidana|pasal\s+\d|legal\s+advice|nasihat\s+hukum|gugatan|saham\s+mana|investasi\s+(?:saham|crypto)|beli\s+bitcoin)\b", re.IGNORECASE),
    # generic "write me a poem/story" creative misuse
    re.compile(r"\b(?:write|buatkan|tuliskan)\s+(?:me\s+)?(?:a\s+)?(?:poem|puisi|story|cerita|lagu|song|essay)\b", re.IGNORECASE),
]

# On-topic allow signals — if present, we DON'T off-topic-block even if an
# off-topic keyword happens to appear (e.g. "kemeja buat ke kantor hukum").
_ONTOPIC_RE = re.compile(
    r"\b(?:"
    r"baju|celana|kemeja|kaos|kaus|jaket|hoodie|sweater|sepatu|sandal|sendal|sneaker|"
    r"dress|rok|outfit|ukuran|size|jeans|denim|chino|polo|jacket|shirt|pants|shoes|"
    r"brand|merk|harga|price|diskon|promo|rekomendasi|recommend|koleksi|katalog|"
    r"tb\s*\d|bb\s*\d|tinggi\s+badan|berat\s+badan|kondangan|wisuda|formal|kasual|"
    r"ongkir|pengiriman|kirim|retur|return|qris|bayar|checkout|keranjang|cart"
    r")\b",
    re.IGNORECASE,
)

# Short-message threshold for greeting classification (word count).
_GREETING_MAX_WORDS = 6


def classify(message: str) -> GuardrailResult:
    """Classify a message. Returns (label, canned_reply) or (None, None).

    Order of checks matters:
      1. injection (most dangerous)
      2. greeting / identity (only when short and no on-topic signal)
      3. off_topic (only when NO on-topic signal present)
      4. otherwise pass to the agent
    """
    norm = _normalize(message)
    if not norm:
        return (None, None)

    # 1. Injection — always wins.
    if _INJECTION_RE.search(norm):
        return ("injection", _INJECTION_REPLY)

    has_ontopic = bool(_ONTOPIC_RE.search(norm))
    word_count = len(norm.split())

    # 2. Greeting / identity.
    if not has_ontopic:
        if word_count <= _GREETING_MAX_WORDS and _GREETING_RE.match(norm):
            return ("greeting", _GREETING_REPLY)
        if _IDENTITY_RE.search(norm):
            return ("greeting", _GREETING_REPLY)

    # 3. Off-topic — only when there is no on-topic signal.
    if not has_ontopic:
        for pat in _OFF_TOPIC_RES:
            if pat.search(norm):
                return ("off_topic", _OFF_TOPIC_REPLY)

    # 4. Ambiguous / on-topic → agent handles it.
    return (None, None)
