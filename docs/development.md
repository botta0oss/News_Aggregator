# Development and tests

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/sviluppo.md)</sub>

Tests, CI and code structure.

Tests need PostgreSQL with pgvector. TypeSafe and Polymarket are mocked at the HTTP level, so
no keys or network are needed.

```bash
pip install -r requirements.txt -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/newsagg
pytest
```

Tests do not use the embedding model, so `sentence-transformers` (and PyTorch) can be skipped
if you only need to run the tests.

**CI.** On GitHub every push to `main` and every pull request start
`.github/workflows/tests.yml`:
- **backend:** all the tests, on Postgres with pgvector (`pgvector/pgvector:pg16`) and without
  PyTorch;
- **frontend:** syntax check of the JavaScript modules with `node --check`.

In CI an unreachable database fails the tests instead of skipping them.

⚠️ The end-to-end test **drops and recreates every table** of the database given by
`TEST_DATABASE_URL`: always use a dedicated database.

| File | Contents |
|---|---|
| `tests/test_pipeline.py` | Composite score, heuristic, Jev evaluator, summaries |
| `tests/test_forecast.py` | Platt calibration, log-odds pooling (multi-outcome too), Kelly, signals, Brier score |
| `tests/test_polymarket.py` | Parsing and filtering of Gamma markets, final outcome (UMA oracle) and 50-50 |
| `tests/test_dedup.py` | Same story from different outlets grouped, confirmations counted per outlet |
| `tests/test_reembed.py` | Change of embedding model (vectors recomputed), historical outlet reliability |
| `tests/test_markets_e2e.py` | Full flow: collection → markets → forecast → API → resolution |
| `tests/test_auth.py` | Passwords, cookies, CSRF, roles, attempt limit, expiry, sign-out, security headers |
| `tests/test_sources.py` | Fetcher (HTML cleaning, redirects, internal network blocking, limits), catalogue, migrations, sources API |
| `tests/test_search.py` | Search on title, text and summary, prefixes, syntax, highlights, filters, index use |
| `tests/test_economics.py` | Book, `p × (1 − p)` fees, maximum price net of the fee, estimated book in levels, Kelly on the book, uncertainty, annualisation, verdicts and preset limits |
| `tests/test_markets_sort.py` | Market sorting on every field and direction, missing values last, stable pagination |
| `tests/test_ratelimit.py` | Limiter (spacing, pause, concurrency), Groq under rate limit and with reasoning models, queue that resumes, 429 on manual forecasts, files without cache |
| `tests/test_portfolio.py` | Automatic bets, exclusions, closing (50-50 too), automatic and manual selling, plan in the API, fees per category, closing price (CLV), profit and loss, curve, API and permissions |
| `tests/test_strategy.py` | Prices at which to buy and sell, actions (buy, wait, avoid, hold, sell), reasons, confidence |
| `tests/test_matching.py` | Key terms, synonyms, match, evidence ranking, targeted Google News search |
| `tests/test_multi.py` | Multi-outcome events: sync, distribution, YES/NO signal, economics, alerts, arbitrage |
| `tests/test_alerts.py` | Detection, immediate assessment, Telegram, quiet hours, prices after the alert, settings and permissions |
| `tests/test_backtest.py` | Planning, news without hindsight (archive too), metrics, bootstrap, parameters verified on recent markets |
| `tests/test_bulk_predict.py` | «Assess all with Jev»: full flow, waiting on rate limits, stop after repeated errors |
| `tests/test_usage.py` | Call log, estimated costs, daily limits and warnings |
| `tests/test_evidence.py` | No long shots, base rate asked to Jev, evidence from the facts (lower of the two), closing-line guard (category, pause, resume) |
| `tests/test_orders.py` | Second opinion (answer parsing, fallback, agreement, block), maker limit orders (price, fill, expiry, replacement, reserved cash, API) |
| `tests/test_shadow.py` | Shadow bets per filter (economic filters, objective evidence, guard), settlement and report, second-opinion scorecard, maker fills from the price history |
| `tests/test_i18n.py` | English and Italian texts: request language (`X-Lang`), `APP_LANGUAGE`, plans in English, every dashboard text translated |

