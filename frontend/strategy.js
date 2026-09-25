// "Cosa fare": the plan built from the latest forecast — action, orders, price levels, reasons.
import { t } from "./i18n.js";
import { h, fmt, icon, infoTip } from "./ui.js";

const ACTIONS = {
  BUY: { cls: "badge-good", icon: "up", label: t("Compra") },
  WAIT: { cls: "badge-warning", icon: "pause", label: t("Aspetta") },
  AVOID: { cls: "badge-critical", icon: "x", label: t("Evita") },
  HOLD: { cls: "badge-accent", icon: "check", label: t("Tieni") },
  SELL: { cls: "badge-warning", icon: "down", label: t("Vendi") },
  NONE: { cls: "", icon: "minus", label: t("Nessuna azione") },
};
const SIDE = { YES: t("SÌ"), NO: "NO" };
const CONFIDENCE = { alta: "badge-good", media: "", bassa: "badge-critical" };

export const STRATEGY_HELP = {
  sell: t("Il prezzo a cui incassare subito rende più che aspettare la risoluzione, tenendo conto delle commissioni e del rendimento che il preset chiede al capitale bloccato."),
  levels: t("Prezzi del SÌ a cui la decisione cambierebbe. La stima viene ricalcolata a ogni prezzo con lo stesso metodo della previsione."),
  stop: t("Niente stop-loss fisso: se il prezzo scende ma la stima non cambia, la quota è ancora più conveniente. Si vende quando una nuova previsione gira la stima o il prezzo raggiunge il valore stimato."),
};

export function actionBadge(plan, big = false) {
  const a = ACTIONS[plan.action] || ACTIONS.NONE;
  const label = plan.action === "BUY" || plan.action === "SELL" ? `${a.label} ${SIDE[plan.side] || ""}`.trim() : a.label;
  return h("span", { class: `badge ${a.cls}${big ? " badge-lg" : ""}` }, icon(a.icon), label);
}

function orderLine(o) {
  const side = SIDE[o.side];
  // "12 quote SÌ" / "12 YES shares", or just the side when the quantity is not known
  const what = o.shares ? t("{0} quote {1}", fmt.shares(o.shares), side) : side;
  if (o.type === "buy" && o.conditional) {
    return [icon("pause"), h("span", {}, t("Ordine in attesa: "), h("b", {}, t("compra {0} a {1} o meno", side, fmt.cents(o.limit))),
      t(". Conviene solo se il prezzo arriva lì."))];
  }
  if (o.type === "buy") {
    return [icon("up"), h("span", {}, t("Acquisto con ordine limite: "), h("b", {}, t("{0} a non più di {1}", what, fmt.cents(o.limit))),
      o.usd != null ? t(" · circa {0}", fmt.money(o.usd)) : "")];
  }
  if (o.after_fill) {
    return [icon("target"), h("span", {}, t("Appena comprate: "), h("b", {}, t("vendita limite a {0}", fmt.cents(o.limit))),
      t(" (resta nel book finché il prezzo non ci arriva)"))];
  }
  return [icon("down"), h("span", {}, t("Vendita: "), h("b", {}, o.limit != null ? t("{0} a {1} o più", what, fmt.cents(o.limit)) : t("{0} al meglio", what)))];
}

