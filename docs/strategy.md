# Economic assessment, strategy and portfolio

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/strategia.md)</sub>

Whether a forecast is really worth it, what to do (buy, wait, sell) and how the simulated portfolio measures the results.

## Economic assessment and simulated portfolio

An edge on paper is not enough: the economic assessment (`backend/betting/`) decides whether a
forecast is really worth it, how much to stake and at what maximum price. It is computed after
every forecast and, live, in the **Worth it?** card of the market detail page.

0. **The forecast, at today's price.** The blend is recomputed at the current price (calibrated
   Jev pooled with the price of now), not taken from the forecast, which was pooled with the
   price of its time. A forecast older than `FORECAST_MAX_AGE_HOURS` (6), or whose price has
   moved more than `FORECAST_MAX_PRICE_MOVE` (0.5 in log-odds: about 12 points around 50 %,
   4 points around 90 %), needs a new one before buying. No bets on markets decided by an
   asset's price (see [the method](method.md#which-markets-are-left-out)), nor on markets
   ending in less than the preset's minimum hours: at that point the price already knows the
   outcome. No shares under `LONGSHOT_MIN_PRICE` (10¢), on either side: long shots win less
   often than their price says (the *favourite–longshot bias*), and an error of a few points
   on a 5¢ share is a large share of the stake. When a [second opinion](method.md#second-opinion)
   puts the probability on the other side of the price from Jev, no buying either.
1. **Real price.** It reads the book of the side to buy from Polymarket's CLOB (public API,
   read only) and computes the average price you would pay for that amount.
   Fee (only for those who take from the book, as here): `rate × price × (1 − price)` per
   share, highest at 50¢ and zero at the extremes. The rate depends on the category (World 0,
   Politics/Technology/Economy 4%, Sport 3%, Culture/Science 5%, Crypto 7%; unknown category:
   `DEFAULT_FEE_BPS`). The CLOB `/fee-rate` endpoint says whether the market has fees on: if it
   answers 0 the fee is zero. Without a book it estimates a 4-level book, from the mid price +
   half the spread upwards, one spread apart (40/30/20/10% of half the declared liquidity):
   buying a lot costs progressively more. It always says so.
2. **Prudent probability.** `p_prudent = p_blended − z × σ`, where
   `σ = w × √(p_jev (1 − p_jev) / (MODEL_PSEUDO_COUNT × evidence + 1))`. After 30 resolved
   markets σ is multiplied by `√(observed Brier / expected Brier)`, where the expected Brier is
   the one perfectly calibrated forecasts would have (the average of `p (1 − p)`): widened if
   the blended forecasts were overconfident (up to 2). It is narrowed (down to 0.75) only when
   the blend has also beaten the market price on the same markets, by two standard errors: a
   blend that is merely consistent with itself does not earn bigger stakes.
3. **Net margin.** `p_prudent − (price + fee)` must exceed the preset's threshold.
4. **Time and return.** The prudent expected return is annualised over the days left before the
   end date and must exceed `RISK_FREE_RATE` + the preset's premium. Short bets always pass that
   test (a few points in two days are a huge annual rate), so the prudent return of the bet
   itself must also reach the preset's minimum.
5. **How much to stake.** Kelly capital is computed on the book, because buying more worsens
   the price. A fraction of it is taken (depending on the preset) and then the limits per
   market, event, category, total invested, available cash and share of the book apply. The
   **maximum price** to pay is the price at which `price + fee` leaves exactly the minimum
   margin: `price + fee(price) = p_prudent − minimum margin`.
6. **Verdict.** *Worth it*, *Barely worth it* (the stake was cut to less than half by the
   limits) or *Not worth it*, always with the reasons.

| Preset | Kelly | z | Net margin | Min return | Annual premium | Max per market | Max per event | Max per category | Max invested | Min liquidity | End date |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Prudent | ×0.15 | 1.64 | 4 pts | 10% | 15% | 2% | 5% | 15% | 40% | $25,000 | 72 h – 120 d |
| Balanced | ×0.25 | 1.0 | 3 pts | 6% | 8% | 4% | 8% | 25% | 60% | $10,000 | 24 h – 365 d |
| Aggressive | ×0.5 | 0.5 | 2 pts | 3% | 3% | 8% | 15% | 40% | 85% | $5,000 | 12 h – 730 d |