The tests run with `APP_LANGUAGE=it` (set in `tests/conftest.py`), so most of them check the
Italian texts; `tests/test_i18n.py` covers English.

## Translations

- **Dashboard.** Every visible text goes through `t()` from `frontend/i18n.js`, written in
  Italian: `t("Prezzo {0}", value)`. `frontend/i18n-en.js` maps each Italian text to English,
  with the same `{0}` placeholders. A missing text falls back to Italian and is collected in
  `window.__i18nMissing`; `tests/test_i18n.py` fails if a `t("…")` has no translation.
- **Server.** Texts for people are written as `tr(italian, english)` (`backend/i18n.py`). The
  language comes from the request's `X-Lang` header, or `APP_LANGUAGE` for background work
  (alerts, summaries, stored notes); `dec()` and `dollars()` format numbers accordingly.

## Project structure

```
backend/
├── main.py                 # FastAPI app, startup and shutdown, request language
├── config.py               # settings from .env
├── i18n.py                 # language of the texts (X-Lang, APP_LANGUAGE), tr(it, en)
├── overrides.py            # parameters changed from the dashboard (e.g. after a backtest)
├── auth/                   # passwords, sessions, roles, attempt limit, user CLI
├── ai/
│   ├── jev.py              # shared TypeSafe client
│   ├── typesafe_evaluator.py  # news classification and scores
│   ├── summarizer.py       # Gemini / Groq / Ollama summaries
│   ├── ratelimit.py        # rate limits per service
│   └── usage.py            # usage and cost of calls, daily limits
├── ingestor/
│   ├── fetcher.py          # safe download and cleaning of RSS/Atom feeds
│   ├── sources.py          # catalogue (feeds.yaml) and source validation
│   ├── deduplicator.py     # URL hash and embeddings
│   ├── reembed.py          # recomputing vectors when the model changes
│   └── scheduler.py        # periodic pipeline and reclassification
├── markets/
│   ├── polymarket.py       # Gamma and CLOB client (read only)
│   ├── service.py          # sync, news linking, Jev forecasts
│   ├── matching.py         # key terms, match and evidence ranking
│   ├── targeted.py         # targeted Google News search per market
│   ├── forecast.py         # Platt calibration, log-odds pooling, edge, Kelly, Brier
│   ├── calibration.py      # results on resolved markets and closing price of signals
│   └── bulk.py             # «Assess all with Jev» in the background
├── betting/
│   ├── profiles.py         # risk presets
│   ├── fees.py             # Polymarket fees per category
│   ├── economics.py        # economic assessment (pure functions)
│   ├── strategy.py         # what to do: action, orders, prices, reasons (pure functions)
│   ├── plans.py            # data for the strategy (position, news, book)
│   ├── clv.py              # closing line value
│   └── portfolio.py        # simulated portfolio: buys, sells, closing
├── multi/
│   ├── service.py          # multi-outcome events: sync, news, distribution forecast
│   └── arbitrage.py        # sure gain buying every YES or every NO
├── backtest/
│   ├── engine.py           # rebuilding the past: historical price, news of the time, Jev
│   └── analysis.py         # metrics, intervals, suggested parameters and their check
├── alerts/
│   ├── service.py          # detection, immediate assessment, prices after the alert
│   └── telegram.py         # Telegram notifications
├── db/                     # SQLAlchemy models, queries and idempotent migrations
└── api/                    # FastAPI schemas and routes
frontend/                   # dashboard with no build step: app.js, ui.js, charts.js, explain.js,
                            # economics.js, strategy.js, i18n.js + i18n-en.js, views/ (one per page)
docs/                       # documentation (English; Italian in docs/it) and screenshots
scripts/
├── check_env.py            # checks keys and database
└── update.sh               # update on the server
Dockerfile · Dockerfile.cuda · docker-compose.yml · docker-compose.gpu.yml
feeds.yaml                  # catalogue of recommended sources
```
