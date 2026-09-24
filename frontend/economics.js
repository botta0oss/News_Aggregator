// "Conviene?" card: live economic evaluation of the latest forecast of a market.
import { h, api, fmt, toast, icon, infoTip } from "./ui.js";
import { strategySection } from "./strategy.js";

export const VERDICTS = {
  GO: { cls: "badge-good", icon: "check", label: "Conviene" },
  SMALL: { cls: "badge-warning", icon: "alert", label: "Conviene poco" },
  NO: { cls: "badge-critical", icon: "x", label: "Non conviene" },
};

export const ECON_HELP = {
  stake: "Cifra da investire in quote, calcolata con il criterio di Kelly sul book reale e poi ridotta dai limiti del preset.",
  limit: "Prezzo massimo per quota oltre il quale la scommessa non conviene più: usalo come prezzo limite dell'ordine.",
  apr: "Rendimento atteso riportato su base annua con la probabilità prudente: confronta scommesse con scadenze diverse.",
  prudent: "Probabilità del lato scelto meno l'incertezza della stima (z × σ). Le decisioni usano questa, non quella centrale.",
  breakeven: "Probabilità minima di vittoria perché la scommessa vada in pari, commissioni comprese.",
};

export function verdictBadge(verdict, big = false) {
  const v = VERDICTS[verdict] || VERDICTS.NO;
  return h("span", { class: `badge ${v.cls}${big ? " badge-lg" : ""}` }, icon(v.icon), v.label);
}

const money = fmt.money;
const pct = fmt.pct;

function figure(label, value, help, cls = "") {
  return h("div", { class: "econ-fig" },
    h("div", { class: "fig-label" }, label, help ? infoTip(help) : null),
    h("div", { class: `fig-value ${cls}` }, value),
  );
}

function aprText(apr) {
  if (apr == null) return "–";
  return apr >= 10 ? "oltre 1000%" : pct(apr);
}

const CAP_LABELS = {
  market: "Per mercato", event: "Per evento", category: "Per categoria", total: "Totale investito", cash: "Liquidità disponibile", book: "Profondità del book",
};

