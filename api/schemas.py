"""
Pydantic models shared across routers.

Extracted from main.py so that routers and tests can import them without
pulling in the entire application module.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Events ───────────────────────────────────────────────────────────────────

class EventIn(BaseModel):
    """One shopper action. Pydantic rejects anything malformed with a 422, so the
    events table cannot fill up with misspelled event names."""

    event_type: Literal[
        "view_product",
        "search",
        "add_to_cart",
        "checkout_start",
        "purchase_mock",
        "copilot_message",
        "copilot_product_click",
        "copilot_add_to_cart",
    ]
    session_id: str = Field(min_length=8, max_length=64)
    asin: str | None = Field(default=None, max_length=32)
    query: str | None = Field(default=None, max_length=200)
    results_count: int | None = Field(default=None, ge=0)
    qty: int | None = Field(default=None, ge=1, le=99)
    price_idr: int | None = Field(default=None, ge=0)


# ── Checkout ─────────────────────────────────────────────────────────────────

class ConfirmItem(BaseModel):
    """What the client is allowed to say about a line: which product, how many.

    Deliberately no price. The server looks it up.
    """

    model_config = ConfigDict(extra="forbid")

    asin: str = Field(min_length=1, max_length=32)
    qty: int = Field(ge=1, le=99)


class ConfirmIn(BaseModel):
    """The checkout request.

    ``session_id`` is required, not optional: a purchase that cannot be attributed to
    a visit is useless to the funnel, and the events table enforces the same rule.

    ``extra="forbid"`` makes the no-client-price rule explicit: a request that tries
    to send a total is rejected loudly rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ConfirmItem] = Field(min_length=1, max_length=50)
    session_id: str = Field(min_length=8, max_length=64)


# ── Copilot ──────────────────────────────────────────────────────────────────

# v2 (design §API contract v2) — the old multi-message / image-upload request
# schema (CopilotChatIn/CopilotMessage) was removed with the agent rewrite.

class CopilotContextV2(BaseModel):
    """Where the shopper is: the product page they're on and any products they
    explicitly referenced (compare tray / pinned)."""

    model_config = ConfigDict(extra="forbid")

    page_asin: str | None = Field(default=None, max_length=32)
    referenced_asins: list[str] = Field(default_factory=list, max_length=3)


class CopilotChatV2In(BaseModel):
    """v2 request. Text-only (no image upload — design §4).

    ``thread_id`` is a browser-generated UUID string that keys the LangGraph
    Postgres checkpointer; ``message`` is a single turn (history lives in the
    checkpointer, not the request).
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, max_length=64)
    thread_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    context: CopilotContextV2 | None = None


class CopilotProductV2(BaseModel):
    """A product card in the v2 response. ``ref`` is the stable number shown in
    the chat ("[1]", "[2]") so follow-ups can say "yang kedua"."""

    ref: int
    id: int | None = None
    asin: str
    title: str
    priceIdr: int
    brand: str | None = None
    imageUrl: str | None = None
    avgRating: float | None = None
    ratingCount: int | None = None
    category: str | None = None
    department: str | None = None
    fit: dict | None = None


class CopilotCitationV2(BaseModel):
    type: Literal["policy", "product", "reviews"]
    title: str
    ref: str


class CopilotSuggestionV2(BaseModel):
    label: str
    prompt: str


class CopilotUIActionV2(BaseModel):
    """A browser-executed action. ``add_to_cart`` (server-validated) or a
    ``size_form`` prompt."""

    type: Literal["add_to_cart", "size_form"]
    asin: str | None = None
    qty: int | None = None
    size: str | None = None
    title: str | None = None
    priceIdr: int | None = None


class CopilotToolCallV2(BaseModel):
    name: str
    args: dict


class CopilotChatV2Out(BaseModel):
    reply: str
    thread_id: str
    products: list[CopilotProductV2] = Field(default_factory=list, max_length=6)
    citations: list[CopilotCitationV2] = Field(default_factory=list)
    suggestions: list[CopilotSuggestionV2] = Field(default_factory=list)
    ui_actions: list[CopilotUIActionV2] = Field(default_factory=list)
    tool_calls: list[CopilotToolCallV2] = Field(default_factory=list)
    guardrail: Literal["greeting", "off_topic", "injection"] | None = None
    took_ms: float


class CopilotThreadMessageV2(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CopilotThreadV2Out(BaseModel):
    thread_id: str
    messages: list[CopilotThreadMessageV2] = Field(default_factory=list)
