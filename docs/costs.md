# API usage and costs

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/costi.md)</sub>

Log of the paid calls, cost estimate and daily limits.

Between classification, summaries, forecasts, alerts, «Assess all», backtests and
multi-outcome markets, paid calls can grow quickly. The **Usage and costs** page keeps them
under control.

**What is recorded.** Every call to Jev, Groq and Gemini, with:
- the **feature** that made it (classification, summaries, reclassification, forecasts,
  Assess all, alerts, multi-outcome, backtest);
- the input and output **tokens**;
- an **estimated cost**, computed from the prices you set: per million tokens and, for Jev,
  also per call. With prices at 0, calls and tokens are counted but not the cost.

Ollama runs locally and is not counted.

**Daily limits.** They are set from the page (admins only) and override the `.env` values:
- `DAILY_JEV_CALL_LIMIT`: maximum number of Jev calls;
- `DAILY_AI_BUDGET_USD`: maximum estimated spend for all APIs.

The day resets at midnight (`ALERT_TIMEZONE` time zone). Past the limit:
- forecasts, alerts, «Assess all» and backtests stop with a clear message;
- classification switches to the local heuristic and summaries to a text excerpt, so news
  keeps coming in;
- a banner across the whole dashboard says so.

At 80 % (`USAGE_WARN_SHARE`) and at 100 % of each limit a Telegram message arrives, once a
day, if Telegram is configured.

**What the page shows.**
- **Today:** calls and spend against the limits.
- **Last 30 days:** a chart per day, by cost, calls or tokens, with the breakdown by feature
  on hover.
- **Summary** by feature and by service, with the share of spend.

The cost is an estimate: the provider's invoice is what counts.
