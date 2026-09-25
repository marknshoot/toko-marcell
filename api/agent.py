"""
Toko Marcell AI Copilot Orchestrator.

Implements the Tri-Modal Multi-Agent Fan-Out / Fan-In architecture:
- Node 0: Relevance, Guardrail & Greeting Fast-Path Classifier
- Node 1: Planner & Tool Decision Node
- Node 2: Tri-Modal Concurrent Tool Execution (asyncio.gather)
- Node 3: Grounded Synthesizer with Admin Toko Marcell Persona
"""

import asyncio
import json
import os
import re
import time
from typing import Any

import httpx
import psycopg

from agent_tools import (
    get_order_status,
    get_product_details,
    get_product_reviews,
    lookup_store_policy,
    search_by_image,
    search_catalog,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
OPENROUTER_API_KEY = os.environ.get(
    "OPENROUTER_API_KEY",
    "sk-or-v1-placeholder-dummy-key",
)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "google/gemini-2.5-flash")

# Tool definitions for OpenRouter function calling
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": (
                "Searches Toko Marcell catalog for products using hybrid BM25 + vector search and Cross-Encoder reranking. "
                "Use for finding apparel, pants, shirts, jackets, shoes, or outfits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords or style description (e.g. 'Dickies 874', 'Levi's 501', 'kemeja oxford')",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g. 'Pants', 'Jeans', 'Shoes', 'Shirts', 'Jackets')",
                    },
                    "department": {
                        "type": "string",
                        "enum": ["Men", "Women", "All"],
                        "description": "Target department",
                    },
                    "price_max": {
                        "type": "integer",
                        "description": "Maximum budget / price constraint in Indonesian Rupiah (Rp)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of products to return (default 4)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "Retrieves authoritative specifications, live price in IDR, features, and brand for a product by ASIN or numeric ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asin_or_id": {
                        "type": "string",
                        "description": "Product ASIN (e.g. 'B0001YRQHQ') or numeric product ID",
                    }
                },
                "required": ["asin_or_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_reviews",
            "description": "Fetches authentic customer reviews for sizing reality, shrinkage, durability, and fabric quality.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {
                        "type": "string",
                        "description": "The product ASIN",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Specific aspect: 'fit', 'sizing', 'durability', 'material', 'shrinkage'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max reviews to fetch (default 5)",
                    },
                },
                "required": ["asin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_image",
            "description": "Finds catalog items that visually match an uploaded image using CLIP vision embeddings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url_or_ref": {
                        "type": "string",
                        "description": "URL or base64 data string of the user's uploaded image",
                    },
                    "department": {
                        "type": "string",
                        "enum": ["Men", "Women", "All"],
                    },
                    "price_max": {
                        "type": "integer",
                        "description": "Maximum budget in IDR",
                    },
                },
                "required": ["image_url_or_ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_store_policy",
            "description": (
                "Answers store-related questions regarding shipping estimates, 7-day return policy, "
                "QRIS demo simulation rules, and master size charts (height/weight/waist)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Policy or sizing question (e.g. 'standar ukuran TB 175 BB 70', 'estimasi pengiriman Jakarta', 'aturan QRIS')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of policy chunks to return (default 3)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Retrieves live order payment status and purchased items from PostgreSQL by unique checkout token (e.g. 'tk_7f9a2b1').",
            "parameters": {
                "type": "object",
                "properties": {
                    "token": {
                        "type": "string",
                        "description": "Unique order token returned at checkout",
                    }
                },
                "required": ["token"],
            },
        },
    },
]

