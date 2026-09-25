# News–price alerts

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/allerte.md)</sub>

Telegram notifications when fresh news moves the estimate before the price.

On Polymarket the edge comes above all from speed: news comes out and the price takes minutes
or hours to adjust. Alerts are there to get in first.

**When an alert fires.** Every time news is linked to markets:
- in the full collection;
- in a quick check every `ALERT_SCAN_MINUTES` minutes, which fetches the sources and links the
  news without summaries or classification.

For each new link the app checks that the news:
- is fresh (configurable maximum age, 6 hours by default);
- is relevant (60 % match by default);
- comes from a reliable source and is not an opinion piece;
- concerns a followed category.

For each market the best news item counts. Markets on pause, that is with an alert in the
last 3 hours, are skipped.

**What happens.**
1. The price is refreshed from Polymarket at that moment.
2. Jev assesses the market: it is one call, counted in the daily limit (30 by default).
3. The economic assessment decides whether it is worth it and how much to stake. The
   simulated bet follows the portfolio rules.
4. If it is worth it a Telegram message arrives with the news, the price, the estimate, the
   edge, the stake and the maximum price. During quiet hours the message arrives silently.
   Messages are written in `APP_LANGUAGE` (English by default, `it` for Italian).
5. Every `ALERT_FOLLOWUP_MINUTES` minutes the price 15 minutes, 1, 6 and 24 hours after the
   alert is recorded.

**The results.** The *favourable move* measures how many points the price moved in the
recommended direction. If it is positive, the alert came before the market. The *Alerts* page
shows the average and the share of favourable alerts for each interval, and the outcome of the
markets already resolved. Assessments that were not worth it are saved too («All
assessments»), so you can see how much alerts cost compared with what they return.

**Setting up Telegram.**
1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Send a message to the bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates`:
   the number in `chat.id` is your `TELEGRAM_CHAT_ID`. For a group, add the bot to the group;
   the id starts with `-`.
3. Put the two values in `.env`, restart and press «Send test message» on the *Alerts* page.

The token stays only in `.env`: it does not appear in the API, the logs or error messages.
