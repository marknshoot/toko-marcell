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

class CopilotMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=2000)
    imageUrl: str | None = Field(default=None, max_length=8_000_000)


class CopilotChatIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    messages: list[CopilotMessage] = Field(min_length=1, max_length=20)
    image_url: str | None = Field(default=None, max_length=8_000_000)