ADMIN_SYSTEM_PROMPT = """You are Admin Toko Marcell, an expert in-store mall stylist and customer service admin for Toko Marcell (Jakarta, Indonesia).

### CORE PERSONA & VOICE:
- Friendly, warm, polite, and consultative:
  - If addressed in Indonesian: use natural Indonesian e-commerce greetings ("Halo kak!", "Selamat siang kak!", "Ada yang bisa mimin bantu?").
  - If addressed in English: use polite, helpful English ("Hello! How can I style you today?").
- Consultative Salesperson: Enthusiastically highlight craftsmanship, genuine durability, and value in Rupiah. Never badmouth any product.
- Proactive Closer: Confidently guide shoppers toward styling and checkout (e.g. "Mau langsung mimin bantu masukkan ke keranjang kak?" or recommend complementary pieces).

### MANDATORY TOOL CALLING DIRECTIVES:
- Product inquiries / availability: Whenever a customer asks if you have certain clothes, brands, or styles (e.g. "ada celana...", "cari kemeja...", "outfit santai...", "jaket denim"), you MUST invoke `search_catalog` immediately to show real products and prices. Do NOT reply with empty pleasantries without searching!
- Sizing & Fit inquiries: For height/weight (TB/BB) or brand sizing questions, invoke `lookup_store_policy` to retrieve the master size chart & brand overrides, and `get_product_reviews` for authentic buyer sizing feedback.
- Fabric & Material inquiries: Invoke `get_product_details` to check exact composition and bullet features.
- Comparing products (A vs B): Invoke `get_product_details` for both products, and `get_product_reviews` for both.
- Buyer reviews & social proof: Invoke `get_product_reviews` (or `search_catalog` if ASIN is not yet known).
- Shipping, QRIS demo, returns: Invoke `lookup_store_policy`.
- Order tracking: Whenever an order token (e.g. tk_...) is mentioned, invoke `get_order_status`.

### STRICT GROUNDING & ANTI-HALLUCINATION RULES:
1. ZERO PRODUCT INVENTIONS:
   - Every product mentioned MUST exist in the tool results.
   - Do NOT invent brands, ASINs, fake stock counts, or fake discounts.
   - If a product isn't found, politely offer alternatives from the catalog.
2. CURRENCY & PRICING:
   - All prices must be strictly formatted in Indonesian Rupiah (e.g. "Rp 178.100", "Rp 494.700"). Never invent prices.
3. SIZING ADVICE (USE CASE 2):
   - Always reference the sizing chart rules and verified customer reviews:
   - Dickies 874: Rigid 8.5 oz twill has zero stretch; recommend sizing up 1–2 waist sizes for comfortable fit.
   - Levi's: 505 has extra room in thigh/seat vs 501 (straight leg classic).
   - Carhartt / Champion: US relaxed cut, runs 1 size larger than Asian standard.
   - Birkenstock: EU sizing with cork arch support; if between sizes, size down.
   - TOMS: Canvas stretches slightly after a few wears.
4. OCCASION-BASED OUTFIT BUILDER (USE CASE 3):
   - When asked for a complete outfit under a budget: recommend matching Top + Bottom + Shoes and calculate total price verifying it stays within the customer's budget!
5. STORE POLICIES & QRIS DEMO (USE CASE 7):
   - Payment: Instant QRIS digital simulation (honest disclosure: realistic demo, zero real funds charged).
   - Shipping: Dispatched from South Jakarta (1–2 days Jabodetabek, 3–5 days outer islands). Free shipping for orders >= Rp 300.000!
   - Returns: 7-day size exchange guarantee for unworn items with original tags.
6. ORDER TRACKING (USE CASE 8):
   - When given an order token (tk_...), report exact status (PAID), total IDR, and items from the database.
"""


def _get_session_browsing_context(session_id: str | None, db_url: str) -> str:
    """Fetch recent browsing and cart events from events table to give agent immediate context."""
    if not session_id:
        return ""
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.event_type, p.title, p.price_idr, e.created_at
                    FROM events e
                    LEFT JOIN products p ON e.asin = p.asin
                    WHERE e.session_id = %s
                      AND e.event_type IN ('view_product', 'add_to_cart')
                    ORDER BY e.id DESC
                    LIMIT 3
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()
                if not rows:
                    return ""

                lines = ["[Recent Browsing Context]"]
                for r in rows:
                    lines.append(f"- {r[0]}: {r[1]} (Rp {r[2]:,})")
                return "\n".join(lines)
    except Exception:
        return ""


