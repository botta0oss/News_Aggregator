"""Closing line value: did the price move our way before the market closed?

The closing price (last price while trading) is the market's best estimate once all the
information is in. Buying below it, consistently, is the most reliable sign of a real edge,
and it can be measured long before enough markets resolve for the Brier score or the profit
to mean anything. Values are in probability points of the side bought.
"""
from typing import Optional


def side_price(yes_price: Optional[float], side: str) -> Optional[float]:
    if yes_price is None:
        return None
    return yes_price if side == "YES" else 1.0 - yes_price


def clv(entry_price: Optional[float], closing_yes: Optional[float], side: str) -> Optional[float]:
    """Closing price of the side bought minus the price paid (> 0: beat the close)."""
    close = side_price(closing_yes, side)
    if entry_price is None or close is None:
        return None
    return round(close - entry_price, 4)


def summarize(values: list) -> dict:
    values = [v for v in values if v is not None]
    return {
        "n": len(values),
        "avg": round(sum(values) / len(values), 4) if values else None,
        "share_positive": round(sum(1 for v in values if v > 0) / len(values), 4) if values else None,
    }
