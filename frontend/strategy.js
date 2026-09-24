// "Cosa fare": the plan built from the latest forecast — action, orders, price levels, reasons.
import { h, fmt, icon, infoTip } from "./ui.js";

const ACTIONS = {
  BUY: { cls: "badge-good", icon: "up", label: "Compra" },
  WAIT: { cls: "badge-warning", icon: "pause", label: "Aspetta" },
  AVOID: { cls: "badge-critical", icon: "x", label: "Evita" },
  HOLD: { cls: "badge-accent", icon: "check", label: "Tieni" },
  SELL: { cls: "badge-warning", icon: "down", label: "Vendi" },
  NONE: { cls: "", icon: "minus", label: "Nessuna azione" },
};
const SIDE = { YES: "SÌ", NO: "NO" };
const CONFIDENCE = { alta: "badge-good", media: "", bassa: "badge-critical" };

export const STRATEGY_HELP = {
  sell: "Il prezzo a cui incassare subito rende più che aspettare la risoluzione, tenendo conto delle commissioni e del rendimento che il preset chiede al capitale bloccato.",
  levels: "Prezzi del SÌ a cui la decisione cambierebbe. La stima viene ricalcolata a ogni prezzo con lo stesso metodo della previsione.",
  stop: "Niente stop-loss fisso: se il prezzo scende ma la stima non cambia, la quota è ancora più conveniente. Si vende quando una nuova previsione gira la stima o il prezzo raggiunge il valore stimato.",
};

export function actionBadge(plan, big = false) {
  const a = ACTIONS[plan.action] || ACTIONS.NONE;
  const label = plan.action === "BUY" || plan.action === "SELL" ? `${a.label} ${SIDE[plan.side] || ""}`.trim() : a.label;
  return h("span", { class: `badge ${a.cls}${big ? " badge-lg" : ""}` }, icon(a.icon), label);
}

function orderLine(o) {
  const side = SIDE[o.side];
  const qty = o.shares ? `${fmt.shares(o.shares)} quote ` : "";
  if (o.type === "buy" && o.conditional) {
    return [icon("pause"), h("span", {}, "Ordine in attesa: ", h("b", {}, `compra ${side} a ${fmt.cents(o.limit)} o meno`),
      ". Conviene solo se il prezzo arriva lì.")];
  }
  if (o.type === "buy") {
    return [icon("up"), h("span", {}, "Acquisto con ordine limite: ", h("b", {}, `${qty}${side} a non più di ${fmt.cents(o.limit)}`),
      o.usd != null ? ` · circa ${fmt.money(o.usd)}` : "")];
  }
  if (o.after_fill) {
    return [icon("target"), h("span", {}, "Appena comprate: ", h("b", {}, `vendita limite a ${fmt.cents(o.limit)}`),
      " (resta nel book finché il prezzo non ci arriva)")];
  }
  return [icon("down"), h("span", {}, "Vendita: ", h("b", {}, o.limit != null ? `${qty}${side} a ${fmt.cents(o.limit)} o più` : `${qty}${side} al meglio`))];
}

/** Scale of YES prices: where buying YES or NO would pay, where the price is, where to sell. */
function priceLadder(plan, priceYes) {
  const lv = plan.levels || {};
  if (priceYes == null || (lv.buy_yes_below == null && lv.buy_no_above == null && lv.sell_above == null)) return null;
  const pos = (v) => `${Math.max(0, Math.min(100, v * 100))}%`;
  // The sale target is a price of the side held: on the YES scale for a NO position it is 1 − target
  const sellYes = lv.sell_above == null ? null : plan.side === "NO" ? 1 - lv.sell_above : lv.sell_above;
  const labels = [];
  if (lv.buy_yes_below != null) labels.push(`compra SÌ sotto ${fmt.cents(lv.buy_yes_below)}`);
  if (lv.buy_no_above != null) labels.push(`compra NO sopra ${fmt.cents(lv.buy_no_above)}`);
  if (sellYes != null) labels.push(`vendi a ${fmt.cents(sellYes)}`);
  return h("div", { class: "ladder-wrap" },
    h("div", { class: "ladder", role: "img", "aria-label": `Prezzo del SÌ ${fmt.cents(priceYes)}: ${labels.join(", ")}` },
      lv.buy_yes_below != null ? h("span", { class: "zone yes", style: { left: "0", width: pos(lv.buy_yes_below) } }) : null,
      lv.buy_no_above != null ? h("span", { class: "zone no", style: { left: pos(lv.buy_no_above), right: "0" } }) : null,
      sellYes != null ? h("span", { class: "tick sell", style: { left: pos(sellYes) } }, h("span", { class: "tick-label" }, `vendi ${fmt.cents(sellYes)}`)) : null,
      h("span", { class: "tick now", style: { left: pos(priceYes) } }, h("span", { class: "tick-label" }, `ora ${fmt.cents(priceYes)}`))),
    h("div", { class: "ladder-axis mono" }, h("span", {}, "0¢"), h("span", {}, "prezzo del SÌ"), h("span", {}, "100¢")),
    h("div", { class: "ladder-legend" },
      lv.buy_yes_below != null ? h("span", {}, h("span", { class: "key zone-yes" }), `Compra SÌ sotto ${fmt.cents(lv.buy_yes_below)}`) : null,
      lv.buy_no_above != null ? h("span", {}, h("span", { class: "key zone-no" }), `Compra NO se il SÌ sale sopra ${fmt.cents(lv.buy_no_above)}`) : null,
      sellYes != null ? h("span", {}, h("span", { class: "key tick-sell" }), `Vendi a ${fmt.cents(lv.sell_above)} (${SIDE[plan.side] || "lato comprato"})`) : null,
      infoTip(STRATEGY_HELP.levels)));
}

function reasonList(items, good) {
  if (!items.length) return h("p", { class: "muted small" }, good ? "Nessun motivo forte a favore." : "Nessun motivo contro rilevante.");
  return h("ul", { class: `plan-reasons ${good ? "pro" : "con"}` }, items.map((t) => h("li", {}, icon(good ? "check" : "x"), h("span", {}, t))));
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
        `fiducia ${plan.confidence}`)),
    plan.orders.length ? h("ul", { class: "plan-orders" }, plan.orders.map((o) => h("li", {}, ...orderLine(o)))) : null,
    priceLadder(plan, priceYes),
    h("div", { class: "plan-why" },
      h("div", {}, h("h3", {}, "Perché sì"), reasonList(plan.pros, true)),
      h("div", {}, h("h3", {}, "Perché no"), reasonList(plan.cons, false))),
    plan.exit.length ? h("div", { class: "plan-exit" },
      h("h3", {}, "Piano d'uscita", infoTip(STRATEGY_HELP.stop)),
      h("ul", { class: "bullets small" }, plan.exit.map((t) => h("li", {}, t)))) : null,
    h("p", { class: "muted small" }, `Fiducia ${plan.confidence}: ${plan.confidence_why.join(", ")}.`),
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
    text = `${side} a non più di ${fmt.cents(ev.limit_price)} · circa ${fmt.money(ev.outlay)}`;
  } else if (blocking.some((r) => STRUCTURAL.has(r.code))) {
    badge = actionBadge({ action: "AVOID" });
    text = blocking.find((r) => STRUCTURAL.has(r.code)).text;
  } else if (blocking.length) {
    badge = actionBadge({ action: "WAIT" });
    text = `al prezzo di allora costi e incertezza si mangiavano il vantaggio (limite ${fmt.cents(ev.limit_price)})`;
  } else {
    return null;
  }
  return h("p", { class: "opp-plan" }, badge, h("span", { class: "small" }, text));
}
