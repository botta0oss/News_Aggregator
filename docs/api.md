# API

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/api.md)</sub>

The app's REST endpoints.

Full interactive documentation on `/docs` (if `API_DOCS_ENABLED=true`). Every endpoint requires
a session; `POST` ones also the `X-CSRF-Token` header, and those that start jobs or paid calls
(`/ingest`, `/markets/sync`, `/markets/{id}/predict`, `/markets/predict-all`) the `admin` role.

**Language.** Send `X-Lang: en` or `X-Lang: it` to get the texts the server writes (plans,
reasons, error messages, preset labels) in that language; without it the server uses
`APP_LANGUAGE`. The dashboard sends it on every request.

### Access

| Method | Path | Description |
|---|---|---|
| POST | `/auth/login` | `{username, password}` → session cookie, user and CSRF token |
| POST | `/auth/logout` | Closes the current session |
| GET | `/auth/me` | Current user and CSRF token |
| POST | `/auth/password` | `{current_password, new_password}`; closes the other sessions |

### News

| Method | Path | Description |
|---|---|---|
| GET | `/articles` | News with search, filters and sorting |
| GET | `/articles/{id}` | Detail of a news item |
| GET | `/categories` | Number of news items per category |
| POST | `/ingest` | Starts the full pipeline right away (admin) |
| POST | `/ingest/reclassify` | Reclassifies the saved news (admin, `limit` up to 1000) |

`/articles` accepts:
- **search:** `q` (text), `scope` (`all` or `title`), `sort` (`relevance`, `score`, `recent`);
- **filters:** `category`, `region`, `source_id`, `since_hours`, `min_market_relevance`, `hide_opinion`;
- **pagination:** `limit`, `offset`;
- **score weights:** `w_authority`, `w_tech`, `w_urgency`, `w_clickbait`, with the thresholds `max_clickbait`, `min_authority`.

With `q` each news item also has `title_highlight` and `snippet`, with the words found wrapped
in the characters `\u0002` and `\u0003`.

```bash
curl -b cookie.txt "localhost:8000/articles?q=fed%20rate%20cut&since_hours=72&min_market_relevance=0.5"
```

### Sources

| Method | Path | Description |
|---|---|---|
| GET | `/sources` | Sources with the status of the latest update and number of news items |
| POST | `/sources` | Adds a source `{name, url, category_hint, active}` (admin) |
| PATCH | `/sources/{id}` | Changes name, address, topic or activation (admin) |
| DELETE | `/sources/{id}` | Deletes the source; if it has news `delete_articles=true` is needed (admin) |
| POST | `/sources/{id}/fetch` | Fetches this source's news right away (admin) |
| POST | `/sources/test` | Tests a feed without saving it: title, number of news items, samples (admin) |
| GET | `/sources/catalog` | Recommended sources from `feeds.yaml`, marking those already added (description in the request language) |
| POST | `/sources/catalog` | Adds sources from the catalogue `{urls: [...]}` (admin) |

### Markets and forecasts

| Method | Path | Description |
|---|---|---|
| POST | `/markets/sync` | Market sync + news linking (+ forecasts if `PREDICTION_AUTO`) |
| GET | `/markets` | Markets with their latest forecast. Filters: `q`, `only_linked`, `include_closed`. Sorting: `sort` = `volume`, `end_date`, `price`, `signal` (latest forecast), `edge`, `news`, `liquidity`, `question`; `order` = `asc`/`desc` (a sensible default for each field, missing values always last) |
| GET | `/markets/{id}` | Linked news (relevance and impact) and forecast history |
| POST | `/markets/{id}/predict` | Immediate Jev forecast (refreshes the price first) |
| POST | `/markets/{id}/search-news` | Targeted Google News search for this market; returns `{query, added, linked}` (admin) |
| POST | `/markets/predict-all` | Starts in the background the Jev forecast on every open market with recent news. `only_new=true`: only never assessed or with new news; `refresh_first=false`: skips the price update. `409` if already running (admin) |
| GET | `/markets/predict-all` | Progress (`total`, `done`, `skipped`, `failed`, `current`, `message`) and assessable markets `eligible: {all, new}` (admin) |
| POST | `/markets/predict-all/stop` | Stops after the current market (admin) |
| GET | `/predictions/opportunities` | Markets with the highest edge. Filters: `min_edge`, `min_evidence`, `include_hold` |
| GET | `/predictions/calibration` | Brier score on resolved markets |
| GET | `/status` | Configuration (Jev on, thresholds) and counters for the dashboard |
| GET | `/markets/{id}/economics` | Live economic assessment of the latest forecast and plan (`strategy`: action, orders, prices, reasons; optional `preset`) |
| POST | `/markets/{id}/paper-bet` | Adds the simulated bet right away, if it is worth it (admin) |

