import math

import pytest

from backend.betting.economics import (
    Exposure, Quote, annualize, buy, estimated_quote, evaluate, fee_per_share, fill, kelly_stake, model_sigma,
)
from backend.betting.profiles import PROFILES, get_profile

BOOK = [(0.36, 500), (0.37, 1000), (0.40, 3000)]


def run(**over):
    args = dict(signal="BUY_YES", p_yes=0.519, sigma=0.0375, quote=Quote(asks=BOOK, mid=0.35), days=30,
                profile=get_profile("bilanciato"), equity=1000, available_cash=1000, exposure=Exposure(),
                liquidity=50_000, risk_free_rate=0.04)
    args.update(over)
    return evaluate(**args)


def codes(ev):
    return {r["code"] for r in ev.reasons}


def test_book_walk_and_fees():
    shares, spent = buy(BOOK, 200)
    assert spent == pytest.approx(200) and shares == pytest.approx(500 + 20 / 0.37)
    assert buy(BOOK, 10_000, max_price=0.37) == pytest.approx((1500, 180 + 370))
    assert fee_per_share(0.36, 100) == pytest.approx(0.01 * 0.36 * 0.64)   # rate × p × (1 - p)
    assert fee_per_share(0.9, 100) == pytest.approx(0.01 * 0.9 * 0.1)
    assert fee_per_share(0.5, 400) == pytest.approx(0.01)                  # 4% rate: 1¢ at 50¢
    assert fill(BOOK, 36, 1.0, 100)[2] == pytest.approx(100 * 0.01 * 0.36 * 0.64)


def test_limit_price_leaves_the_net_edge_after_fees():
    from backend.betting.economics import max_price_for
    for rate_bps in (0, 400, 700, 1000):
        for target in (0.05, 0.3, 0.5, 0.8, 0.97):
            x = max_price_for(target, rate_bps)
            assert x + fee_per_share(x, rate_bps) == pytest.approx(target, abs=1e-9)
    # At the limit price the net edge equals the preset minimum, never less
    profile = get_profile("bilanciato")
    ev = evaluate(signal="BUY_YES", p_yes=0.6, sigma=0.0,
                  quote=Quote(asks=[(0.3, 1e6)], mid=0.3, fee_bps=1000, source="book"),
                  days=30, profile=profile, equity=1000, available_cash=1000, exposure=Exposure(),
                  liquidity=1e6, risk_free_rate=0.04)
    x = ev.limit_price
    assert 0.6 - (x + fee_per_share(x, 1000)) == pytest.approx(profile.min_net_edge, abs=1e-9)


def test_kelly_matches_closed_form_on_deep_book():
    # Unlimited depth at one price: book-aware Kelly = classic (p - q) / (1 - q)
    stake = kelly_stake(0.6, [(0.5, 1e9)], 0.0, 1000, 1.0)
    assert stake == pytest.approx(1000 * (0.6 - 0.5) / 0.5, rel=0.01)
    assert kelly_stake(0.4, [(0.5, 1e9)], 0.0, 1000, 1.0) == 0.0


def test_sigma_shrinks_with_evidence_and_weight():
    assert model_sigma(0.8, 1.0, 0.5, 20) < model_sigma(0.8, 0.2, 0.5, 20)
    assert model_sigma(0.8, 0.75, 0.0, 20) == 0.0
    assert model_sigma(0.8, 0.75, 0.375, 20) == pytest.approx(0.375 * math.sqrt(0.16 / 16))


def test_annualize():
    assert annualize(0.10, 365) == pytest.approx(0.10)
    assert annualize(0.10, 182.5) == pytest.approx(0.21)
    assert annualize(0.5, 1) == 10.0  # capped
    assert annualize(-1.0, 30) == -1.0


def test_good_bet_is_sized_and_capped():
    ev = run()
    assert ev.verdict == "GO" and ev.side == "YES"
    assert ev.p_conservative == pytest.approx(0.519 - 0.0375)
    assert ev.limit_price == pytest.approx(ev.p_conservative - 0.03)
    assert ev.target_stake == pytest.approx(0.25 * ev.kelly_stake)
    assert ev.stake == pytest.approx(40)  # 4% of 1000 per market
    assert ev.limited_by == "market"
    assert ev.avg_price == pytest.approx(0.36)
    assert ev.expected_profit == pytest.approx(0.519 * ev.shares - 40)
    assert ev.break_even == pytest.approx(0.36)
    assert ev.prob_loss == pytest.approx(0.481)


def test_no_side_uses_complement():
    ev = run(signal="BUY_NO", p_yes=0.2, quote=Quote(asks=[(0.66, 5000)], mid=0.65))
    assert ev.side == "NO" and ev.p_side == pytest.approx(0.8)
    assert ev.verdict == "GO"


