"""Arbitrage on events with mutually exclusive outcomes (Polymarket negRisk).

Exactly one outcome wins, so one YES share of every outcome always pays 1 $, and one NO
share of every outcome always pays N − 1 $ (all lose but one). If buying the whole set costs
less than that, fees included, the difference is locked in whatever happens.

Only the top of the book is known here (Gamma's best bid/ask), not its depth: the gap may
be available for a handful of shares only, and prices move fast. It is a flag to look at,
not an order.
"""
from dataclasses import dataclass
from typing import Optional

from backend.betting.fees import fee_per_share

MIN_PROFIT = 0.005   # at least half a cent per set of shares, after fees


@dataclass
class Arbitrage:
    kind: str          # buy_all_yes / buy_all_no
    cost: float        # to buy one share of every outcome, fees included
    payout: float      # what the set pays in any case
    profit: float      # payout − cost, per set
    outcomes: int

    def as_dict(self) -> dict:
        return {"kind": self.kind, "cost": round(self.cost, 4), "payout": self.payout, "profit": round(self.profit, 4),
                "return": round(self.profit / self.cost, 4) if self.cost else None, "outcomes": self.outcomes}


def find(quotes: list[tuple[Optional[float], Optional[float]]], fee_bps: float) -> Optional[Arbitrage]:
    """quotes: (best YES ask, best YES bid) of EVERY outcome of the event. A NO share costs
    about 1 − the YES bid. None if a quote is missing or there is no gap worth flagging."""
    n = len(quotes)
    if n < 2:
        return None
    found = []
    asks = [a for a, _ in quotes]
    if all(a is not None and 0 < a < 1 for a in asks):
        cost = sum(a + fee_per_share(a, fee_bps) for a in asks)
        found.append(Arbitrage("buy_all_yes", cost, 1.0, 1.0 - cost, n))
    no_asks = [1.0 - b if b is not None else None for _, b in quotes]
    if all(a is not None and 0 < a < 1 for a in no_asks):
        cost = sum(a + fee_per_share(a, fee_bps) for a in no_asks)
        found.append(Arbitrage("buy_all_no", cost, float(n - 1), (n - 1) - cost, n))
    best = max(found, key=lambda x: x.profit / x.cost, default=None)
    return best if best is not None and best.profit >= MIN_PROFIT else None
