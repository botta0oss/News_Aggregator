# Configuration

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/configurazione.md)</sub>

Every variable of the `.env` file.

All variables are set in `.env`. Placeholder values `your_...` count as "not configured".

<details>
<summary><b>Docker compose</b></summary>

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | `password` | Database password; choose it before the first start (letters and digits only) |
| `COMPOSE_PROFILES` | – | Optional services: `tunnel` (Cloudflare Tunnel), `backup` (daily dump), also together: `tunnel,backup` |
| `CLOUDFLARE_TUNNEL_TOKEN` | – | Token of the tunnel created on Cloudflare |
| `BACKUP_KEEP_DAYS` | `14` | Days of backups kept in `./backups` |
| `API_BIND` | `127.0.0.1` | Address port 8000 answers on: this machine only, or `0.0.0.0` for the home network (see [Deploy](deploy.md#local-use-and-home-network)) |

</details>

<details>
<summary><b>Database and AI</b></summary>

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/postgres` | Database connection |
| `SIMILARITY_THRESHOLD` | `0.92` | Similarity above which two titles are the same news |
| `STORY_SIMILARITY_THRESHOLD` | `0.82` | Title + text similarity above which two articles tell the same story (titles rewritten by different outlets) |
| `STORY_WINDOW_HOURS` | `48` | How far back the same story is looked for |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Embedding model (384 dimensions, multilingual: Italian news finds the market written in English). If you change it, at startup the stored vectors are recomputed in the background, newest news first |
| `TYPESAFE_API_KEY` | – | TypeSafe key. Without a key: no forecasts, heuristic classification |
| `TYPESAFE_MODEL` | `jev-latest` | Jev model |
| `SUMMARIZER_PROVIDER` | `auto` | `gemini`, `groq`, `ollama` or `auto` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | Google AI Studio |
| `GROQ_API_KEY` / `GROQ_MODEL` | – / `llama-3.1-8b-instant` | Groq Cloud |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:1.5b` | Local Ollama |

</details>

<details>
<summary><b>Polymarket and forecasts</b></summary>

| Variable | Default | Description |
|---|---|---|
| `POLYMARKET_ENABLED` | `true` | Turns market sync and linking on |
| `POLYMARKET_SYNC_LIMIT` | `200` | Maximum number of open markets followed |
| `POLYMARKET_MIN_VOLUME` | `10000` | Minimum volume (USD): excludes illiquid markets |
| `MARKET_MATCH_THRESHOLD` | `0.5` | Minimum news ↔ market match (meaning + key terms), 0–1 |
| `MARKET_CANDIDATE_MARGIN` | `0.15` | Candidates: semantic similarity ≥ threshold − margin |
| `MARKET_MATCH_TERM_WEIGHT` | `0.3` | Weight of the key terms in the match |
| `MARKET_MATCH_NO_ENTITY_PENALTY` | `0.6` | Multiplier when the news does not mention any name in the question |
| `MARKET_NEWS_WINDOW_HOURS` | `168` | Only news from the last N hours (7 days: for many markets a week of context matters) |
| `MARKET_MAX_ARTICLES` | `8` | News items passed to Jev for each forecast |
| `EVIDENCE_HALF_LIFE_HOURS` | `48` | A news item's weight halves every N hours (minimum 25 %) |
| `EVIDENCE_MIN_JEV_RELEVANCE` | `0.25` | News that Jev judged less relevant than this for a market is no longer passed to it |
| `TARGETED_NEWS_ENABLED` | `true` | Targeted Google News search for each market |
| `TARGETED_NEWS_MAX_MARKETS` | `25` | Markets searched on each run (the most traded) |
| `TARGETED_NEWS_REFRESH_HOURS` | `6` | How often to repeat the search for the same market |
| `TARGETED_NEWS_MAX_RESULTS` | `10` | News items kept per search |
| `TARGETED_NEWS_DAYS` | `7` | Only results from the last N days |
| `TARGETED_NEWS_LOCALE` | `hl=en-US&gl=US&ceid=US:en` | Language and country of the results |
| `PREDICTION_AUTO` | `false` | Automatic forecasts after every collection (each forecast is a paid call) |
| `PREDICTION_MAX_PER_RUN` | `10` | Cap on automatic forecasts per run |
| `MODEL_WEIGHT_MAX` | `0.25` | Jev's maximum weight against the market price |
| `MODEL_DISAGREEMENT_LOGIT` | `2.0` | Jev's weight shrinks when it is further than this from the price, in log-odds (`0` = never) |
| `FORECAST_MAX_AGE_HOURS` | `6` | A forecast older than this needs a new one before buying |
| `FORECAST_MAX_PRICE_MOVE` | `0.5` | Same if the YES price has moved more than this since the forecast, in log-odds (≈ 12 points at 50 %, 4 at 90 %) |
| `EXCLUDE_PRICE_MARKETS` | `true` | Markets decided by an asset's price get no bets and no automatic, bulk or alert forecasts |
| `BLEND_METHOD` | `logodds` | How Jev and price are combined: `logodds` or `linear` |
| `JEV_CALIB_A` / `JEV_CALIB_B` | `0` / `1` | Platt calibration of Jev's estimate (estimated by the backtest) |
| `JEV_SAMPLES` | `1` | Jev calls per forecast, averaged (each one is paid) |
| `MIN_EDGE` | `0.05` | Minimum edge to issue a signal |
| `MIN_EVIDENCE` | `0.5` | Minimum evidence strength to issue a signal |
| `KELLY_FRACTION` | `0.25` | Fraction of the Kelly criterion used for the stake |

</details>

<details>
<summary><b>Server</b></summary>

| Variable | Default | Description |
|---|---|---|
| `APP_LANGUAGE` | `en` | Language of the texts written without a dashboard request: Telegram alerts, news summaries, stored notes. `en` or `it`. The dashboard has its own IT/EN button |
| `INGEST_INTERVAL_MINUTES` | `60` | Pipeline interval |
| `ALERT_SCAN_MINUTES` | `10` | Quick check for alerts: sources → links → alerts, without summaries (`0` turns it off) |
| `ALERT_FOLLOWUP_MINUTES` | `5` | How often to record the price after alerts |
| `ALERT_TIMEZONE` | `Europe/Rome` | Time zone of the quiet hours |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | – | Bot and chat that receive the notifications |
| `PUBLIC_URL` | – | Dashboard address, for the link in notifications (e.g. `https://news.example.com`) |
| `AI_BATCH_SIZE` | `25` | News items summarised and classified on each run |
| `FEED_TIMEOUT_SECONDS` | `20` | Maximum time to download a feed |
| `FEED_MAX_BYTES` | `5000000` | Maximum size of a feed |
| `ALLOW_PRIVATE_FEEDS` | `false` | Allows feeds on internal addresses (see below) |
| `CORS_ORIGINS` | – | Allowed external origins, comma separated. The dashboard does not need any |
| `API_DOCS_ENABLED` | `true` | Publishes `/docs` and `/openapi.json`. Turn it off in production |

</details>

<details>
<summary><b>Economic assessment and simulated portfolio</b></summary>

| Variable | Default | Description |
|---|---|---|
| `POLYMARKET_CLOB_URL` | `https://clob.polymarket.com` | Public book API (read only) |
| `RISK_FREE_RATE` | `0.04` | Annual return of the risk-free alternative, the basis of the return threshold |
| `DEFAULT_FEE_BPS` | `500` | Fee rate (basis points, applied to `p × (1 − p)`) for unknown categories |
| `DEFAULT_SPREAD` | `0.02` | Spread assumed when the book is not available |
| `MODEL_PSEUDO_COUNT` | `20` | How many "observations" a Jev estimate with full evidence is worth (sets the uncertainty) |
| `PAPER_BANKROLL` | `1000` | Starting simulated capital (editable from the dashboard) |
| `PAPER_PRESET` | `bilanciato` | Starting preset: `prudente` (prudent), `bilanciato` (balanced), `aggressivo` (aggressive) |

</details>

<details>
<summary><b>AI API limits</b></summary>

Each provider has a client-side limiter: it spaces requests out, caps concurrent ones and, when
the API answers 429, pauses that provider for the time given by `Retry-After`. Set the values
of your plan (you find them in the Groq, Google AI Studio and TypeSafe consoles).

| Variable | Default | Description |
|---|---|---|
| `GROQ_RPM` | `20` | Requests per minute to Groq |
| `GEMINI_RPM` | `10` | Requests per minute to Gemini |
| `JEV_RPM` | `30` | Requests per minute to TypeSafe Jev |
| `JEV_CONCURRENCY` | `2` | Concurrent Jev calls |
| `OLLAMA_CONCURRENCY` | `1` | Concurrent requests to local Ollama |
| `AI_MAX_WAIT_SECONDS` | `90` | Maximum wait of a background job for its turn |

- **Jev pause:** if Jev is paused, classification stops and the remaining news is picked up
  on the next run (it is not downgraded to the heuristic). A manual forecast from the dashboard
  waits at most 10 seconds, then answers "try again in N seconds".
- **Groq or Gemini pause:** it moves to the next provider; with no provider available the
  summary is an excerpt of the text.
- **Reasoning models:** with Groq's reasoning models (`openai/gpt-oss-20b`,
  `openai/gpt-oss-120b`) the app asks for short reasoning (`reasoning_effort=low`) and limits
  the answer tokens, so as not to exhaust the tokens-per-minute limit.

</details>

<details>
<summary><b>Access</b></summary>

| Variable | Default | Description |
|---|---|---|
| `SESSION_TTL_HOURS` | `168` | Maximum length of a session (7 days) |
| `SESSION_IDLE_MINUTES` | `720` | Closed after inactivity (12 hours) |
| `SESSION_COOKIE_SECURE` | `auto` | `auto` = `Secure` except on localhost over HTTP; or `true` / `false`. To open the dashboard over HTTP from another device at home you need `false` |
| `CLIENT_IP_HEADER` | – | Header with the visitor's real IP set by a trusted proxy in front of the app (`CF-Connecting-IP` behind Cloudflare Tunnel). Use it only if the app is reachable exclusively through that proxy |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed attempts per IP and username before lockout |
| `LOGIN_MAX_ATTEMPTS_PER_IP` | `20` | Failed attempts per IP, whatever the username |
| `LOGIN_WINDOW_MINUTES` | `15` | Window and length of the lockout |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | – | Admin created at the first start if there are no users |

</details>

**Sources.** They are managed from *Settings → Sources*. At the first start, with the sources
table empty, the `feeds.yaml` entries with `active: true` are added; the others show up among
the recommended sources. Each source can have a main topic, used as a hint by the classifier.
For safety the server refuses feeds pointing to internal networks (localhost, 10.x,
192.168.x, cloud metadata), even after a redirect: otherwise whoever adds a source could make
the server query the internal network. If you need an internal feed set
`ALLOW_PRIVATE_FEEDS=true`.

The catalogue contains 85 sources, grouped by the topics traded on Polymarket:
- world affairs and conflicts;
- US and European politics, including polls, the Supreme Court and elections;
- economy, with primary sources such as the Fed, ECB, Bank of England, BLS, BEA and SEC;
- crypto;
- technology and AI, including the official announcements of OpenAI and Google;
- science, space, health and weather (NASA, WHO, NOAA for hurricanes);
- sport;
- culture and entertainment (awards, box office, music);
- Italian outlets.

In *Settings* you can add them all, or by category, with «Select all». Before activating many
new sources test the feeds with «Test the feed»: RSS addresses sometimes change.