**Simulated portfolio** (*Portfolio* page):
- **Automatic bets:** every forecast with a *Worth it* or *Barely worth it* verdict becomes a
  virtual bet, at most one open per market. With `PAPER_ORDER_MODE=maker` (the default) it is
  first a **limit order**, as a maker would place it:
  - price: one tick (`MAKER_TICK`, 1¢) above the side's best bid, below its best ask, never
    above the assessment's maximum price;
  - no fee (on Polymarket makers pay none) and no half spread;
  - it fills, at its price, when a market sync shows the side's best ask at or below it (a
    seller came down to it). Between two syncs it reads the CLOB's minute price history
    (`MAKER_FILL_FROM_HISTORY`): if the side traded **below** the limit while the order was
    waiting, it fills at the time of that trade (at the limit itself others may be ahead in
    the queue, so that does not count). Without history it waits for the next sync;
  - it expires after `MAKER_ORDER_TTL_HOURS` (6); a new forecast on the market replaces it
    (a new order follows if it still says buy); the guard's pause or an exclusion cancels it;
    a market that closes against the side fills it (it went through the price on its way down);
  - its money is reserved: it is not available cash and counts in the exposure limits.

  The cost is adverse selection: orders fill more often when the price is moving against the
  bet, and some never fill. The portfolio shows the pending orders, how many filled or expired,
  and what the filled ones saved against the book price when they were placed. Manual bets
  («Add now») still take the book (`entry` = taker) and cancel a pending order on the market.
  `PAPER_ORDER_MODE=taker` goes back to taking the ask on automatic bets too.
- **Shadow bets:** every automatic bet blocked **only** by filters is followed without money,
  as if it had been bought at the book price, until resolution (`SHADOW_BETS_ENABLED`). The
  filters measured: shares under the minimum price, second opinion disagreeing, markets on an
  asset's price, too close to the end, return of the bet too low, evidence from the facts
  weaker than Jev's rating, closing-line pause (also the categories it excluded). A bet also
  blocked by something else (illiquid market, margin after costs…) is not counted: the filter
  would not have changed anything. The portfolio card «What the filters blocked» shows per
  filter how many bets it blocked, won/lost, the hypothetical result, per dollar, and how the
  price moved after the block. Negative means the filter avoided losses; positive over many
  bets, that it is costing gains and could be loosened. `GET /portfolio/shadow` and the
  *Shadow* sheet of the export list them one by one.
- **Closing-line guard:** the result of a bet takes weeks, the price moves within hours. For
  the latest `CLV_GUARD_WINDOW` (15) bets older than an hour, the guard measures how much the
  price of the side bought has moved since the purchase (to the closing price once the market
  has closed). If the average is below `CLV_GUARD_MIN_AVG` (−2 points):
  - over at least `CLV_GUARD_CATEGORY_MIN_BETS` (5) bets of one category → the category is
    excluded from automatic bets (it shows up in the exclusions, and can be removed there);
  - over at least `CLV_GUARD_MIN_BETS` (8) bets → automatic bets pause, with a Telegram
    message. The portfolio page shows why; an admin resumes them with «Resume automatic
    bets», and the bets before the resume are not counted again.

  Manual bets are never blocked. A market that keeps moving against the signals means they are
  not ahead of it: betting on would only pay the spread.
