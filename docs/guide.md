# Dashboard guide

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/guida.md)</sub>

The dashboard's pages, the recommended workflow and what to do if it does not open.

## Dashboard

The web interface is served by the same app on `/`: HTML, CSS and JavaScript with no
dependencies and no build step, in the `frontend/` folder.

**Navigation.** Sections are grouped by what you are doing: *Signals* (Opportunities,
Alerts), *Markets* (Yes / No, Multi-outcome), *News*, *Results* (Portfolio, Backtest,
Calibration). Settings, Usage and costs, «How it works» and the account are at the bottom.
- **From 1024 px up:** a side menu that can be collapsed to icons only; the choice is
  remembered. At the top, a status line (latest news, open markets, Jev on) and the «Update»
  menu with the news and market updates (admins only).
- **On tablets and phones:** a bottom bar with the four most used sections and «More» for
  all the others.
- The number on «Alerts» is the opportunities of the last 24 hours.

**Language.** The **IT / EN** button at the top right, next to the theme button, switches the
whole dashboard between Italian and English. The choice is remembered in the browser; on the
first visit the dashboard follows the browser's language. Numbers, dates and amounts follow the
language too (`1,5 $` / `$1.5`), and so do the texts the server writes for the dashboard
(plans, reasons, error messages).

| Section | What it shows |
|---|---|
| **Opportunities** | Markets with an active signal, sorted by edge: price, Jev estimate and blended probability on the same 0–100 % scale, suggested stake and evidence strength. Filters for minimum edge and evidence. |
| **Markets** | Table of markets with search and sorting (click the columns or the «Sort by» menu): price in cents, volume, liquidity, end date with the days left, linked news, latest signal and edge. |
| **Multi-outcome** | Events with several possible answers (elections, leagues, awards) as a distribution: a price bar for each outcome, Jev's estimate and the blend, edge per outcome, a signal on the outcome furthest from its price (YES if undervalued, NO if overvalued), an arbitrage badge. |
| **Market detail** | The **What to do** card (action, limit orders, price scale, reasons for and against, exit plan, the numbers of the bet), the latest forecast and a button to ask for a new one, history (price against blend), linked news with relevance and impact, resolution rules. |
| **Portfolio** | Simulated portfolio: value, realised and unrealised profits, closing price, capital curve, open positions with their plan (hold or sell), closed or sold bets, risk presets, automatic buys and sells, exclusions. |
| **News** | Search in the news (title, text, summary) with highlighted words; filters for source, period, region, category, relevance to markets, opinions; sorting by relevance, score or date. |
| **Alerts** | Latest alerts with the news, the price at the alert and the favourable move after 15 minutes, 1, 6 and 24 hours; overall results; settings (categories, thresholds, daily limit, quiet hours, Telegram test). |
| **Backtest** | Jev on markets already resolved, with the news and the price of the time: Brier against the price, by horizon and category, calibration, simulated bets, suggested parameters to apply. |
| **Calibration** | Brier score of price, Jev and blend on resolved markets with a confidence interval, and the closing price of the signals (did the price move the right way?). |
| **Usage and costs** | Calls to the paid APIs per day and per feature, estimated cost, editable daily limits. |
| **How it works** | The method step by step with the server's real parameters, a worked example and a glossary. |
| **Settings** | Sources: add (with a feed test before saving), edit, activate/deactivate, update now, delete; recommended sources from the catalogue; reclassification; forecast parameters (read only). |

<p align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/strategy-dark.png">
  <img alt="The What to do card in a market's detail page" src="images/strategy-light.png" width="85%">
</picture>
</p>

In a market's detail page the **Why this signal** card shows the full calculation on the
numbers of that forecast: Jev estimate, weight, blended probability, edge, checks passed and
stake. Technical terms have a definition on hover (ⓘ).

Search accepts quoted phrases, `-word` to exclude and `or` for alternatives; the last word
also works as a prefix. The link `#/notizie?q=...` reopens the same search.

The «Update» menu starts the news and market updates. The page refreshes by itself while the
work goes on in the background.

**Assess all with Jev** (in Opportunities and Markets, admins only) asks for a forecast for
every open market with linked recent news. Before starting it shows how many paid calls are
needed and how long they take with the `JEV_RPM` limit. You can assess all markets or only
those never assessed or with news newer than the latest forecast.

The work runs in the background: you can close the page and find the progress when you come
back. It refreshes prices and links first, so the edge is computed on the current price. If
Jev answers with a rate limit, it waits and resumes from the same market. It stops by itself
after 3 consecutive errors, for example with no connection or an invalid key, and you can stop
it whenever you want. Simulated bets follow the portfolio rules.

Design choices:
- **Dark theme by default**, with a light theme from the button at the top. The choice is remembered.
- **Fixed colours for each series in every chart:** price orange, Jev teal, blend blue. Green and red are reserved for signals and always come with an icon and text.
- **Palette checked** for colour blindness on both themes.
- **Distinct shapes:** circle and diamond, solid and dashed line. Every chart has a legend with values and an alternative table.
- **Numbers in a monospace font** (Fira Code) and prices in cents, as on Polymarket.
- **Accessibility:** keyboard navigable, 44 px touch targets, respects reduced motion, no horizontal scrolling from 320 px up.

## Recommended workflow

1. Start the app: the first collection and the market sync begin right away.
2. See which markets have linked news:
   `GET /markets?only_linked=true`
3. Check that the news is relevant: `GET /markets/{id}`. If the links are noisy raise
   `MARKET_MATCH_THRESHOLD`, if there are too few lower it. In the market detail page
   «Search news» runs the targeted search right away.
4. Ask for a forecast on the markets you care about (`POST /markets/{id}/predict`),
   or on all of them with **Assess all with Jev** (`POST /markets/predict-all`).
5. Look at the opportunities: `GET /predictions/opportunities?min_edge=0.08`
6. When the results are convincing, turn on `PREDICTION_AUTO=true`, keeping an eye on costs
   with `PREDICTION_MAX_PER_RUN`.
7. Let the simulated portfolio run for dozens of resolved markets. Before using real money,
   check that the actual result is positive and close to the expected one.

## If the dashboard does not open

If the page stays empty with only the logo, the app's JavaScript did not start. After 8
seconds the page says so and offers to reload it.

1. **Hard reload** (Ctrl+F5 or Cmd+Shift+R). After an update the browser may have old files
   in its cache; the dashboard files are now served with `Cache-Control: no-cache`, so from
   the next update on it should not happen again.
2. **If it persists**, open the browser's developer tools (F12 → Console and Network) and
   check the container logs: a request that does not answer points to a stuck server.
