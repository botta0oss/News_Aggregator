"""Buy/sell strategy: price levels, actions, reasons."""
import pytest

from backend.betting.economics import Exposure, Quote, evaluate, model_sigma
from backend.betting.fees import fee_per_share
from backend.betting.profiles import get_profile
from backend.betting.strategy import Forecast, build_plan, buy_levels, hold_value, sell_above

PROFILE = get_profile("bilanciato")
HURDLE = 0.04 + PROFILE.min_apr_premium


def fc(model=0.8, evidence=0.75, weight=0.375):
    return Forecast(model=model, weight=weight, evidence=evidence, sigma=model_sigma(model, evidence, weight, 20))


def ev_at(f, price, signal, days=30, liquidity=1e6, fee_bps=400, equity=1000.0):
    p_yes = f.p_yes(price)
    side_price = price if signal != "BUY_NO" else 1 - price
    q = Quote(asks=[(round(side_price + 0.005, 4), 1e5)], mid=side_price, fee_bps=fee_bps, source="book")
    return evaluate(signal=signal, p_yes=p_yes, sigma=f.sigma, quote=q, days=days, profile=PROFILE, equity=equity,
                    available_cash=equity, exposure=Exposure(), liquidity=liquidity, risk_free_rate=0.04).as_dict()


def plan(f, price, signal, **kw):
    kw.setdefault("days", 30)
    ev = kw.pop("ev", None) or ev_at(f, price, signal, days=kw["days"], liquidity=kw.pop("liquidity", 1e6))
    return build_plan(ev=ev, fc=f, signal=signal, market_price=price, profile=PROFILE, fee_bps=400,
                      min_edge=0.05, risk_free=0.04, **kw)


def test_buy_level_is_where_buying_stops_being_worth_it():
    f = fc()
    levels = buy_levels(f, PROFILE, 400, 30, 0.05, HURDLE)
    q = levels["buy_yes_below"]
    assert 0.35 < q < 0.8 and levels["buy_no_above"] is None
    # Just below the level every check passes, just above one fails
    for price, ok in ((q, True), (q + 0.01, False)):
        p = f.p_yes(price)
        cost = price + fee_per_share(price, 400)
        passes = p - price >= 0.05 and (p - PROFILE.z * f.sigma) - cost >= PROFILE.min_net_edge
        assert passes == ok


def test_sell_level_is_where_cashing_in_beats_holding():
    f = fc()
    x = sell_above(f, "YES", 400, 30, HURDLE)
    assert x - fee_per_share(x, 400) >= hold_value(f, x, "YES", 30, HURDLE)
    assert (x - 0.005) - fee_per_share(x - 0.005, 400) < hold_value(f, x - 0.005, "YES", 30, HURDLE)
    # Around the estimate for a short horizon, lower when the money stays locked for long
    assert 0.75 <= x <= 0.85
    assert sell_above(f, "YES", 400, 300, HURDLE) < x
    # A NO position: sell when the NO price reaches the NO estimate
    assert sell_above(fc(model=0.2), "NO", 400, 30, HURDLE) == pytest.approx(x, abs=0.02)


def test_plan_buy_with_orders_and_reasons():
    f = fc()
    p = plan(f, 0.35, "BUY_YES", tally={"raises_yes": 2, "lowers_yes": 1, "neutral": 0,
                                       "raises_yes_titles": ["Fed signals cut"], "lowers_yes_titles": ["Inflation up"]})
    assert p.action == "BUY" and p.side == "YES" and p.title == "Compra SÌ"
    buy, sell = p.orders
    assert buy["type"] == "buy" and buy["limit"] > 0.35 and buy["shares"] > 0
    # The price scale and the order agree: never pay more than the evaluation's maximum price
    assert p.levels["yes_limit"] == pytest.approx(buy["limit"], abs=1e-3)
    assert sell["type"] == "sell" and sell["after_fill"] and sell["limit"] == p.levels["sell_above"]
    assert any("Fed signals cut" in r for r in p.pros) and any("Inflation up" in r for r in p.cons)
    assert any("probabilità" in r for r in p.cons)            # the chance of losing is always said
    assert p.exit and p.confidence in ("bassa", "media", "alta")


