"""Spend guard for Meta Model API calls.

Meta's billing only sends email alerts, so we enforce our own limits:
every call is refused once today's spend or the all-time spend reaches its cap.
"""
import json
import os
from datetime import date

from locksmith import db

USAGE_FILE = db.ROOT / "usage.json"

# USD per 1M tokens (input, output). Unknown models fall back to the most expensive.
PRICES = {
    "muse-spark-1.3": (1.25, 4.25),
    "muse-spark-1.2": (1.25, 4.25),
    "muse-spark-1.1": (1.25, 4.25),
    "muse-wonder-1.0": (4.00, 20.00),
}
FALLBACK_PRICE = (4.00, 20.00)


class BudgetExceeded(Exception):
    pass


def limits() -> tuple[float, float]:
    return (
        float(os.environ.get("LOCKSMITH_DAILY_USD", "5")),
        float(os.environ.get("LOCKSMITH_TOTAL_USD", "100")),
    )


def _load() -> dict:
    if USAGE_FILE.exists():
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    return {"total_usd": 0.0, "days": {}}


def _save(usage: dict) -> None:
    USAGE_FILE.write_text(json.dumps(usage, indent=2), encoding="utf-8")


def spent() -> dict:
    usage = _load()
    return {"today_usd": usage["days"].get(date.today().isoformat(), 0.0), "total_usd": usage["total_usd"]}


def check() -> None:
    daily, total = limits()
    s = spent()
    if s["today_usd"] >= daily:
        raise BudgetExceeded(f"daily limit reached: ${s['today_usd']:.4f} of ${daily:.2f}")
    if s["total_usd"] >= total:
        raise BudgetExceeded(f"total limit reached: ${s['total_usd']:.4f} of ${total:.2f}")


def record(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, FALLBACK_PRICE)
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    usage = _load()
    today = date.today().isoformat()
    usage["days"][today] = usage["days"].get(today, 0.0) + cost
    usage["total_usd"] += cost
    _save(usage)
    return cost


def create_response(client, **kwargs):
    """client.responses.create with the budget checked before and recorded after."""
    check()
    response = client.responses.create(**kwargs)
    if response.usage:
        record(kwargs["model"], response.usage.input_tokens, response.usage.output_tokens)
    return response
