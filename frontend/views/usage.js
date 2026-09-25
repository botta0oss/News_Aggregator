// Uso e costi: paid AI calls per day and per feature, daily limits and prices.
import { t } from "../i18n.js";
import { h, api, fmt, toast, icon, statTile, meter, infoTip } from "../ui.js";
import { dailyBars } from "../charts.js";

const PROVIDERS = { jev: t("TypeSafe Jev"), groq: t("Groq"), gemini: t("Gemini") };
const state = { metric: "cost" };
const usd = (v) => (v == null ? "–" : v < 0.01 && v > 0 ? fmt.dollars(fmt.dec(v, 4)) : fmt.money(v));
const tokens = (n) => (n >= 1e6 ? `${fmt.dec(n / 1e6, 1)} M` : n >= 1e3 ? `${Math.round(n / 1e3)} k` : fmt.int(n));

export async function viewUsage(ctx) {
  const data = await api("/usage", { params: { days: 30 } });
  const { today, report, settings } = data;
  const items = report.items;
  const pricesSet = ["JEV_PRICE_PER_CALL", "JEV_PRICE_INPUT_MTOK", "JEV_PRICE_OUTPUT_MTOK", "GROQ_PRICE_INPUT_MTOK", "GEMINI_PRICE_INPUT_MTOK"]
    .some((k) => settings[k].value > 0);

  return h("div", {},
    ctx.pageHead(t("Uso e costi"),
      t("Chiamate alle API a pagamento (Jev, Groq, Gemini) per giorno e per funzione, con limiti giornalieri. Oltre il limite le chiamate si fermano fino a mezzanotte: classificazione e riassunti passano alle alternative gratuite.")),
    todayCard(today, pricesSet),
    historyCard(report, items),
    breakdownCard(report, items),
    settingsCard(ctx, settings),
  );
}

function todayCard(today, pricesSet) {
  const resetIn = t("{0} h {1} min", Math.floor(today.resets_in_seconds / 3600), Math.floor((today.resets_in_seconds % 3600) / 60));
  const jevTile = statTile(t("Chiamate a Jev oggi"), fmt.int(today.jev_calls),
    today.jev_call_limit ? t("su {0} al giorno", fmt.int(today.jev_call_limit)) : t("nessun limite impostato"));
  const costTile = statTile(t("Spesa stimata oggi"), pricesSet ? usd(today.cost) : "–",
    !pricesSet ? t("imposta i prezzi qui sotto") : today.budget ? t("su {0} al giorno", usd(today.budget)) : t("nessun budget impostato"));
  if (today.share_jev_calls != null) jevTile.append(meter(Math.min(1, today.share_jev_calls), t("{0} del limite", fmt.pct(today.share_jev_calls))));
  if (today.share_cost != null) costTile.append(meter(Math.min(1, today.share_cost), t("{0} del budget", fmt.pct(today.share_cost))));
  const warn = [today.share_jev_calls, today.share_cost].some((v) => v != null && v >= today.warn_share);
  return h("section", { class: "card", "aria-labelledby": "h-us-today", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("h2", { id: "h-us-today" }, t("Oggi")), h("span", { class: "muted small" }, t("si azzera tra {0}", resetIn))),
    today.blocked ? h("div", { class: "notice", role: "alert" }, icon("alert"), h("p", {},
      h("b", {}, t("Limite giornaliero raggiunto. ")), t("Previsioni, allerte e backtest sono in pausa fino a mezzanotte; classificazione e riassunti usano le alternative gratuite.")))
      : warn ? h("div", { class: "notice", role: "status" }, icon("alert"), h("p", {}, t("Hai usato più del {0} di un limite giornaliero.", fmt.pct(today.warn_share)))) : null,
    h("div", { class: "kpis" }, jevTile, costTile),
  );
}

function historyCard(report, items) {
  const days = [];
  const start = new Date(`${report.since}T00:00:00`);
  for (let i = 0; i < report.days; i++) {
    const d = new Date(start.getTime() + i * 86_400_000);
    days.push(d.toISOString().slice(0, 10));
  }
  const byDay = new Map(days.map((d) => [d, []]));
  for (const it of items) byDay.get(it.day)?.push(it);
  const valueOf = (it) => (state.metric === "cost" ? it.cost : state.metric === "calls" ? it.calls : it.input_tokens + it.output_tokens);
  const format = state.metric === "cost" ? usd : state.metric === "calls" ? (v) => fmt.int(Math.round(v)) : (v) => tokens(Math.round(v));
  const holder = h("div", {});
  const paint = () => {
    const rows = days.map((d) => {
      const list = byDay.get(d);
      const perFeature = {};
      for (const it of list) perFeature[it.feature] = (perFeature[it.feature] || 0) + valueOf(it);
      const tip = Object.entries(perFeature).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 5)
        .map(([f, v]) => [null, report.features[f] || f, format(v)]);
      return { day: d, value: list.reduce((a, it) => a + valueOf(it), 0), tip };
    });
    holder.replaceChildren(rows.some((r) => r.value > 0)
      ? dailyBars(rows, { format, label: t("Uso per giorno negli ultimi {0} giorni", report.days) })
      : h("p", { class: "secondary" }, t("Nessuna chiamata a pagamento in questo periodo.")));
  };
  const seg = h("div", { class: "segmented", role: "group", "aria-label": t("Misura") },
    [["cost", t("Costo")], ["calls", t("Chiamate")], ["tokens", t("Token")]].map(([k, l]) => {
      const b = h("button", { class: "seg", type: "button", "aria-pressed": String(state.metric === k) }, l);
      b.addEventListener("click", () => {
        state.metric = k;
        seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        paint();
      });
      return b;
    }));
  paint();
  return h("section", { class: "card", "aria-labelledby": "h-us-hist", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("h2", { id: "h-us-hist" }, t("Ultimi {0} giorni", report.days)), seg),
    holder);
}