- **Selling:** after every market update and every new forecast, open positions are reviewed
  with the [strategy](#buy-and-sell-strategy): if the plan says «Sell», the shares are sold on
  the book (status «sold», with price and reason). It can be turned off («Sell automatically»)
  or done by hand: «Sell now» asks for confirmation and offers to exclude the market from
  automatic buys (on by default), otherwise the next forecast that still says «worth it» would
  buy it back at a higher price. Sales at a profit count as won.
- **Closing:** happens when the market resolves for good (price at 1/0 and, if Gamma reports
  it, `umaResolutionStatus` = «resolved»: an outcome that is only proposed or disputed can still
  change). A winning share is worth $1; if the market closes 50-50 each share is worth $0.50
  and the bet counts as «voided» (outside the win rate).
- **What it shows:** current value, realised and unrealised profits, win rate, capital curve and a comparison between expected and actual profit.
  Open positions are valued at what selling them now would fetch: the best bid from the last
  sync, minus the sale fee (the value at the market price is shown on hover).
- **Exclusions:**
  - single bets, even already closed ones (they do not count in the results and can be readmitted
    as they were: same result and closing date; a sold bet comes back sold);
  - markets, events or categories, which automatic bets skip.
- **Settings:** you can choose the preset, turn automatic bets and sales on or off, or start over with a new capital.
- **Export:** «Export Excel» downloads the whole portfolio as it is at that moment, to analyse
  it in Excel, Google Sheets or LibreOffice:
  - *Summary*: capital, value, realised and unrealised profits (at the bid and at the market
    price), counts, win rate, CLV, preset parameters;
  - *Bets*: every bet, excluded ones too, with purchase, sale or resolution, result, current
    value and plan of the open ones, the forecast that led to it (Jev, blend, evidence, edge,
    signal) and its economic assessment;
  - *Orders* (limit orders, filled or not), *Shadow* (bets the filters blocked), *Equity*,
    *Exclusions*, and *Columns*, which explains every column.

  Column names are stable keys (the same as the API); prices and probabilities are fractions
  0–1, amounts in dollars, times in UTC. «CSV» downloads only the bets (comma separator,
  decimal point).

## What went wrong in the first portfolio

The first real simulated portfolio lost 24 % of its $50 in a day and a half (13 bets); the
crypto price markets alone lost $11 of the $11.86. The causes, and what changed:

| Cause | What changed |
|---|---|
| Jev estimated markets decided by the price of Bitcoin or Ethereum (66 % against a 5.5 % market) without seeing that price | Price markets are left out: no bets, no paid forecasts |
| Two bets were bought 24 minutes before the end, against a price that already knew the outcome | Minimum hours to the end per preset (72 / 24 / 12) |
| A manual bet used a 19-hour-old forecast, whose blend had been pooled with the price of then: +$22 expected on $1.89 | The blend is recomputed at the current price; old forecasts or big price moves need a new one |
| Every bet came from a disagreement of about 50 points between Jev and the price: the biggest disagreements are the likeliest Jev errors | Jev's maximum weight 0.5 → 0.25, and less weight the further Jev is from the price |
| The uncertainty was narrowed (factor 0.75) because the blend was consistent with itself, not because it beat the price | Narrowing only when the blend beats the price on resolved markets |
| Annualised returns were capped at 1000 %, so the return check never excluded anything | Minimum prudent return per bet (10 / 6 / 3 %) |

With 13 bets the result itself proves little; the causes above are structural. Run a
[backtest](verification.md#backtest) and apply the calibration it suggests before trusting the
signals again.

## Buy and sell strategy

The **What to do** card of a market's detail page (and of each outcome of multi-outcome
markets) turns forecast and economic assessment into instructions (`backend/betting/strategy.py`):

| Action | When | What it says |
|---|---|---|
| **Buy YES / NO** | The assessment says *Worth it* | Limit order: shares, maximum price, amount. Once bought, a limit sell order |
| **Wait** | The edge is there, but at the current price costs and uncertainty eat it up | The price at which it pays: a pending order |
| **Avoid** | The problem is not the price: illiquid market, end date beyond the preset, risk limits full | The reasons |
| **No action** | The estimate is close to the price | At what price buying YES or NO would become worth it |
| **Hold** | There is a position and the price is still below the estimate | The limit sell order |
| **Sell** | The price has reached the estimate, or a new forecast has turned it | How many shares and at what price |

**Prices at which the decision changes.** For each YES price between 1¢ and 99¢ the blended
estimate is recomputed as in the forecast (calibrated Jev pooled with that price) and the
checks are run again: signal (`MIN_EDGE`), margin after fees and uncertainty, annual return.
The highest price that passes them all is «buy YES below»; the lowest for NO is «buy NO
above». The detail page shows them on a price scale together with the current price.

**When to sell.** Holding a share is worth the probability that it wins, discounted for the
time the capital stays locked at the return the preset asks for (risk-free rate + premium).
Selling is worth the bid on offer minus the fee. The selling price is the lowest at which
selling pays at least as much as holding:

```
sell if  bid − fee(bid)  ≥  P(side wins | price = bid) / (1 + required return)^(days/365)
```

With little time to the end date it almost matches the estimate; with many months it is lower,
because cashing in earlier frees the capital. There is no fixed stop-loss: if the price falls
but the estimate does not, the share is even better value. You sell instead when a new
forecast says the opposite side is worth it.

**Why yes / why not.** Each plan lists the reasons: the difference between estimate and price,
the news Jev judged for or against (with the titles), the margin after costs, the annual
return, the time, the probability of losing, the estimated book, the exclusions and the past
results (edge over the price on resolved markets and closing price of the signals).

**Confidence** *high*, *medium* or *low*: it goes up with strong news and good past results;
it goes down with weak news, an uncertain estimate, estimated prices without a book and
missing or negative past results.

The plan also shows up in the opportunities (with the prices at the time of the forecast), in
the portfolio (exit plan of each open position) and in the Telegram notifications (buy and
sell orders).