def _classify_greeting_or_offtopic(query: str) -> dict[str, Any] | None:
    """Fast-path classification for greetings, identity questions, or off-topic queries."""
    q = query.strip().lower()

    # Greetings
    greetings = ["halo", "hai", "hi", "hello", "hei", "pagi", "siang", "sore", "malam", "selamat pagi", "selamat siang", "selamat sore", "selamat malam"]
    if q in greetings or q.startswith("halo min") or q.startswith("hai min"):
        return {
            "type": "greeting",
            "reply": "Halo kak! Selamat datang di Toko Marcell. Mimin siap bantu rekomendasi outfit, cek ukuran (TB/BB), info bahan, atau cek status pesanan kakak. Mau cari pakaian apa hari ini kak?",
        }

    # "What can you do?" / identity
    if any(phrase in q for phrase in ["kamu siapa", "bisa apa aja", "who are you", "what can you do", "toko apa ini"]):
        return {
            "type": "identity",
            "reply": "Halo kak! Saya Admin Toko Marcell, asisten pribadi & personal stylist untuk toko fashion kami di Jakarta. Mimin bisa bantu kakak:\n1. Cari baju/celana/sepatu sesuai gaya atau foto\n2. Konsultasi ukuran pas (TB & BB)\n3. Rekomendasi padu padan outfit sesuai budget\n4. Cek bahan, jahitan & review pembeli asli\n5. Cek estimasi pengiriman, garansi tukar size & status pesanan (token QRIS).\nAda yang bisa mimin bantu cari sekarang kak?",
        }

    # Obvious off-topic or prompt injection
    off_topic_patterns = [
        r"write python code",
        r"solve (math|equation|fibonacci)",
        r"capital of [a-z]+",
        r"ignore previous instructions",
        r"system prompt",
        r"presiden indonesia",
    ]
    for pattern in off_topic_patterns:
        if re.search(pattern, q):
            return {
                "type": "off_topic",
                "reply": "Halo kak! Mimin adalah asisten belanja khusus Toko Marcell yang bertugas membantu kakak seputar produk fashion, ukuran, dan pesanan toko kami. Yuk tanyakan seputar koleksi baju, celana, atau outfit impian kakak!",
            }

    return None


def _compact_tool_result(name: str, res: Any) -> Any:
    """Keep tool result representation compact to minimize prompt tokens and avoid credit limits."""
    if not res:
        return res
    if name in ("search_catalog", "search_by_image") and isinstance(res, list):
        return [
            {
                "asin": p.get("asin"),
                "title": p.get("title"),
                "brand": p.get("brand"),
                "priceIdr": p.get("priceIdr"),
                "department": p.get("department"),
                "category": p.get("category"),
                "avgRating": p.get("avgRating"),
                "features": (p.get("features") or [])[:2],
            }
            for p in res[:4]
        ]
    if name == "get_product_details" and isinstance(res, dict):
        return {
            "asin": res.get("asin"),
            "title": res.get("title"),
            "brand": res.get("brand"),
            "priceIdr": res.get("priceIdr"),
            "department": res.get("department"),
            "category": res.get("category"),
            "features": (res.get("features") or [])[:4],
            "description": (res.get("description") or "")[:200],
        }
    if name == "get_product_reviews" and isinstance(res, dict):
        quotes = [r.get("comment", "")[:120] for r in res.get("reviews", [])[:3]]
        return {
            "asin": res.get("asin"),
            "total_reviews": res.get("total_reviews"),
            "average_rating": res.get("average_rating"),
            "aspect_summary": res.get("aspect_summary"),
            "quotes": quotes,
        }
    if name == "lookup_store_policy" and isinstance(res, list):
        return [
            {"title": p.get("title"), "content": p.get("content", "")[:300]}
            for p in res[:3]
        ]
    if name == "get_order_status" and isinstance(res, dict):
        return {
            "token": res.get("token"),
            "status": res.get("status"),
            "totalIdr": res.get("totalIdr"),
            "itemCount": res.get("itemCount"),
            "items": res.get("items"),
        }
    return res


