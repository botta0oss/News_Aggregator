# Checking the results: backtest and calibration

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/verifica.md)</sub>

How much to trust the forecasts, on the past and on the markets as they resolve.

## Backtest

The simulated portfolio and the alerts measure the results as markets resolve, that is over
weeks or months. The backtest gives a first answer right away: how would Jev have forecast the
markets already resolved?

**How it works.**
1. **Markets.** It takes the Yes/No markets closed in the chosen period, most traded first
   (Gamma API, `closed=true`).
2. **Moments.** For each market and each horizon (1, 7 or 30 days before the close) it rebuilds
   the situation at that moment:
   - the **price of the time**, from the CLOB history (`/prices-history`);
   - the **news of the 7 previous days**, from one of two sources (chosen in the form):
     - **archive**: the articles this app had already saved at that date
       (`fetched_at ≤ moment`). No later news can get in: it is the honest measure, but it only
       covers the period in which the app was running;
     - **Google News** with the `after:`/`before:` filters: it covers any period, but the date
       filters let through pages updated later, and results can look better than they are.
       Those with a later date are discarded anyway.

     By default the archive is used when it has news for that case, otherwise Google News. Each
     case records the source used, and the results of the archive-only cases are shown
     separately.
3. **News selection.** Match (meaning + key terms) and choice of the best, as in the app.
4. **Forecast.** Jev gets the same request, with «today» set to that date and without seeing
   the price.
5. **Signal and bet.** Blend, signal and economic assessment with the portfolio's preset. The
   historical book does not exist: the price is the one of the time plus half the typical
   spread.
6. **Comparison.** The actual outcome of the market says who was right.

For multi-outcome events, the outcomes shown to Jev are the most likely **according to the
price of the time** (up to `MULTI_MAX_OUTCOMES`, among the 30 most traded), as live. If the
winner was priced low it ends up among the «other outcomes»: choosing them knowing who won
would make the results look better than they are.

**Skipped cases** (without using calls):
- historical price not available;
- outcome already priced in (below 3 % or above 97 %, can be turned off);
- no news in those days.

There is a Jev call limit for each backtest. The work runs in the background, with progress
and a button to stop it. If the server restarts, the backtest shows as stopped.

**Results.**
- Brier score (lower is better) of price, Jev and blend, in total, by horizon and by category.
- **Edge over the price** (price Brier − blended Brier) with the **95 % interval**, computed
  with a bootstrap that resamples whole markets: the horizons of the same market share the
  outcome and are not independent cases. If the interval includes zero, the edge may be due to
  chance.
- Share of right signals and simulated bets: profit and ROI.
- Calibration chart (forecast against happened).
- Table of cases, with the news Jev read.

**Suggested parameters.**
- **Jev's calibration** (Platt scaling: logistic regression of the outcome on `logit P_jev`,
  with a slight penalty towards «no correction»).
- With Jev calibrated, **Jev's maximum weight** that gives the blend the lowest Brier.
- With that weight, the **minimum edge** that would have paid most staking $1 per signal, if
  there are at least 10 bets.
- **Out-of-sample check.** Markets are sorted by close date: parameters are estimated on the
  oldest 70 % and tried on the most recent 30 %, as they would be used live. A value is
  *recommended* only if it improves there too; the values shown are then re-estimated on all
  markets. At least 10 markets per part are needed, otherwise nothing is recommended.
- Reliability depends on the **distinct markets** (not the cases): *low* below 30, *medium*
  below 100, *high* from 100 up.

An admin can apply the recommended values with one click: they are saved in the database,
apply to the following forecasts and override the `.env` values until you press «Restore the
.env values». Only `MODEL_WEIGHT_MAX`, `MIN_EDGE`, `JEV_CALIB_A` and `JEV_CALIB_B` can be
changed.

**The limit.** Jev might already know the outcome of past events: markets resolved before the
date up to which its model's knowledge goes can give results that are too good. For an honest
measure choose markets closed after that date and, when the archive covers them, archive news
only.

## Calibration

When a followed market resolves, the sync detects it and saves the outcome.
`/predictions/calibration` compares, on the latest forecast made for each market, the
**Brier score** (mean squared error, lower is better) of:

- `brier_market`: the market price at the time of the forecast;
- `brier_model`: Jev's raw estimate;
- `brier_blended`: the blended probability used for the signals.

The system adds value only if `brier_blended` is consistently **lower** than `brier_market`
over many markets. With few resolved markets the comparison is not meaningful: `gain_blended`
and `gain_model` give the edge over the price with the 95 % interval (bootstrap on markets). If
the interval includes zero, the edge may be due to chance.

**Closing price (CLV).** The sync records the last price of each market while it still trades
(`last_trading_price`): it is the «close», the market's estimate when all the information is
known. Consistently buying below the close is the most reliable sign of a real edge, and it
can be measured long before there are enough resolved markets for the Brier score:
- `signal_clv` in Calibration: how much the price moved towards each signal up to the close;
- in the simulated portfolio: close of the side bought − average price paid, per bet and on
  average (`clv`; `clv_open` is the move so far on the open ones);
- in the alerts: move from the alert price to the close, in the recommended direction.