### Simulated portfolio

| Method | Path | Description |
|---|---|---|
| GET | `/portfolio` | Summary, capital curve, available presets |
| GET | `/portfolio/export` | The whole portfolio now: `format=xlsx` (summary, bets, equity, exclusions, columns) or `format=csv` (bets); `lang=it/en` for the descriptions |
| PUT | `/portfolio/settings` | `{preset, auto_paper, auto_sell}` (admin) |
| POST | `/portfolio/reset` | `{bankroll, preset}`: deletes the simulated bets and starts over (admin) |
| GET | `/portfolio/bets` | Bets: `status` = `open`, `settled`, `excluded`, `all`; open ones have their exit plan (`plan`) |
| POST | `/portfolio/bets/{id}/exclude` · `/include` | Excludes or readmits a bet (admin) |
| POST | `/portfolio/bets/{id}/sell` | Sells an open bet on the book right away (admin) |
| GET · POST | `/portfolio/exclusions` | Exclusions `{kind: market/event/category, value, label}` (POST admin) |
| DELETE | `/portfolio/exclusions/{id}` | Removes an exclusion (admin) |

### Backtest

| Method | Path | Description |
|---|---|---|
| GET · POST | `/backtest/runs` | List of backtests; start `{resolved_after, resolved_before, max_markets, min_volume, horizons, max_calls, exclude_decided, kinds}` (`kinds`: `binary` and/or `multi`) (POST admin, `409` if one is already running) |
| GET | `/backtest/runs/{id}` | Progress and summary (metrics, calibration, suggestions) |
| GET | `/backtest/runs/{id}/cases` | Cases: `status` = `ok`, `skipped`, `all` |
| POST | `/backtest/runs/{id}/stop` · DELETE `/backtest/runs/{id}` | Stops or deletes (admin) |
| GET · PUT | `/backtest/parameters` | `MODEL_WEIGHT_MAX`, `MIN_EDGE`, `JEV_CALIB_A`, `JEV_CALIB_B` in use; PUT overrides them (admin) |
| POST | `/backtest/parameters/reset` | Back to the `.env` values (admin) |

### Usage and costs

| Method | Path | Description |
|---|---|---|
| GET | `/usage` | Today against the limits, history per day, service and feature (`days`, max 90), limits and prices in use |
| PUT | `/usage/settings` | Daily limits and prices (admin) |
| POST | `/usage/settings/reset` | Back to the `.env` values (admin) |

### Multi-outcome markets

| Method | Path | Description |
|---|---|---|
| GET | `/multi/opportunities` | Events with an undervalued outcome (`min_edge`, `min_evidence`, `include_hold`), with the economic assessment |
| GET | `/multi` | Events with the top outcomes and the latest forecast. `q` (title or name of an outcome), `sort` = `volume`, `edge`, `signal`, `end_date`, `news` |
| GET | `/multi/{id}` | All outcomes, linked news, forecast history |
| POST | `/multi/{id}/predict` | Jev forecast of the distribution (admin; `422` without news) |

### Alerts

| Method | Path | Description |
|---|---|---|
| GET | `/alerts` | Alerts with the news, the prices after the alert and the favourable move. `kind` = `opportunities` (default) or `all` |
| GET | `/alerts/summary` | Results of the last `days` days: average move and favourable share for each interval, outcome of the resolved markets |
| GET · PUT | `/alerts/settings` | Settings (PUT admin) |
| POST | `/alerts/test-telegram` | Sends a test message (admin) |
| POST | `/alerts/run` | Checks the new news right away (admin) |

Error codes of `/markets/{id}/predict`: `503` TypeSafe key missing, `422` no linked news,
`409` market closed or without a price, `404` unknown market.

Example answer of `/predictions/opportunities` (illustrative values):

```json
[
  {
    "market": {
      "id": "123456",
      "question": "Will the Fed cut interest rates in December 2026?",
      "url": "https://polymarket.com/event/fed-decision-in-december",
      "yes_price": 0.35,
      "linked_articles": 4
    },
    "prediction": {
      "model_probability": 0.8,
      "evidence_strength": 0.75,
      "blended_probability": 0.5188,
      "edge": 0.1688,
      "signal": "BUY_YES",
      "kelly_fraction": 0.0649,
      "article_count": 4
    }
  }
]
```
