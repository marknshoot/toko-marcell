"""Checkout endpoints: confirm, order lookup."""

import logging
import secrets

from db import get_conn
from fastapi import APIRouter, HTTPException
from schemas import ConfirmIn

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/checkout/confirm", status_code=201)
def confirm_checkout(payload: ConfirmIn):
    """Turn a cart into a paid order — the authoritative step."""
    wanted: dict[str, int] = {}
    for item in payload.items:
        wanted[item.asin] = wanted.get(item.asin, 0) + item.qty

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asin, title, price_idr FROM products WHERE asin = ANY(%s)",
                (list(wanted),),
            )
            found = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

            unknown = sorted(set(wanted) - set(found))
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown asin(s): {', '.join(unknown)}",
                )

            lines = [
                (asin, found[asin][0], qty, found[asin][1])
                for asin, qty in wanted.items()
            ]
            total_idr = sum(qty * unit_price for _, _, qty, unit_price in lines)
            item_count = sum(qty for _, _, qty, _ in lines)

            token = f"tk_{secrets.token_urlsafe(12)}"
            cur.execute(
                """
                INSERT INTO orders (token, total_idr, item_count, status, session_id)
                VALUES (%s, %s, %s, 'paid', %s)
                RETURNING id
                """,
                (token, total_idr, item_count, payload.session_id),
            )
            order_id = cur.fetchone()[0]

            for asin, title, qty, unit_price in lines:
                cur.execute(
                    """
                    INSERT INTO order_items (order_id, asin, title, qty, unit_price_idr)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (order_id, asin, title, qty, unit_price),
                )

            cur.execute(
                """
                INSERT INTO events (session_id, event_type, qty, price_idr)
                VALUES (%s, 'purchase_mock', %s, %s)
                """,
                (payload.session_id, item_count, total_idr),
            )

            conn.commit()

    return {
        "token": token,
        "status": "paid",
        "total_idr": total_idr,
        "item_count": item_count,
        "items": [
            {"asin": asin, "title": title, "qty": qty, "unit_price_idr": unit_price}
            for asin, title, qty, unit_price in lines
        ],
    }


@router.get("/orders/{token}")
def get_order(token: str):
    """Read an order back by token."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, token, total_idr, item_count, status, created_at
                FROM orders WHERE token = %s
                """,
                (token,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Order not found")

            order_id, token, total_idr, item_count, status, created_at = row
            cur.execute(
                """
                SELECT asin, title, qty, unit_price_idr
                FROM order_items WHERE order_id = %s ORDER BY id
                """,
                (order_id,),
            )
            items = [
                {"asin": asin, "title": title, "qty": qty, "unitPriceIdr": unit_price}
                for asin, title, qty, unit_price in cur.fetchall()
            ]

    return {
        "token": token,
        "status": status,
        "totalIdr": total_idr,
        "itemCount": item_count,
        "createdAt": created_at.isoformat(),
        "items": items,
    }
