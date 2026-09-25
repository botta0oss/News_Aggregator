# The method: from news to forecast

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/metodo.md)</sub>

How news is collected, linked to markets and turned into a probability estimate.

## The pipeline, step by step

| Step | Description |
|---|---|
| **Collection** | Reads the active sources every `INGEST_INTERVAL_MINUTES` (and right away at startup). Sources are managed from the dashboard; `feeds.yaml` is the catalogue of recommended sources. |
| **Deduplication** | L1: hash of the normalised URL. L2: near-identical title, or very similar title and text in the last 48 hours: the same story rewritten by several outlets ends up in a single group, with the number of different outlets reporting it. |
| **Summary** | Gemini → Groq → Ollama, falling back to an excerpt of the text. Written in `APP_LANGUAGE`. |
| **Classification** | Jev assigns category (8, aligned with Polymarket's topics), region, opinion or news, relevance to markets, clickbait, authority, depth and urgency. Without a key it uses weighted keywords. |
| **Search** | Full-text search on title, text and summary, with highlighting and filters. |
| **Markets** | Syncs, read only, Polymarket's most traded Yes/No markets and multi-outcome events. |
| **Targeted search** | For the most traded markets it searches Google News for news with the key terms of the question, even on topics the usual sources do not cover. |
| **Linking** | Associates each market with the recent news about the same subject: semantic similarity (pgvector) plus key terms (names, acronyms, numbers, synonyms). |
| **Evidence selection** | Jev reads the most useful news: relevant, from reliable sources, recent, one per story with the number of sources confirming it. |
| **Forecast** | Jev estimates the probability of YES from the market rules and the news, without seeing the price; the estimate is calibrated on markets already resolved. |
| **Signal** | Pools the estimate with the price (in log-odds, with a weight that grows with the strength of the news) and compares them: `BUY_YES`, `BUY_NO` or `HOLD`. |
| **Economic assessment** | Decides whether it is really worth it and how much to stake: real price from the book, fees, uncertainty of the estimate, annualised return, Kelly on the book and risk limits. |
| **Strategy** | For each market it says what to do (buy YES/NO, wait, avoid, hold, sell), with which limit orders, at what prices the decision would change, and why or why not. |
| **Simulated portfolio** | Every bet that is worth it becomes a virtual bet, sold when the plan says so or closed at resolution, to measure the results before using real money. |
| **Alerts** | When fresh, relevant news comes out for a market, Jev assesses it right away. If it is worth it a Telegram notification arrives; then the price after 15 minutes, 1, 6 and 24 hours is recorded to measure whether the alert got ahead of the market. |

## How news is linked to markets

1. **Candidates.** pgvector finds the recent news similar to the market question. It compares
   both the title and the title plus the start of the text, because many titles are vague.
2. **Key terms.** Names and acronyms (weight 2), distinctive words and numbers (weight 1), and
   years, months and days (weight 0.5) are extracted from the question. The most common
   synonyms count too: Fed = Federal Reserve = Powell, BTC = Bitcoin, 100k = 100,000 =
   $100,000. Short acronyms are compared case-sensitively, so «US» does not match «us».
3. **Match.** It computes `0.7 × similarity + 0.3 × terms found`. If the news does not mention
   any name in the question the match drops to 60 %: it is the same topic about another
   subject, for example news about the ECB for a market on the Fed. News with a match
   ≥ `MARKET_MATCH_THRESHOLD` is kept.
4. **Usefulness for Jev.** The usefulness of each news item is `match × source reliability ×
   freshness × Jev's judgement`.
   - Reliability depends on authority, clickbait and opinion pieces. The authority of a single
     article is a noisy estimate: for outlets with at least 5 classified articles, half of it
     is the outlet's average.
   - Freshness halves every `EVIDENCE_HALF_LIFE_HOURS`.
   - News that Jev has already judged not relevant for that market is removed.

   A **story** groups articles with a near-identical title (`SIMILARITY_THRESHOLD`) or with a
   very similar title + text in the last `STORY_WINDOW_HOURS` hours
   (`STORY_SIMILARITY_THRESHOLD`: outlets rewrite titles). Confirmations count different
   outlets, not articles: a source repeating the news does not make it more certain.
   Jev reads the `MARKET_MAX_ARTICLES` most useful: one per story, at most 3 per source. For each
   news item it gets the age, the type (news or opinion), the source reliability and the number
   of outlets that reported it, plus the days left before the market's end date.
5. **Targeted search.** On every run, for the `TARGETED_NEWS_MAX_MARKETS` most traded markets
   not searched in the last `TARGETED_NEWS_REFRESH_HOURS` hours, the key terms are searched on
   Google News. Results are saved as news of the automatic «Targeted search» source, with the
   name of the original outlet, and follow the same path: deduplication, classification,
   linking. It can also be run by hand from a market's detail page («Search news»). If the
   service does not answer for 3 markets in a row, the search stops until the next run.

In a market's detail page each news item shows the match, the key terms found, the number of
sources confirming it and whether it comes from the targeted search.

## How a forecast is made

### 1. Questions to Jev

Each market gets **a single** `system_one` call. The state contains the market question, the
resolution rules, the end date, today's date and the linked news. **The market price is not
passed**, so Jev's estimate stays independent and comparable with the price.

| Question | Primitive | Use |
|---|---|---|
| `base_rate` | `Noul` | Outside view: how often events of this kind happen in a similar time frame, before reading the news |
| `resolves_yes` | `Noul` | Probability that the market resolves YES, starting from the base rate and moving away from it only as far as the news justifies |
| `evidence_strength` | `Score` (0–4) | How much the news really informs the outcome |
| `relevant_nX` | `Noul` | Is news item X relevant to the outcome? |
| `impact_nX` | `Choice` | News item X raises, lowers or does not change the probability of YES |

**Outside view first.** Forecasters who start from how often similar events happen (the base
rate) and then adjust for the specific case are better calibrated than those who start from
the story. Jev is asked for the base rate first; the base rate is saved with the forecast and
shown in the explanation.

**Evidence strength: the lower of two ratings.** Jev's rating (0–4 → 0–1) is compared with
one computed from the facts: every linked news item weighs its outlet's reliability × its
freshness × the relevance Jev gave it, more if other outlets confirm the same story (up to 3)
and 1.5× if it comes from a primary source (Fed, ECB, BLS, SEC, courts, official
announcements…). The total `T` becomes `T / (T + EVIDENCE_OBJECTIVE_HALF)`: with the default
1.5 about three fresh items from reliable outlets, or one confirmed primary source, are
needed to reach 0.5. The forecast uses the **lower** of the two ratings, so a single article
cannot count as strong evidence because Jev says so. Both ratings are saved.

### 2. From estimate to signal

Liquid markets are usually already well calibrated, so Jev's estimate is not used as it is:

1. **Calibration (Platt scaling).** `logit P_cal = JEV_CALIB_A + JEV_CALIB_B × logit P_jev`,
   with `logit p = ln(p / (1 − p))`. The two numbers are estimated by the backtest on
   resolved markets: `B < 1` softens an overconfident Jev, `B > 1` sharpens one that is too
   cautious. By default (0 and 1) the estimate stays as it is.
2. **Pooling with the price in log-odds**, with a weight that grows with the evidence strength
   and shrinks when Jev is very far from the price:

```
d              = |logit(P_cal) − logit(price)|
w              = MODEL_WEIGHT_MAX × evidence_strength × min(1, MODEL_DISAGREEMENT_LOGIT / d)
logit(blended) = w × logit(P_cal) + (1 − w) × logit(price)
edge           = blended − price
```

`MODEL_WEIGHT_MAX` is 0.25 and `MODEL_DISAGREEMENT_LOGIT` 2. A liquid market that disagrees
with Jev by more than 2 log-odds (for example 80 % against 35 %, or 66 % against 5.5 %) is more
often right than Jev: without the reduction the biggest disagreements, the likeliest Jev errors,
would become the biggest edges and the first bets. The first real portfolio lost money exactly
there (see [strategy](strategy.md#what-went-wrong-in-the-first-portfolio)).

Averaging in log-odds is the standard way to combine calibrated forecasts: a simple average
(`BLEND_METHOD=linear`, the earlier method) makes them systematically too timid. With
`w = 0` the blend is the price, with `w = 1` it is Jev.

With `JEV_SAMPLES > 1` each forecast asks Jev several times and averages the answers (in
log-odds): less noise, but every call is paid.

- `edge ≥ MIN_EDGE` and evidence ≥ `MIN_EVIDENCE` → **BUY_YES**
- `edge ≤ −MIN_EDGE` and evidence ≥ `MIN_EVIDENCE` → **BUY_NO**
- otherwise → **HOLD**

The suggested stake is fractional Kelly: for YES `(blended − price) / (1 − price) × KELLY_FRACTION`,
for NO the symmetric formula on the NO price.

### Example

| | Value |
|---|---|
| Market price (YES) | 0.35 |
| Jev estimate | 0.80 |
| Evidence strength | 3/4 → 0.75 (the facts rate it at least as high) |
| Distance `d` | \|logit 0.80 − logit 0.35\| = 2.005 → reduction 2 / 2.005 = 0.997 |
| Weight `w` | 0.25 × 0.75 × 0.997 ≈ 0.187 |
| Blended probability | logit⁻¹(0.187 × logit 0.80 + 0.813 × logit 0.35) ≈ **0.439** |
| Edge | **+0.089** → `BUY_YES` |
| Stake (simple Kelly) | (0.439 − 0.35) / 0.65 × 0.25 ≈ **3.4 % of bankroll** |

The actual stake is then decided by the [economic assessment](strategy.md#economic-assessment-and-simulated-portfolio), which accounts for the real price, costs, uncertainty, time and limits.

## Which markets are left out

- **Markets decided by an asset's price** («Will Bitcoin be above $84,000 on September 24?»,
  «Will ETH reach $2,800 this week?», «Will gold hit $3,000?», «Bitcoin Up or Down»): Jev reads
  news, not the live price and its volatility, while the market price already reflects both.
  They are recognised by the question (an asset, a price level and a threshold such as above,
  below, between, reach, dip, hit: `backend/markets/kinds.py`). With `EXCLUDE_PRICE_MARKETS=true`
  (the default) they get no bets, and automatic forecasts, «Assess all» and alerts skip them, so
  no Jev call is spent there. A forecast asked by hand still runs.
- **Markets close to the end**: below the preset's minimum hours (72 / 24 / 12) no bet.

## Multi-outcome markets

Many of the most traded events on Polymarket have several possible answers, only one of which
wins: «Who will win the election?», «Who will win the Champions League?». On Polymarket each
outcome is a separate YES/NO share; here they are kept together and separate from the Yes/No
markets, in the **Multi-outcome** section, because they read as a distribution.

- **Sync.** The most traded open events come in (Gamma `/events`, `negRisk`, at least 3
  outcomes; `MULTI_SYNC_LIMIT`). The outcomes of these events no longer show up among the
  Yes/No markets, the opportunities and the alerts. The winner is detected when the event
  closes.
- **News.** Linking works as for Yes/No markets, on the event title. News naming one of the
  outcomes (a candidate, a team) gets a bonus.
- **Forecast.** With a single call Jev gives the probability of each outcome (a multiple-choice
  question), without seeing the prices.
  - It gets the `MULTI_MAX_OUTCOMES` most likely outcomes (12 by default); the others are
    summed into «Other outcomes».
  - Prices are normalised to 100 %, so the market's margin disappears from the reference.
  - The blend combines the two distributions log-linearly (`p ∝ Jev^w × market^(1−w)`, then
    normalised), with the same weight as Yes/No markets.
  - The edge is measured on the **real price** of the YES share, the one you pay: normalising
    removes the margin from the reference, not from the cost.
  - The signal points to the outcome furthest from its price, if the edge exceeds `MIN_EDGE`
    and the evidence `MIN_EVIDENCE`: **buy YES** if it is undervalued, **buy NO** if it is
    overvalued (often a favourite the market is too optimistic about).
- **Arbitrage.** Only one outcome wins, so a YES on every outcome always pays $1 and a NO on
  every outcome always pays $N − 1. If at the best book price buying the whole set costs less,
  fees included, the event shows the «Arbitrage» badge with the gain per set. Only the first
  level of the book is known: the quantity can be small and the price change quickly.
- **View.** In the list, for each event, the top outcomes with the price bar and the markers
  of Jev (diamond) and blend (circle). In the detail page: all outcomes, the table, the news
  (with the outcome each one favours) and the history.

**Economics, portfolio, alerts and backtest.** Everything that holds for Yes/No markets holds
for outcomes:
- **Economic assessment.** After each forecast the 3 outcomes furthest from their price are
  assessed, on the YES share if undervalued and on the NO share if overvalued: real book price,
  fees, uncertainty, annualised return, Kelly and the preset limits. In the event's detail page
  the «Worth it?» card lets you choose the outcome.
- **Simulated portfolio.** The best outcome becomes a simulated bet, if it is worth it. The
  per-event limit applies to all outcomes together, so you do not bet on three candidates of
  the same election beyond the preset's risk. Exclusions per event and category and closing at
  resolution work as for Yes/No markets.
- **Opportunities.** Events with an under- or overvalued outcome show up in a separate block,
  below the Yes/No markets.
- **Alerts.** Fresh, relevant news linked to an event makes the distribution be recomputed
  right away (one call). If it is worth it a «Buy YES on …» (or «Buy NO on …») notification
  arrives and the following price is measured as for Yes/No markets.
- **Backtest.** Choose «Multi-outcome» among the market types. For each resolved event the
  historical prices are rebuilt and the outcomes shown to Jev are the most likely according to
  the price of the time. The result reports:
  - the multi-outcome Brier (0 = perfect, 2 = certain and wrong);
  - the probability given to the winner;
  - how often the favourite won, for Jev and for the market;
  - the simulated bets.

Technically each outcome is also a row of the markets table, marked with `multi_event_id` and
hidden from the Yes/No lists: book, economic assessment, bets and closing use the same code.
