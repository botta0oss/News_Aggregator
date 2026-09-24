import json
import httpx
from backend.markets import polymarket


def gamma_market(id_, question, yes="0.35", volume=50000, closed=False, outcomes='["Yes", "No"]'):
    return {
        "id": str(id_), "question": question, "slug": f"m-{id_}", "description": "Resolves YES if ...",
        "endDate": "2026-12-31T12:00:00Z", "outcomes": outcomes,
        "outcomePrices": json.dumps([yes, str(round(1 - float(yes), 4))]),
        "volumeNum": volume, "liquidityNum": 1000, "active": True, "closed": closed,
        "events": [{"slug": f"event-{id_}"}],
    }


def test_parse_binary_market():
    m = polymarket.parse_market(gamma_market(1, "Will X happen?"))
    assert m.yes_price == 0.35 and m.volume == 50000 and m.resolved_yes is None
    assert m.end_date.year == 2026
    assert m.url == "https://polymarket.com/event/event-1"


def test_parse_skips_non_binary_and_detects_resolution():
    assert polymarket.parse_market(gamma_market(2, "Who wins?", outcomes='["A", "B", "C"]')) is None
    assert polymarket.parse_market(gamma_market(3, "Q?", yes="1", closed=True)).resolved_yes is True
    assert polymarket.parse_market(gamma_market(4, "Q?", yes="0", closed=True)).resolved_yes is False


async def test_fetch_active_markets_filters_volume():
    def handler(request: httpx.Request):
        assert request.url.path == "/markets"
        assert request.url.params["closed"] == "false"
        return httpx.Response(200, json=[
            gamma_market(1, "Liquid?", volume=50000),
            gamma_market(2, "Illiquid?", volume=10),
            gamma_market(3, "Multi?", outcomes='["A", "B", "C"]'),
        ])

    async with httpx.AsyncClient(base_url="https://gamma.test", transport=httpx.MockTransport(handler)) as client:
        markets = await polymarket.fetch_active_markets(limit=10, min_volume=1000, client=client)
    assert [m.id for m in markets] == ["1"]


def test_parse_trading_fields_and_order_book():
    raw = gamma_market(9, "Q?")
    raw.update({"clobTokenIds": '["111", "222"]', "bestBid": 0.34, "bestAsk": "0.36", "takerBaseFee": 0, "orderMinSize": 5})
    m = polymarket.parse_market(raw)
    assert (m.yes_token_id, m.no_token_id, m.best_bid, m.best_ask, m.order_min_size) == ("111", "222", 0.34, 0.36, 5)
    book = polymarket.parse_order_book({
        "bids": [{"price": "0.30", "size": "10"}, {"price": "0.34", "size": "5"}],
        "asks": [{"price": "0.40", "size": "7"}, {"price": "0.36", "size": "3"}, {"price": "1.5", "size": "1"}],
    })
    assert book.asks == [(0.36, 3.0), (0.40, 7.0)] and book.best_bid == 0.34


async def test_fetch_order_book():
    def handler(request: httpx.Request):
        assert request.url.path == "/book" and request.url.params["token_id"] == "111"
        return httpx.Response(200, json={"asks": [{"price": "0.5", "size": "10"}], "bids": []})
    async with httpx.AsyncClient(base_url="https://clob.test", transport=httpx.MockTransport(handler)) as client:
        book = await polymarket.fetch_order_book("111", client=client)
    assert book.best_ask == 0.5