def test_spread_eats_the_edge():
    ev = run(quote=Quote(asks=[(0.47, 5000)], mid=0.35))
    assert ev.verdict == "NO" and "edge_after_costs" in codes(ev)
    assert ev.stake == 0 and ev.outlay == 0


def test_uncertainty_can_kill_a_signal():
    ev = run(sigma=0.15)
    assert ev.verdict == "NO" and "edge_after_costs" in codes(ev)


def test_long_horizon_needs_bigger_edge():
    # 3 points of edge (7.75% return) over 300 days: ~9.4% a year, below the 12% hurdle
    ev = run(p_yes=0.431, sigma=0.0, quote=Quote(asks=[(0.40, 10_000)], mid=0.40), days=300)
    assert "return_too_low" in codes(ev) and ev.verdict == "NO"
    assert run(p_yes=0.431, sigma=0.0, quote=Quote(asks=[(0.40, 10_000)], mid=0.40), days=20).verdict != "NO"


def test_preset_filters():
    assert "illiquid" in codes(run(liquidity=5_000))
    assert "too_far" in codes(run(days=400))
    assert "no_signal" in codes(run(signal="HOLD"))
    assert "no_book" in codes(run(quote=Quote(asks=[], mid=0.35)))
    prudent, bold = run(profile=PROFILES["prudente"]), run(profile=PROFILES["aggressivo"])
    assert prudent.stake < bold.stake


def test_exposure_caps():
    full = run(exposure=Exposure(market=40))
    assert full.verdict == "NO" and "exposure_cap" in codes(full)
    small = run(exposure=Exposure(category=240))  # 10 $ left in the category (25% of 1000)
    assert small.verdict == "SMALL" and small.stake == pytest.approx(10) and small.limited_by == "category"
    assert "no_cash" in codes(run(available_cash=0))


def test_min_order_and_estimated_quote():
    tiny = run(equity=20, available_cash=20)
    assert "stake_below_min" in codes(tiny)
    q = estimated_quote(0.35, 0.02, 50_000, 0.0)
    assert q.source == "estimate" and q.asks[0][0] == pytest.approx(0.36)
    # Several levels, one spread apart, holding half the liquidity in dollars
    assert [p for p, _ in q.asks] == pytest.approx([0.36, 0.38, 0.40, 0.42])
    assert sum(p * s for p, s in q.asks) == pytest.approx(25_000)
    # Near 1 the levels are capped at 0.99 and merged
    top = estimated_quote(0.97, 0.02, 1000, 0.0)
    assert [p for p, _ in top.asks] == [0.98, 0.99] and sum(p * s for p, s in top.asks) == pytest.approx(500)
    ev = run(quote=q)
    assert ev.quote_source == "estimate" and ev.notes


def test_annualize_does_not_overflow():
    """Regression: a 1¢ share with a day left gave OverflowError (500 on /economics)."""
    from backend.betting.economics import MAX_APR, annualize
    assert annualize(99.0, 1.0) == MAX_APR
    assert annualize(10.0, 0.2) == MAX_APR
    assert annualize(-1.0, 1.0) == -1.0
    assert annualize(0.1, 365.0) == pytest.approx(0.1)
    assert annualize(-0.5, 1.0) == pytest.approx(-1.0, abs=1e-9)


def test_evaluate_never_crashes_on_extreme_books():
    import json, random
    from backend.betting.profiles import PROFILES
    rng = random.Random(7)
    for _ in range(3000):
        mid = rng.choice([0.001, 0.01, 0.5, 0.99, rng.uniform(0.001, 0.999)])
        asks = sorted((round(min(0.999, max(0.001, mid + rng.uniform(-0.05, 0.2))), 3), rng.choice([1e-3, 5, 1e5]))
                      for _ in range(rng.randint(0, 5)))
        ev = evaluate(signal=rng.choice(["BUY_YES", "BUY_NO", "HOLD"]), p_yes=rng.uniform(0, 1), sigma=rng.uniform(0, 0.2),
                      quote=Quote(asks=asks, mid=mid, fee_bps=rng.choice([0, 1000]), min_order_shares=5, source="book"),
                      days=rng.choice([1.0, 30.0, 2000.0]), profile=get_profile(rng.choice(list(PROFILES))),
                      equity=rng.choice([0.0, 1000.0]), available_cash=500.0, exposure=Exposure(0, 0, 0, 0),
                      liquidity=rng.choice([0.0, 1e6]), risk_free_rate=0.045)
        json.dumps(ev.as_dict(), allow_nan=False)  # the API response must be valid JSON