function breakdownCard(report, items) {
  const group = (key) => {
    const out = {};
    for (const it of items) {
      const k = it[key];
      out[k] = out[k] || { calls: 0, errors: 0, input: 0, output: 0, cost: 0 };
      out[k].calls += it.calls; out[k].errors += it.errors; out[k].input += it.input_tokens; out[k].output += it.output_tokens; out[k].cost += it.cost;
    }
    return Object.entries(out).sort((a, b) => b[1].cost - a[1].cost || b[1].calls - a[1].calls);
  };
  const totalCost = items.reduce((a, it) => a + it.cost, 0);
  const table = (title, rows, names) => h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
    h("thead", {}, h("tr", {}, ...[title, t("Chiamate"), t("Errori"), t("Token in"), t("Token out"), t("Costo"), t("Quota")].map((t, i) => h("th", { scope: "col", class: i ? "num" : null }, t)))),
    h("tbody", {}, rows.map(([k, v]) => h("tr", {},
      h("th", { scope: "row" }, names[k] || k), h("td", { class: "num" }, fmt.int(v.calls)), h("td", { class: "num" }, fmt.int(v.errors)),
      h("td", { class: "num" }, tokens(v.input)), h("td", { class: "num" }, tokens(v.output)), h("td", { class: "num" }, usd(v.cost)),
      h("td", { class: "num" }, totalCost ? fmt.pct(v.cost / totalCost) : "–"))))));
  return h("section", { class: "card", "aria-labelledby": "h-us-break", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("h2", { id: "h-us-break" }, t("Per funzione e per servizio")),
      infoTip(t("Il costo è una stima: token (o chiamate) per i prezzi impostati qui sotto. Controlla sempre la fattura del fornitore."))),
    items.length ? [table(t("Funzione"), group("feature"), report.features), h("div", { style: { height: "12px" } }), table(t("Servizio"), group("provider"), PROVIDERS)]
      : h("p", { class: "secondary" }, t("Ancora nessuna chiamata registrata.")));
}

function settingsCard(ctx, settings) {
  const admin = ctx.isAdmin();
  const inputs = {};
  const num = (key, label, { step = "0.01", suffix, hint } = {}) => {
    const input = h("input", { id: `us-${key}`, class: "input", type: "number", min: "0", step, value: String(settings[key].value),
      inputmode: "decimal", disabled: !admin, style: { maxWidth: "140px" } });
    inputs[key] = input;
    return h("label", { class: "field-col", for: `us-${key}` }, h("span", { class: "field-label" }, label),
      h("span", { class: "inline" }, input, suffix ? h("span", { class: "muted small" }, suffix) : null),
      settings[key].overridden ? h("span", { class: "muted small" }, `.env: ${settings[key].default}`) : hint ? h("span", { class: "muted small" }, hint) : null);
  };
  let actions = null;
  if (admin) {
    const save = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), t("Salva"));
    save.addEventListener("click", async () => {
      const body = {};
      for (const [k, input] of Object.entries(inputs)) {
        const v = Number(input.value);
        if (!(v >= 0)) return toast(t("I valori devono essere numeri maggiori o uguali a zero"), { error: true });
        body[k] = k === "DAILY_JEV_CALL_LIMIT" ? Math.round(v) : v;
      }
      ctx.setBusy(save, true);
      try { await api("/usage/settings", { method: "PUT", body }); toast(t("Limiti e prezzi salvati")); ctx.rerender(); }
      catch (e) { toast(e.message, { error: true }); ctx.setBusy(save, false); }
    });
    const reset = h("button", { class: "btn btn-ghost", type: "button" }, t("Ripristina i valori del .env"));
    reset.addEventListener("click", async () => {
      try { await api("/usage/settings/reset", { method: "POST" }); toast(t("Valori del .env ripristinati")); ctx.rerender(); }
      catch (e) { toast(e.message, { error: true }); }
    });
    actions = h("div", { class: "actions", style: { marginTop: "16px" } }, save, reset);
  }
  return h("section", { class: "card", "aria-labelledby": "h-us-set" },
    h("div", { class: "card-head" }, h("h2", { id: "h-us-set" }, t("Limiti e prezzi")),
      !admin ? h("span", { class: "muted small" }, t("Solo un amministratore può modificarli")) : null),
    h("p", { class: "muted small", style: { marginBottom: "12px" } }, t("0 = nessun limite. I prezzi sono in dollari per milione di token: prendili dal listino del tuo piano.")),
    h("div", { class: "settings-grid" },
      num("DAILY_JEV_CALL_LIMIT", t("Chiamate a Jev al giorno"), { step: "1" }),
      num("DAILY_AI_BUDGET_USD", t("Budget giornaliero"), { suffix: "$" }),
      num("JEV_PRICE_PER_CALL", t("Jev: prezzo per chiamata"), { step: "0.001", suffix: "$" }),
      num("JEV_PRICE_INPUT_MTOK", t("Jev: token in"), { suffix: "$/M" }),
      num("JEV_PRICE_OUTPUT_MTOK", t("Jev: token out"), { suffix: "$/M" }),
      num("GROQ_PRICE_INPUT_MTOK", t("Groq: token in"), { suffix: "$/M" }),
      num("GROQ_PRICE_OUTPUT_MTOK", t("Groq: token out"), { suffix: "$/M" }),
      num("GEMINI_PRICE_INPUT_MTOK", t("Gemini: token in"), { suffix: "$/M" }),
      num("GEMINI_PRICE_OUTPUT_MTOK", t("Gemini: token out"), { suffix: "$/M" })),
    actions);
}