/** Scale of YES prices: where buying YES or NO would pay, where the price is, where to sell. */
function priceLadder(plan, priceYes) {
  const lv = plan.levels || {};
  if (priceYes == null || (lv.buy_yes_below == null && lv.buy_no_above == null && lv.sell_above == null)) return null;
  const pos = (v) => `${Math.max(0, Math.min(100, v * 100))}%`;
  // The sale target is a price of the side held: on the YES scale for a NO position it is 1 − target
  const sellYes = lv.sell_above == null ? null : plan.side === "NO" ? 1 - lv.sell_above : lv.sell_above;
  const labels = [];
  if (lv.buy_yes_below != null) labels.push(t("compra SÌ sotto {0}", fmt.cents(lv.buy_yes_below)));
  if (lv.buy_no_above != null) labels.push(t("compra NO sopra {0}", fmt.cents(lv.buy_no_above)));
  if (sellYes != null) labels.push(t("vendi a {0}", fmt.cents(sellYes)));
  return h("div", { class: "ladder-wrap" },
    h("div", { class: "ladder", role: "img", "aria-label": t("Prezzo del SÌ {0}: {1}", fmt.cents(priceYes), labels.join(", ")) },
      lv.buy_yes_below != null ? h("span", { class: "zone yes", style: { left: "0", width: pos(lv.buy_yes_below) } }) : null,
      lv.buy_no_above != null ? h("span", { class: "zone no", style: { left: pos(lv.buy_no_above), right: "0" } }) : null,
      sellYes != null ? h("span", { class: "tick sell", style: { left: pos(sellYes) } }, h("span", { class: "tick-label" }, t("vendi {0}", fmt.cents(sellYes)))) : null,
      h("span", { class: "tick now", style: { left: pos(priceYes) } }, h("span", { class: "tick-label" }, t("ora {0}", fmt.cents(priceYes))))),
    h("div", { class: "ladder-axis mono" }, h("span", {}, "0¢"), h("span", {}, t("prezzo del SÌ")), h("span", {}, "100¢")),
    h("div", { class: "ladder-legend" },
      lv.buy_yes_below != null ? h("span", {}, h("span", { class: "key zone-yes" }), t("Compra SÌ sotto {0}", fmt.cents(lv.buy_yes_below))) : null,
      lv.buy_no_above != null ? h("span", {}, h("span", { class: "key zone-no" }), t("Compra NO se il SÌ sale sopra {0}", fmt.cents(lv.buy_no_above))) : null,
      sellYes != null ? h("span", {}, h("span", { class: "key tick-sell" }), t("Vendi a {0} ({1})", fmt.cents(lv.sell_above), SIDE[plan.side] || t("lato comprato"))) : null,
      infoTip(STRATEGY_HELP.levels)));
}

function reasonList(items, good) {
  if (!items.length) return h("p", { class: "muted small" }, good ? t("Nessun motivo forte a favore.") : t("Nessun motivo contro rilevante."));
  return h("ul", { class: `plan-reasons ${good ? "pro" : "con"}` }, items.map((x) => h("li", {}, icon(good ? "check" : "x"), h("span", {}, x))));
}

/** The whole plan, for the top of the "Cosa fare" card. `priceYes`: current YES price of the market. */
export function strategySection(plan, priceYes) {
  return h("div", { class: "plan" },
    h("div", { class: "plan-head" },
      actionBadge(plan, true),
      // The badge already says the action: repeat the title only when it adds something ("... a 31¢ o meno")
      h("div", { class: "plan-title" }, plan.title !== actionBadge(plan).textContent ? h("b", {}, plan.title) : null,
        h("p", { class: plan.title !== actionBadge(plan).textContent ? "secondary" : "" }, plan.summary)),
      h("span", { class: `badge ${CONFIDENCE[plan.confidence] || ""}`, title: plan.confidence_why.join(" · ") },
        t("fiducia {0}", t(plan.confidence)))),
    plan.orders.length ? h("ul", { class: "plan-orders" }, plan.orders.map((o) => h("li", {}, ...orderLine(o)))) : null,
    priceLadder(plan, priceYes),
    h("div", { class: "plan-why" },
      h("div", {}, h("h3", {}, t("Perché sì")), reasonList(plan.pros, true)),
      h("div", {}, h("h3", {}, t("Perché no")), reasonList(plan.cons, false))),
    plan.exit.length ? h("div", { class: "plan-exit" },
      h("h3", {}, t("Piano d'uscita"), infoTip(STRATEGY_HELP.stop)),
      h("ul", { class: "bullets small" }, plan.exit.map((x) => h("li", {}, x)))) : null,
    h("p", { class: "muted small" }, t("Fiducia {0}: {1}.", t(plan.confidence), plan.confidence_why.join(", "))),
  );
}

const STRUCTURAL = new Set(["illiquid", "too_far", "exposure_cap", "no_cash", "no_book"]);

/** One line for lists, from the evaluation stored with the forecast (prices of that moment). */
export function planLine(ev) {
  if (!ev) return null;
  const side = SIDE[ev.side];
  const blocking = (ev.reasons || []).filter((r) => r.blocking);
  let badge, text;
  if (ev.verdict === "GO" || ev.verdict === "SMALL") {
    badge = actionBadge({ action: "BUY", side: ev.side });
    text = t("{0} a non più di {1} · circa {2}", side, fmt.cents(ev.limit_price), fmt.money(ev.outlay));
  } else if (blocking.some((r) => STRUCTURAL.has(r.code))) {
    badge = actionBadge({ action: "AVOID" });
    text = blocking.find((r) => STRUCTURAL.has(r.code)).text;
  } else if (blocking.length) {
    badge = actionBadge({ action: "WAIT" });
    text = t("al prezzo di allora costi e incertezza si mangiavano il vantaggio (limite {0})", fmt.cents(ev.limit_price));
  } else {
    return null;
  }
  return h("p", { class: "opp-plan" }, badge, h("span", { class: "small" }, text));
}