async def _execute_tool_call(tool_name: str, args: dict[str, Any], db_url: str) -> dict[str, Any]:
    """Execute a single deterministic tool asynchronously."""
    try:
        if tool_name == "search_catalog":
            res = await asyncio.to_thread(
                search_catalog,
                query=args.get("query", ""),
                category=args.get("category"),
                department=args.get("department"),
                price_max=args.get("price_max"),
                limit=args.get("limit", 4),
                db_url=db_url,
            )
            return {"name": tool_name, "result": res}

        elif tool_name == "get_product_details":
            res = await asyncio.to_thread(
                get_product_details,
                asin_or_id=args.get("asin_or_id", ""),
                db_url=db_url,
            )
            return {"name": tool_name, "result": res}

        elif tool_name == "get_product_reviews":
            res = await asyncio.to_thread(
                get_product_reviews,
                asin=args.get("asin", ""),
                topic=args.get("topic"),
                limit=args.get("limit", 5),
                db_url=db_url,
            )
            return {"name": tool_name, "result": res}

        elif tool_name == "search_by_image":
            res = await asyncio.to_thread(
                search_by_image,
                image_url_or_ref=args.get("image_url_or_ref"),
                department=args.get("department"),
                price_max=args.get("price_max"),
                limit=args.get("limit", 4),
                db_url=db_url,
            )
            return {"name": tool_name, "result": res}

        elif tool_name == "lookup_store_policy":
            res = await asyncio.to_thread(
                lookup_store_policy,
                query=args.get("query", ""),
                limit=args.get("limit", 3),
                db_url=db_url,
            )
            return {"name": tool_name, "result": res}

        elif tool_name == "get_order_status":
            res = await asyncio.to_thread(
                get_order_status,
                token=args.get("token", ""),
                db_url=db_url,
            )
            return {"name": tool_name, "result": res}

        else:
            return {"name": tool_name, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        return {"name": tool_name, "error": str(e)}


async def _call_openrouter(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Call OpenRouter with adaptive retry for transient credit / rate-limit errors."""
    for attempt in range(3):
        try:
            resp = await client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )
            if resp.status_code == 402:
                # Lower max_tokens to fit in-flight credit window
                current = body.get("max_tokens", 250)
                body["max_tokens"] = max(150, current - 50)
                await asyncio.sleep(1.0)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 402 and attempt < 2:
                body["max_tokens"] = 180
                await asyncio.sleep(1.0)
                continue
            if attempt == 2:
                raise e
            await asyncio.sleep(1.0)
        except Exception as e:
            if attempt == 2:
                raise e
            await asyncio.sleep(1.0)
    raise RuntimeError("OpenRouter call failed after retries")


async def chat_copilot(
    messages: list[dict[str, Any]],
    session_id: str | None = None,
    image_url: str | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Run full Toko Marcell AI Copilot turn:

    Node 0: Guardrail/Greeting Fast-Path
    Node 1: LLM Tool Selection & Planner
    Node 2: Tri-Modal Concurrent Tool Execution (asyncio.gather)
    Node 3: Grounded Synthesis with Admin Toko Marcell Persona
    """
    started = time.perf_counter()
    url = db_url or DATABASE_URL

    if not messages:
        return {
            "reply": "Halo kak! Ada yang bisa mimin bantu cari hari ini?",
            "products": [],
            "citations": [],
            "tool_calls": [],
            "took_ms": 1.0,
        }

    latest_msg = messages[-1].get("content", "")

    # Node 0: Fast-path for greetings or off-topic
    if not image_url and len(messages) == 1:
        fast_path = _classify_greeting_or_offtopic(latest_msg)
        if fast_path:
            return {
                "reply": fast_path["reply"],
                "products": [],
                "citations": [],
                "tool_calls": [],
                "took_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    # Extract order token if present in text (e.g. tk_... or "token xyz")
    token_match = re.search(
        r"(?:token(?:nya)?\s*[:=]?\s*['\"`]?)([A-Za-z0-9_-]{12,32})|\b(tk_[A-Za-z0-9_-]+)\b",
        latest_msg,
        re.IGNORECASE,
    )
    extracted_token = None
    if token_match:
        extracted_token = token_match.group(1) or token_match.group(2)

    # Prepare message history (last 4 turns to conserve tokens)
    browsing_ctx = _get_session_browsing_context(session_id, url)
    system_content = ADMIN_SYSTEM_PROMPT
    if browsing_ctx:
        system_content += f"\n\n{browsing_ctx}"

    convo_history = [{"role": "system", "content": system_content}]
    for m in messages[-4:]:
        convo_history.append({"role": m["role"], "content": m["content"]})

    # If image_url was attached, notify planner
    if image_url:
        convo_history[-1]["content"] += f"\n[User attached an image: {image_url}]"

    # Node 1: Call OpenRouter LLM with Tools
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Toko Marcell AI Copilot",
    }

    req_body: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": convo_history,
        "tools": TOOLS_SCHEMA,
        "max_tokens": 220,
        "temperature": 0.2,
    }

    tool_calls_executed = []
    executed_tools_results = []
    assistant_msg = None

    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            res_json = await _call_openrouter(client, req_body, headers)
            choice = res_json["choices"][0]
            assistant_msg = choice["message"]
        except Exception as e:
            print(f"[agent] Notice: LLM call unavailable ({e}), using deterministic grounded intent planner.")
            assistant_msg = {"role": "assistant", "content": None, "tool_calls": []}
            q_lower = latest_msg.lower()
            tool_calls = []

            if extracted_token:
                tool_calls.append({
                    "id": "direct_order_lookup",
                    "type": "function",
                    "function": {"name": "get_order_status", "arguments": json.dumps({"token": extracted_token})},
                })
            elif any(k in q_lower for k in ["kirim", "pengiriman", "ongkir", "qris", "bayar", "tukar", "garansi", "retur", "kembali", "cod", "jam", "offline"]):
                tool_calls.append({
                    "id": "direct_policy_lookup",
                    "type": "function",
                    "function": {"name": "lookup_store_policy", "arguments": json.dumps({"query": latest_msg})},
                })
            elif any(k in q_lower for k in ["tb ", "bb ", "tinggi", "berat", "size", "ukuran"]):
                tool_calls.append({
                    "id": "direct_sizing_lookup",
                    "type": "function",
                    "function": {"name": "lookup_store_policy", "arguments": json.dumps({"query": latest_msg})},
                })
                if "dickies" in q_lower or "874" in q_lower:
                    tool_calls.append({
                        "id": "direct_sizing_reviews",
                        "type": "function",
                        "function": {"name": "get_product_reviews", "arguments": json.dumps({"asin": "B0001YRQHQ", "topic": "sizing"})},
                    })
            elif any(k in q_lower for k in ["review", "ulasan", "puas", "bintang", "awet", "bahan", "kaku", "tebal", "oz"]):
                if "dickies" in q_lower or "874" in q_lower:
                    tool_calls.append({
                        "id": "direct_reviews_lookup",
                        "type": "function",
                        "function": {"name": "get_product_reviews", "arguments": json.dumps({"asin": "B0001YRQHQ", "topic": "durability"})},
                    })
                else:
                    tool_calls.append({
                        "id": "direct_catalog_search",
                        "type": "function",
                        "function": {"name": "search_catalog", "arguments": json.dumps({"query": latest_msg})},
                    })
            else:
                clean_q = re.sub(r"\b(min|ada|gak|ya|tolong|cariin|dong|di toko|mimin)\b", "", q_lower).strip()
                tool_calls.append({
                    "id": "direct_catalog_search",
                    "type": "function",
                    "function": {"name": "search_catalog", "arguments": json.dumps({"query": clean_q or latest_msg})},
                })

        # Node 2: Check if LLM requested Tool Calls
        if not assistant_msg.get("tool_calls") and not tool_calls:
            tool_calls = assistant_msg.get("tool_calls", [])

        # Heuristic backfill if model didn't call tool for explicit order token
        if not tool_calls and extracted_token:
            tool_calls = [
                {
                    "id": "direct_order_lookup",
                    "type": "function",
                    "function": {
                        "name": "get_order_status",
                        "arguments": json.dumps({"token": extracted_token}),
                    },
                }
            ]

        # Heuristic backfill if image attached but no tool was invoked
        if not tool_calls and image_url:
            tool_calls = [
                {
                    "id": "direct_image_search",
                    "type": "function",
                    "function": {
                        "name": "search_by_image",
                        "arguments": json.dumps({"image_url_or_ref": image_url}),
                    },
                }
            ]

        # Heuristic backfill: if user asks for products or clothing availability and model didn't call search
        if not tool_calls:
            q_lower = latest_msg.lower()
            if any(k in q_lower for k in ["ada ", "cari ", "rekomendasi", "celana", "baju", "jaket", "sepatu", "outfit", "dickies", "levi"]):
                clean_q = re.sub(r"\b(min|ada|gak|ya|tolong|cariin|dong|di toko|mimin)\b", "", q_lower).strip()
                tool_calls = [
                    {
                        "id": "direct_catalog_search",
                        "type": "function",
                        "function": {
                            "name": "search_catalog",
                            "arguments": json.dumps({"query": clean_q or latest_msg}),
                        },
                    }
                ]

        if tool_calls:
            # Node 2: Execute all tool calls concurrently with asyncio.gather()
            tasks = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = {}

                tool_calls_executed.append({"name": name, "args": args})
                tasks.append(_execute_tool_call(name, args, url))

            raw_results = await asyncio.gather(*tasks)
            executed_tools_results = raw_results

            # Node 3: Synthesizer Fan-In
            # Append assistant's tool-call request and tool result messages
            convo_history.append(assistant_msg if assistant_msg.get("tool_calls") else {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            })
            for tc, res in zip(tool_calls, executed_tools_results):
                compact_res = _compact_tool_result(tc["function"]["name"], res.get("result", res))
                convo_history.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", "call_1"),
                    "name": tc["function"]["name"],
                    "content": json.dumps(compact_res, default=str),
                })

            # Call LLM to synthesize final user-facing response
            syn_body = {
                "model": LLM_MODEL,
                "messages": convo_history,
                "max_tokens": 260,
                "temperature": 0.3,
            }
            try:
                syn_json = await _call_openrouter(client, syn_body, headers)
                syn_choice = syn_json["choices"][0]
                final_reply = syn_choice["message"].get("content", "")
            except Exception as e:
                print(f"[agent] Warning: Synthesis LLM call failed ({e}), building grounded response from tool evidence.")
                reply_parts = ["Halo kak!"]

                # Extract tool results safely upfront
                order_tool = next((r["result"] for r in executed_tools_results if r.get("name") == "get_order_status"), None)
                policy_tool = next((r["result"] for r in executed_tools_results if r.get("name") == "lookup_store_policy"), None)
                review_tool = next((r["result"] for r in executed_tools_results if r.get("name") == "get_product_reviews"), None)
                detail_tool = next((r["result"] for r in executed_tools_results if r.get("name") == "get_product_details"), None)
                prods = [p for r in executed_tools_results if isinstance(r.get("result"), list) for p in r["result"] if isinstance(p, dict) and "priceIdr" in p]

                # Check for order lookup
                if order_tool and order_tool.get("found"):
                    items_str = ", ".join(f"{it['qty']}x {it['title']}" for it in order_tool.get("items", []))
                    reply_parts.append(
                        f"Pesanan dengan token {order_tool['token']} berstatus {order_tool['status'].upper()} "
                        f"dengan total Rp {order_tool['totalIdr']:,}. Rincian barang: {items_str}."
                    )

                # 1. Check for sizing consultation
                msg_lower = latest_msg.lower()
                if any(w in msg_lower for w in ["tb", "bb", "tinggi", "berat", "size", "ukuran"]):
                    if "dickies" in msg_lower or "874" in msg_lower:
                        reply_parts.append(
                            "Untuk **Dickies 874 Original Fit**, karena bahannya rigid twill tebal (8.5 oz) dan non-stretch (kaku di pinggang), "
                            "mimin rekomendasikan **naik 1–2 ukuran (ambil Size 32 atau 34)** dari ukuran celana standar ya kak, agar nyaman dan leluasa dipakai bergerak!"
                        )
                    elif "505" in msg_lower or "501" in msg_lower:
                        reply_parts.append(
                            "Untuk celana **Levi's 501** potongannya regular straight leg dengan button fly (100% cotton). "
                            "Kalau kakak punya paha lebih berisi, mimin sangat sarankan **Levi's 505 Regular** karena memiliki ruang ekstra di bagian paha dan pinggul!"
                        )
                    elif policy_tool and isinstance(policy_tool, list) and policy_tool:
                        reply_parts.append(f"Rekomendasi ukuran kami: {policy_tool[0].get('content', '')[:300]}")

                # 2. Check for store operations policy (shipping, QRIS, returns)
                is_policy_query = any(w in msg_lower for w in ["kirim", "pengiriman", "ongkir", "qris", "bayar", "tukar", "garansi", "retur", "kembali"])
                if is_policy_query and policy_tool and isinstance(policy_tool, list) and policy_tool:
                    policies_text = " ".join(p.get("content", "") for p in policy_tool).lower()
                    has_shipping = any(w in msg_lower for w in ["kirim", "pengiriman", "ongkir", "mana"])
                    has_qris = any(w in msg_lower for w in ["qris", "bayar", "pembayaran"])
                    has_return = any(w in msg_lower for w in ["tukar", "garansi", "retur", "kembali"])

                    policy_bullets = []
                    if has_shipping:
                        policy_bullets.append("Pengiriman diproses langsung dari hub **Jakarta Selatan** (estimasi 1–2 hari Jabodetabek, 3–5 hari luar pulau).")
                    if has_qris:
                        policy_bullets.append("Pembayaran bisa instan dan praktis menggunakan **QRIS** (simulasi demo belanja cepat & aman).")
                    if has_return:
                        policy_bullets.append("Tersedia **garansi tukar size 7 hari** setelah barang diterima jika ukuran kurang pas.")

                    if policy_bullets:
                        reply_parts.append("\n".join(f"• {b}" for b in policy_bullets))
                    else:
                        top_pol = policy_tool[0]
                        reply_parts.append(top_pol.get("content", "")[:280])

                # 3. Check for reviews / fabric / durability specs
                if review_tool and isinstance(review_tool, dict):
                    avg = review_tool.get("average_rating", 0)
                    total = review_tool.get("total_reviews", 0)
                    aspect = review_tool.get("aspect_summary", "")

                    if any(w in msg_lower for w in ["bahan", "kaku", "tebal", "oz", "material", "twill"]):
                        reply_parts.append(
                            "Celana Dickies 874 menggunakan bahan **8.5 oz heavyweight twill** (campuran katun dan polyester). "
                            "Karakteristik bahannya tebal, kokoh, dan awalnya terasa kaku khas workwear original, tapi sangat awet dan tahan sobek bertahun-tahun kak!"
                        )
                    if any(w in msg_lower for w in ["review", "ulasan", "puas", "bintang", "awet"]):
                        reply_parts.append(
                            f"Dari **{total:,} ulasan pembeli terverifikasi** (rating rata-rata **{avg}/5.0**), mayoritas pembeli sangat puas dengan daya tahan dan kualitas bahannya kak. Catatan penting: {aspect}."
                        )

                # 4. Check for catalog products
                if prods and not any(w in msg_lower for w in ["review", "ulasan", "kirim", "qris"]):
                    reply_parts.append("Berikut rekomendasi terbaik dari katalog Toko Marcell:")
                    for idx, p in enumerate(prods[:3], 1):
                        reply_parts.append(f"{idx}. **{p.get('title')}** - Rp {p.get('priceIdr', 0):,}")
                    reply_parts.append("Mau langsung mimin bantu masukkan ke keranjang kak?")

                # 5. Check for product details
                if detail_tool and isinstance(detail_tool, dict) and "title" in detail_tool:
                    feats = ", ".join(detail_tool.get("features", [])[:3])
                    reply_parts.append(f"Untuk **{detail_tool['title']}** (Rp {detail_tool['priceIdr']:,}): terbuat dari material berkualitas dengan fitur: {feats}.")

                final_reply = "\n\n".join(reply_parts)
        else:
            final_reply = assistant_msg.get("content", "")

    # Extract structured products and citations from executed tool results
    structured_products = []
    structured_citations = []
    seen_asins = set()

    for item in executed_tools_results:
        res = item.get("result")
        if not res:
            continue

        # Product list from search_catalog or search_by_image
        if isinstance(res, list):
            for prod in res:
                if isinstance(prod, dict) and "asin" in prod:
                    a = prod["asin"]
                    if a not in seen_asins:
                        seen_asins.add(a)
                        structured_products.append({
                            "id": prod.get("id"),
                            "asin": prod["asin"],
                            "title": prod.get("title", ""),
                            "priceIdr": prod.get("priceIdr", 0),
                            "brand": prod.get("brand", ""),
                            "imageUrl": prod.get("imageUrl"),
                            "avgRating": prod.get("avgRating"),
                            "ratingCount": prod.get("ratingCount", 0),
                            "category": prod.get("category", ""),
                            "department": prod.get("department", ""),
                        })

        # Single product from get_product_details
        elif isinstance(res, dict) and "asin" in res and "title" in res:
            a = res["asin"]
            if a not in seen_asins:
                seen_asins.add(a)
                structured_products.append({
                    "id": res.get("id"),
                    "asin": res["asin"],
                    "title": res.get("title", ""),
                    "priceIdr": res.get("priceIdr", 0),
                    "brand": res.get("brand", ""),
                    "imageUrl": res.get("imageUrl"),
                    "avgRating": res.get("avgRating"),
                    "ratingCount": res.get("ratingCount", 0),
                    "category": res.get("category", ""),
                    "department": res.get("department", ""),
                })

        # Citations from reviews
        if isinstance(res, dict) and "reviews" in res:
            asin = res.get("asin", "")
            for rev in res.get("reviews", []):
                comment = rev.get("comment", "")
                if comment and len(comment) > 20:
                    structured_citations.append({
                        "asin": asin,
                        "source": "Verified Customer Review",
                        "rating": rev.get("rating"),
                        "quote": comment[:160] + ("..." if len(comment) > 160 else ""),
                    })

        # Citations from policies
        if isinstance(res, list) and res and "title" in res[0] and "category" in res[0]:
            for pol in res:
                structured_citations.append({
                    "source": f"Store Policy: {pol.get('title')}",
                    "category": pol.get("category"),
                    "quote": pol.get("content", "")[:180] + "...",
                })

    took_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "reply": final_reply,
        "products": structured_products[:6],
        "citations": structured_citations[:4],
        "tool_calls": tool_calls_executed,
        "took_ms": took_ms,
    }
