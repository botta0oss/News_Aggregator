# Economic assessment, strategy and portfolio

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/strategia.md)</sub>

Whether a forecast is really worth it, what to do (buy, wait, sell) and how the simulated portfolio measures the results.

## Economic assessment and simulated portfolio

An edge on paper is not enough: the economic assessment (`backend/betting/`) decides whether a
forecast is really worth it, how much to stake and at what maximum price. It is computed after
every forecast and, live, in the **Worth it?** card of the market detail page.

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
   the blended forecasts were overconfident, narrowed if they were cautious (a factor between
   0.75 and 2).
3. **Net margin.** `p_prudent − (price + fee)` must exceed the preset's threshold.
4. **Time.** The prudent expected return is annualised over the days left before the end date
   and must exceed `RISK_FREE_RATE` + the preset's premium.
5. **How much to stake.** Kelly capital is computed on the book, because buying more worsens
   the price. A fraction of it is taken (depending on the preset) and then the limits per
   market, event, category, total invested, available cash and share of the book apply. The
   **maximum price** to pay is the price at which `price + fee` leaves exactly the minimum
   margin: `price + fee(price) = p_prudent − minimum margin`.
6. **Verdict.** *Worth it*, *Barely worth it* (the stake was cut to less than half by the
   limits) or *Not worth it*, always with the reasons.

| Preset | Kelly | z | Net margin | Annual premium | Max per market | Max per event | Max per category | Max invested | Min liquidity | Max end date |
|---|---|---|---|---|---|---|---|---|---|---|
| Prudent | ×0.15 | 1.64 | 4 pts | 15% | 2% | 5% | 15% | 40% | $25,000 | 120 d |
| Balanced | ×0.25 | 1.0 | 3 pts | 8% | 4% | 8% | 25% | 60% | $10,000 | 365 d |
| Aggressive | ×0.5 | 0.5 | 2 pts | 3% | 8% | 15% | 40% | 85% | $5,000 | 730 d |

**Simulated portfolio** (*Portfolio* page):
- **Automatic bets:** every forecast with a *Worth it* or *Barely worth it* verdict becomes a virtual bet at the real price of the moment, at most one open per market.
- **Selling:** after every market update and every new forecast, open positions are reviewed
  with the [strategy](#buy-and-sell-strategy): if the plan says «Sell», the shares are sold on
  the book (status «sold», with price and reason). It can be turned off («Sell automatically»)
  or done by hand. Sales at a profit count as won.
- **Closing:** happens when the market resolves for good (price at 1/0 and, if Gamma reports
  it, `umaResolutionStatus` = «resolved»: an outcome that is only proposed or disputed can still
  change). A winning share is worth $1; if the market closes 50-50 each share is worth $0.50
  and the bet counts as «voided» (outside the win rate).
- **What it shows:** current value, realised and unrealised profits, win rate, capital curve and a comparison between expected and actual profit.
- **Exclusions:**
  - single bets, even already closed ones (they do not count in the results and can be readmitted);
  - markets, events or categories, which automatic bets skip.
- **Settings:** you can choose the preset, turn automatic bets and sales on or off, or start over with a new capital.

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