def test_plan_wait_when_the_price_is_too_high():
    f = fc()
    q = buy_levels(f, PROFILE, 400, 30, 0.05, HURDLE)["buy_yes_below"]
    price = q + 0.03   # still a signal, but costs and uncertainty eat the edge
    p = plan(f, price, "BUY_YES")
    assert p.action == "WAIT" and p.orders[0]["conditional"] and p.orders[0]["limit"] == q
    assert p.title.endswith("o meno") and "si mangiano il vantaggio" in p.summary


def test_levels_and_orders_account_for_the_spread():
    """Buying pays half a spread above the mid: the order limit is the ask, consistent with the
    evaluation made at the real ask (regression: "buy below 70.5¢" while the ask was 70¢)."""
    f = Forecast(model=0.12, weight=0.4, evidence=0.8, sigma=0.0315, method="linear")
    hs = 0.01
    levels = buy_levels(f, PROFILE, 400, 128, 0.05, HURDLE, hs)
    assert levels["no_limit"] == pytest.approx(1 - levels["buy_no_above"] + hs)
    # At a YES mid of 0.31 the NO ask is 0.70: the evaluation says no, the plan waits below 70¢
    p = build_plan(ev=ev_at(f, 0.30, "BUY_NO", days=128), fc=f, signal="BUY_NO", market_price=0.30, profile=PROFILE,
                   fee_bps=400, days=128, min_edge=0.05, risk_free=0.04, half_spread=hs)
    assert p.action in ("WAIT", "BUY")
    if p.action == "WAIT":
        assert p.orders[0]["limit"] < 0.705   # below the ask the evaluation rejected
    # Without the spread the level would be more generous
    assert buy_levels(f, PROFILE, 400, 128, 0.05, HURDLE)["buy_no_above"] <= levels["buy_no_above"]
    # Selling: the forecast is recomputed at the mid, half a spread above the bid that is sold into
    g = fc()
    x = sell_above(g, "YES", 400, 30, HURDLE, hs)
    assert x - fee_per_share(x, 400) >= hold_value(g, x + hs, "YES", 30, HURDLE)


def test_plan_avoid_for_structural_reasons():
    p = plan(fc(), 0.35, "BUY_YES", liquidity=100)
    assert p.action == "AVOID" and "liquido" in p.summary


def test_plan_without_signal_gives_the_prices_that_would_make_one():
    f = fc(model=0.55, evidence=0.9, weight=0.45)
    p = plan(f, 0.5, "HOLD")
    assert p.action == "NONE"
    assert p.levels["buy_yes_below"] is not None and p.levels["buy_yes_below"] < 0.5
    assert "SÌ se il prezzo scende sotto" in p.summary


def test_plan_hold_then_sell_a_position():
    f = fc()
    pos = {"side": "YES", "shares": 100, "avg_price": 0.36}
    hold = plan(f, 0.45, "BUY_YES", position=pos, sell_bid=0.44)
    assert hold.action == "HOLD" and hold.orders[0]["type"] == "sell"
    target = hold.levels["sell_above"]
    sell = plan(f, target, "BUY_YES", position=pos, sell_bid=target)
    assert sell.action == "SELL" and "raggiunto la stima" in sell.summary
    # The forecast turned against the position: sell whatever the price
    flipped = plan(fc(model=0.1), 0.45, "BUY_NO", position=pos, sell_bid=0.44)
    assert flipped.action == "SELL" and "girata" in flipped.summary


def test_closed_market():
    assert plan(fc(), 0.35, "BUY_YES", market_closed=True).action == "NONE"


def test_confidence_uses_the_track_record():
    good = {"resolved_markets": 80, "gain_blended": {"mean": 0.01, "lo": 0.002, "hi": 0.02}}
    bad = {"resolved_markets": 80, "gain_blended": {"mean": -0.02, "lo": -0.03, "hi": -0.01}}
    f = fc(evidence=0.9, weight=0.45)
    assert plan(f, 0.35, "BUY_YES", track=good).confidence == "alta"
    assert plan(f, 0.35, "BUY_YES", track=bad).confidence in ("bassa", "media")
    assert any("più accurato" in c for c in plan(f, 0.35, "BUY_YES", track=bad).cons)
