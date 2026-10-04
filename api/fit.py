"""Review-derived fit signal lookup for serving.

Reads the ``product_fit`` and ``brand_fit`` tables built offline by
``pipelines/build_fit_signals.py`` and shapes them into the ``fit`` object the
API contract puts on product cards:

    {
      "label": "runs_small" | "true_to_size" | "runs_large",
      "source": "product" | "brand",
      "nMentions": int,
      "shareSmall": float, "shareTts": float, "shareLarge": float,
      "fitScore": float,
      "phrasing": str,            # short Indonesian shopper-facing sentence
      "disclaimer": str           # always present — never a certainty
    }

Fallback rule (design §3c): if a product has **fewer than 5** fit mentions, fall
back to the brand aggregate and mark ``source = "brand"``. If neither has usable
data, return ``None`` so the card simply omits the badge.
"""

import logging

logger = logging.getLogger(__name__)

# Below this many product-level mentions we fall back to the brand aggregate.
MIN_PRODUCT_MENTIONS = 5
# Below this many mentions (product or brand) we don't show a signal at all.
MIN_USABLE_MENTIONS = 3

_DISCLAIMER = "Estimasi dari ulasan pelanggan, bukan jaminan — cek juga tabel ukuran produk ya kak."

_LABEL_PHRASING = {
    "runs_small": "Cenderung kekecilan — banyak pembeli menyarankan naik 1 ukuran.",
    "runs_large": "Cenderung kebesaran — sebagian pembeli menyarankan turun 1 ukuran.",
    "true_to_size": "Sesuai ukuran standar (true to size) menurut mayoritas ulasan.",
}


def _pct(x: float) -> int:
    return round(float(x) * 100)


def _shape(row: dict, source: str) -> dict:
    """Turn a product_fit / brand_fit row dict into the serving ``fit`` object."""
    label = row["label"]
    n = row["n_mentions"]
    share_small = float(row["share_small"])
    share_tts = float(row["share_tts"])
    share_large = float(row["share_large"])

    # Build an evidence-grounded phrasing: "Dari N ulasan: X% kekecilan, Y% pas, Z% kebesaran."
    evidence = (
        f"Dari {n} ulasan: {_pct(share_small)}% bilang kekecilan, "
        f"{_pct(share_tts)}% pas, {_pct(share_large)}% kebesaran."
    )
    headline = _LABEL_PHRASING.get(label, "Data ukuran dari ulasan tersedia.")

    return {
        "label": label,
        "source": source,
        "nMentions": n,
        "shareSmall": round(share_small, 4),
        "shareTts": round(share_tts, 4),
        "shareLarge": round(share_large, 4),
        "fitScore": round(float(row["fit_score"]), 4),
        "phrasing": f"{headline} {evidence}" if source == "product" else f"{headline} (berdasarkan brand secara umum)",
        "disclaimer": _DISCLAIMER,
    }


def _fetch_product_fit(cur, asin: str) -> dict | None:
    cur.execute(
        "SELECT asin, n_mentions, share_small, share_tts, share_large, fit_score, label "
        "FROM product_fit WHERE asin = %s",
        (asin,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "asin": row[0], "n_mentions": row[1], "share_small": row[2],
        "share_tts": row[3], "share_large": row[4], "fit_score": row[5], "label": row[6],
    }


def _fetch_brand_fit(cur, brand: str) -> dict | None:
    cur.execute(
        "SELECT brand, n_mentions, share_small, share_tts, share_large, fit_score, label "
        "FROM brand_fit WHERE brand = %s",
        (brand,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "brand": row[0], "n_mentions": row[1], "share_small": row[2],
        "share_tts": row[3], "share_large": row[4], "fit_score": row[5], "label": row[6],
    }


def get_product_fit(cur, asin: str, brand: str | None = None) -> dict | None:
    """Return the serving ``fit`` object for an ASIN, or ``None`` if no usable data.

    Layering (design §3):
      1. Product-level signal when it has >= MIN_PRODUCT_MENTIONS mentions.
      2. Otherwise the brand aggregate (source="brand"), if the brand has
         >= MIN_USABLE_MENTIONS mentions.
      3. Otherwise None.

    ``cur`` is an open DB cursor (caller owns the connection). Any DB error is
    swallowed and returns None so a missing fit table never breaks a product
    response (the tables may not exist on an un-migrated DB).
    """
    try:
        prod = _fetch_product_fit(cur, asin)
        if prod and prod["n_mentions"] >= MIN_PRODUCT_MENTIONS and prod["label"] != "insufficient_data":
            return _shape(prod, source="product")

        if brand:
            brand_row = _fetch_brand_fit(cur, brand)
            if (
                brand_row
                and brand_row["n_mentions"] >= MIN_USABLE_MENTIONS
                and brand_row["label"] != "insufficient_data"
            ):
                return _shape(brand_row, source="brand")

        # Last resort: a product with 3-4 mentions is still better than nothing.
        if prod and prod["n_mentions"] >= MIN_USABLE_MENTIONS and prod["label"] != "insufficient_data":
            return _shape(prod, source="product")

        return None
    except Exception as e:
        logger.info("get_product_fit(%s) note: %s", asin, e)
        return None