export function economicsCard(ctx, market) {
  const body = h("div", { class: "econ-body" }, h("div", { class: "skeleton", style: { height: "140px" } }));
  let preset = null;

  const presetSwitch = h("div", { class: "segmented", role: "group", "aria-label": "Preset di rischio" });
  const paintSwitch = (active) => presetSwitch.replaceChildren(...["prudente", "bilanciato", "aggressivo"].map((key) => h("button", {
    type: "button", class: "seg", "aria-pressed": String(key === active),
    on: { click: () => { preset = key; paintSwitch(key); load(); } },
  }, key[0].toUpperCase() + key.slice(1))));

  async function load() {
    body.setAttribute("aria-busy", "true");
    body.classList.add("is-loading");
    try {
      const data = await api(`/markets/${encodeURIComponent(market.id)}/economics`, { params: { preset } });
      paintSwitch(data.preset.key);
      body.replaceChildren(render(data));
    } catch (e) {
      if (!presetSwitch.childElementCount) paintSwitch(preset);
      body.replaceChildren(e.status === 409
        ? h("p", { class: "secondary" }, "Serve prima una previsione di Jev.")
        : h("div", { class: "econ-error", role: "alert" },
          h("p", { class: "secondary" }, `Valutazione non disponibile: ${e.message}`),
          h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: load } }, icon("refresh"), "Riprova")));
    } finally {
      body.removeAttribute("aria-busy");
      body.classList.remove("is-loading");
    }
  }

  function render(data) {
    const ev = data.evaluation;
    const blocking = ev.reasons.filter((r) => r.blocking);
    const infoReasons = ev.reasons.filter((r) => !r.blocking);
    const side = ev.side === "YES" ? "SÌ" : "NO";
    const isGo = ev.verdict !== "NO";
    // With a position already open, a full market cap means "hold what you have", not "bad bet"
    const holding = data.open_bet && blocking.every((r) => r.code === "exposure_cap");
    const b = data.open_bet;

    const headline = holding
      ? `Hai già ${fmt.shares(b.shares)} quote ${b.side === "YES" ? "SÌ" : "NO"} (${money(b.outlay)}): il limite del preset per questo mercato è raggiunto, non conviene aggiungere.`
      : isGo
        ? `${data.open_bet ? "Puoi aggiungere" : "Compra"} ${fmt.shares(ev.shares)} quote ${side} per ${money(ev.outlay)}, a non più di ${fmt.cents(ev.limit_price)} l'una.`
        : "Con il preset scelto questa scommessa non conviene.";

    return h("div", {},
      data.strategy ? strategySection(data.strategy, data.market.yes_price) : null,
      data.strategy ? h("h3", { class: "econ-sub" }, "I conti della scommessa") : null,
      h("div", { class: "econ-head" },
        holding ? h("span", { class: "badge badge-accent badge-lg" }, icon("check"), "In portafoglio") : verdictBadge(ev.verdict, true),
        h("p", { class: "econ-headline" }, headline),
      ),
      blocking.length && !holding ? h("ul", { class: "reasons" }, blocking.map((r) => h("li", {}, icon("x"), h("span", {}, r.text)))) : null,
      infoReasons.length ? h("ul", { class: "reasons info" }, infoReasons.map((r) => h("li", {}, icon("alert"), h("span", {}, r.text)))) : null,
      ev.notes.map((n) => h("p", { class: "note", style: { marginTop: "8px" } }, icon("alert"), n)),

      h("div", { class: "econ-grid" },
        figure("Puntata", isGo ? money(ev.outlay) : "–", ECON_HELP.stake),
        figure("Prezzo medio", ev.avg_price != null ? fmt.cents(ev.avg_price) : ev.best_price != null ? `${fmt.cents(ev.best_price)} (migliore)` : "–"),
        // Without a signal the plan's price levels apply, not this maximum
        figure("Prezzo massimo", ev.reasons.some((r) => r.code === "no_signal") ? "–" : fmt.cents(ev.limit_price), ECON_HELP.limit),
        figure("Se vince", isGo ? fmt.signedMoney(ev.profit_if_win) : "–", null, "pos"),
        figure("Se perde", isGo ? fmt.signedMoney(-ev.outlay) : "–", null, "neg"),
        figure("Profitto atteso", isGo ? fmt.signedMoney(ev.expected_profit) : "–", null, ev.expected_profit > 0 ? "pos" : ""),
        figure("Rendimento annuo (prudente)", aprText(ev.apr), ECON_HELP.apr),
        figure("Probabilità di perdere", pct(ev.prob_loss)),
        figure("Pareggio sopra", ev.break_even != null ? pct(ev.break_even) : "–", ECON_HELP.breakeven),
      ),

      h("details", { class: "econ-details" },
        h("summary", {}, "Come è calcolato"),
        h("ol", { class: "xsteps", style: { marginTop: "12px" } },
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "1"), h("div", {},
            h("div", { class: "xstep-title" }, "Probabilità prudente", infoTip(ECON_HELP.prudent)),
            h("p", { class: "formula" }, `${pct(ev.p_side)} − ${String(data.preset.z).replace(".", ",")} × ${pct(ev.sigma)} = `, h("b", {}, pct(ev.p_conservative))),
            h("p", { class: "muted small" }, `Probabilità che vinca il ${side} secondo la stima blended, meno l'incertezza della stima.`),
          )),
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "2"), h("div", {},
            h("div", { class: "xstep-title" }, "Costi reali"),
            h("p", {}, ev.quote_source === "book" ? "Prezzi presi dal book di Polymarket in questo momento." : "Book non disponibile: prezzo stimato."),
            h("p", { class: "formula" }, `Prezzo medio ${ev.avg_price != null ? fmt.cents(ev.avg_price) : fmt.cents(ev.best_price)} (a metà mercato ${fmt.cents(ev.mid)}), commissioni ${money(ev.fee)}`),
            ev.net_edge != null ? h("p", { class: "formula" }, `Margine netto: ${pct(ev.p_conservative)} − costo per quota = `, h("b", {}, fmt.pts(ev.net_edge)),
              ` (minimo ${Math.round(data.preset.min_net_edge * 100)} pt)`) : null,
          )),
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "3"), h("div", {},
            h("div", { class: "xstep-title" }, "Tempo"),
            h("p", {}, `Il mercato si risolve tra ${Math.round(ev.days)} giorni. Rendimento annuo prudente ${aprText(ev.apr)}; soglia ${pct(ev.hurdle_apr)} (tasso senza rischio + premio del preset).`),
          )),
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "4"), h("div", {},
            h("div", { class: "xstep-title" }, "Quanto puntare"),
            h("p", { class: "formula" }, `Kelly sul book ${money(ev.kelly_stake)} × ${String(data.preset.kelly_scale).replace(".", ",")} = ${money(ev.target_stake)}`),
            h("table", { class: "caps" }, h("tbody", {}, Object.entries(ev.caps).map(([k, v]) => h("tr", { class: ev.limited_by === k ? "limiting" : "" },
              h("td", {}, CAP_LABELS[k] || k), h("td", { class: "num" }, money(v)),
              h("td", {}, ev.limited_by === k ? h("span", { class: "badge badge-warning" }, "limita") : ""),
            )))),
            h("p", { class: "muted small" }, "Spazio ancora libero per ciascun limite del preset: la puntata è il minimo tra Kelly e questi valori."),
          )),
        ),
      ),
      portfolioBar(ctx, market, data),
    );
  }

  const section = h("section", { class: "card econ", "aria-labelledby": "h-econ" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { id: "h-econ" }, "Cosa fare"),
        h("p", { class: "muted small" }, "Strategia di acquisto e vendita, con prezzi reali, costi, tempo e limiti di rischio."),
      ),
      presetSwitch,
    ),
    body,
  );
  paintSwitch(null);
  load();
  return section;
}

