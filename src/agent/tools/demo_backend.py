"""Stand-in business system for the demo.

Deterministic fake data so a demo behaves identically every time it is run. The
tool *shapes* are real — this is what a client integration replaces, one
handler at a time, without the agent changing.
"""

from __future__ import annotations

import asyncio

from agent.tools.registry import ToolRegistry

__all__ = ["build_demo_registry"]

# Deliberately small and fixed. A demo that returns random data cannot be
# rehearsed, and an unrehearsed demo is where the surprises happen.
_ORDERS: dict[str, dict[str, str]] = {
    "4471": {"status": "راستے میں", "eta": "کل شام تک", "courier": "TCS"},
    "1200": {"status": "ڈیلیور ہو چکا", "eta": "دو دن پہلے", "courier": "Leopards"},
}
_BALANCES: dict[str, int] = {"03001234567": 1500, "03219876543": 250}

# Real systems are not instant. Zero-latency tools hide the need for a filler
# line and make the demo misleading about where the time goes.
_LOOKUP_DELAY_S = 0.12


async def _get_order_status(order_id: str) -> str:
    await asyncio.sleep(_LOOKUP_DELAY_S)
    order = _ORDERS.get(order_id.strip().upper().removeprefix("ORD-"))
    if order is None:
        return f"Order {order_id} not found. Ask the caller to confirm the number."
    return (
        f"Order {order_id}: status {order['status']}, expected {order['eta']}, "
        f"courier {order['courier']}."
    )


async def _get_balance(phone: str) -> str:
    await asyncio.sleep(_LOOKUP_DELAY_S)
    digits = "".join(c for c in phone if c.isdigit())
    balance = _BALANCES.get(digits)
    if balance is None:
        return f"No account found for {phone}. Ask the caller to confirm the number."
    return f"Account {phone} has a balance of {balance} rupees."


async def _raise_complaint(summary: str, phone: str = "") -> str:
    await asyncio.sleep(_LOOKUP_DELAY_S)
    return f"Complaint logged as CMP-8891 for {phone or 'this caller'}: {summary}"


def build_demo_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        "get_order_status",
        "Look up the delivery status of an order by its order number.",
        {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order number, e.g. 4471"}
            },
            "required": ["order_id"],
        },
        _get_order_status,
    )

    registry.register(
        "get_balance",
        "Fetch the account balance for a customer's phone number.",
        {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Phone number, e.g. 03001234567"}
            },
            "required": ["phone"],
        },
        _get_balance,
    )

    registry.register(
        "raise_complaint",
        "File a complaint on the caller's behalf and return a reference number.",
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One-line summary in Urdu"},
                "phone": {"type": "string", "description": "Caller's phone number if known"},
            },
            "required": ["summary"],
        },
        _raise_complaint,
    )

    return registry