function portfolioBar(ctx, market, data) {
  const admin = ctx.isAdmin();
  const bar = h("div", { class: "econ-portfolio" });
  const ev = data.evaluation;
  const refresh = () => ctx.rerender();

  if (data.open_bet) {
    const b = data.open_bet;
    bar.append(h("p", {}, icon("check"), ` Nel portafoglio simulato: ${fmt.shares(b.shares)} quote ${b.side === "YES" ? "SÌ" : "NO"} a ${fmt.cents(b.avg_price)} (${money(b.outlay)}). `,
      h("a", { href: "#/portafoglio" }, "Vedi il portafoglio")));
  } else if (data.excluded_by) {
    const what = { market: "questo mercato", event: "questo evento", category: `la categoria ${data.market.category}` }[data.excluded_by];
    bar.append(h("p", {}, icon("pause"), ` Escluso dal portafoglio simulato automatico: hai escluso ${what}.`));
  } else if (ev.verdict !== "NO") {
    bar.append(h("p", {}, "Le prossime previsioni con questo esito vengono aggiunte automaticamente al portafoglio simulato."));
  }

  if (admin) {
    const actions = h("div", { class: "form-actions" });
    if (!data.open_bet && ev.verdict !== "NO") {
      const add = h("button", { class: "btn btn-primary btn-sm", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Aggiungi ora al portafoglio simulato");
      add.addEventListener("click", async () => {
        add.disabled = true;
        try {
          await api(`/markets/${encodeURIComponent(market.id)}/paper-bet`, { method: "POST" });
          toast("Scommessa simulata aggiunta");
          refresh();
        } catch (e) { toast(e.message, { error: true }); add.disabled = false; }
      });
      actions.append(add);
    }
    const exclusions = [["market", market.id, "Escludi questo mercato", market.question]];
    if (data.market.event_slug) exclusions.push(["event", data.market.event_slug, "Escludi tutto l'evento", data.market.event_slug]);
    for (const [kind, value, label, desc] of exclusions) {
      const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, label);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await api("/portfolio/exclusions", { method: "POST", body: { kind, value, label: desc } });
          toast("Esclusione salvata: le scommesse automatiche lo salteranno. Le scommesse già aperte restano, puoi escluderle dal portafoglio.");
          refresh();
        } catch (e) { toast(e.message, { error: true }); btn.disabled = false; }
      });
      if (!data.excluded_by) actions.append(btn);
    }
    if (data.excluded_by) actions.append(h("a", { class: "btn btn-ghost btn-sm", href: "#/portafoglio" }, "Gestisci le esclusioni"));
    bar.append(actions);
  }
  return bar;
}
