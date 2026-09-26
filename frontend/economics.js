// "Conviene?" card: live economic evaluation of the latest forecast of a market.
import { t } from "./i18n.js";
import { h, api, fmt, toast, icon, infoTip } from "./ui.js";
import { strategySection } from "./strategy.js";

export const VERDICTS = {
  GO: { cls: "badge-good", icon: "check", label: t("Conviene") },
  SMALL: { cls: "badge-warning", icon: "alert", label: t("Conviene poco") },
  NO: { cls: "badge-critical", icon: "x", label: t("Non conviene") },
};

export const ECON_HELP = {
  stake: t("Cifra da investire in quote, calcolata con il criterio di Kelly sul book reale e poi ridotta dai limiti del preset."),
  limit: t("Prezzo massimo per quota oltre il quale la scommessa non conviene più: usalo come prezzo limite dell'ordine."),
  apr: t("Rendimento atteso riportato su base annua con la probabilità prudente: confronta scommesse con scadenze diverse."),
  prudent: t("Probabilità del lato scelto meno l'incertezza della stima (z × σ). Le decisioni usano questa, non quella centrale."),
  breakeven: t("Probabilità minima di vittoria perché la scommessa vada in pari, commissioni comprese."),
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
  return apr >= 10 ? t("oltre 1000%") : pct(apr);
}

const CAP_LABELS = {
  market: t("Per mercato"), event: t("Per evento"), category: t("Per categoria"), total: t("Totale investito"), cash: t("Liquidità disponibile"), book: t("Profondità del book"),
};

export function economicsCard(ctx, market) {
  const body = h("div", { class: "econ-body" }, h("div", { class: "skeleton", style: { height: "140px" } }));
  let preset = null;

  const PRESET_LABELS = { prudente: t("Prudente"), bilanciato: t("Bilanciato"), aggressivo: t("Aggressivo") };
  const presetSwitch = h("div", { class: "segmented", role: "group", "aria-label": t("Preset di rischio") });
  const paintSwitch = (active) => presetSwitch.replaceChildren(...["prudente", "bilanciato", "aggressivo"].map((key) => h("button", {
    type: "button", class: "seg", "aria-pressed": String(key === active),
    on: { click: () => { preset = key; paintSwitch(key); load(); } },
  }, PRESET_LABELS[key])));

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
        ? h("p", { class: "secondary" }, t("Serve prima una previsione di Jev."))
        : h("div", { class: "econ-error", role: "alert" },
          h("p", { class: "secondary" }, t("Valutazione non disponibile: {0}", e.message)),
          h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: load } }, icon("refresh"), t("Riprova"))));
    } finally {
      body.removeAttribute("aria-busy");
      body.classList.remove("is-loading");
    }
  }

  function render(data) {
    const ev = data.evaluation;
    const blocking = ev.reasons.filter((r) => r.blocking);
    const infoReasons = ev.reasons.filter((r) => !r.blocking);
    const side = ev.side === "YES" ? t("SÌ") : "NO";
    const isGo = ev.verdict !== "NO";
    // With a position already open, a full market cap means "hold what you have", not "bad bet"
    const holding = data.open_bet && blocking.every((r) => r.code === "exposure_cap");
    const b = data.open_bet;

    const headline = holding
      ? t("Hai già {0} quote {1} ({2}): il limite del preset per questo mercato è raggiunto, non conviene aggiungere.", fmt.shares(b.shares), b.side === "YES" ? t("SÌ") : "NO", money(b.outlay))
      : isGo
        ? t("{0} {1} quote {2} per {3}, a non più di {4} l'una.", data.open_bet ? t("Puoi aggiungere") : t("Compra"), fmt.shares(ev.shares), side, money(ev.outlay), fmt.cents(ev.limit_price))
        : t("Con il preset scelto questa scommessa non conviene.");

    return h("div", {},
      data.strategy ? strategySection(data.strategy, data.market.yes_price) : null,
      data.strategy ? h("h3", { class: "econ-sub" }, t("I conti della scommessa")) : null,
      h("div", { class: "econ-head" },
        holding ? h("span", { class: "badge badge-accent badge-lg" }, icon("check"), t("In portafoglio")) : verdictBadge(ev.verdict, true),
        h("p", { class: "econ-headline" }, headline),
      ),
      blocking.length && !holding ? h("ul", { class: "reasons" }, blocking.map((r) => h("li", {}, icon("x"), h("span", {}, r.text)))) : null,
      infoReasons.length ? h("ul", { class: "reasons info" }, infoReasons.map((r) => h("li", {}, icon("alert"), h("span", {}, r.text)))) : null,
      ev.notes.map((n) => h("p", { class: "note", style: { marginTop: "8px" } }, icon("alert"), n)),

      h("div", { class: "econ-grid" },
        figure(t("Puntata"), isGo ? money(ev.outlay) : "–", ECON_HELP.stake),
        figure(t("Prezzo medio"), ev.avg_price != null ? fmt.cents(ev.avg_price) : ev.best_price != null ? t("{0} (migliore)", fmt.cents(ev.best_price)) : "–"),
        // Without a signal the plan's price levels apply, not this maximum
        figure(t("Prezzo massimo"), ev.reasons.some((r) => r.code === "no_signal") ? "–" : fmt.cents(ev.limit_price), ECON_HELP.limit),
        figure(t("Se vince"), isGo ? fmt.signedMoney(ev.profit_if_win) : "–", null, "pos"),
        figure(t("Se perde"), isGo ? fmt.signedMoney(-ev.outlay) : "–", null, "neg"),
        figure(t("Profitto atteso"), isGo ? fmt.signedMoney(ev.expected_profit) : "–", null, ev.expected_profit > 0 ? "pos" : ""),
        figure(t("Rendimento annuo (prudente)"), aprText(ev.apr), ECON_HELP.apr),
        figure(t("Probabilità di perdere"), pct(ev.prob_loss)),
        figure(t("Pareggio sopra"), ev.break_even != null ? pct(ev.break_even) : "–", ECON_HELP.breakeven),
      ),

      h("details", { class: "econ-details" },
        h("summary", {}, t("Come è calcolato")),
        h("ol", { class: "xsteps", style: { marginTop: "12px" } },
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "1"), h("div", {},
            h("div", { class: "xstep-title" }, t("Probabilità prudente"), infoTip(ECON_HELP.prudent)),
            h("p", { class: "formula" }, t("{0} − {1} × {2} = ", pct(ev.p_side), fmt.dec(data.preset.z), pct(ev.sigma)), h("b", {}, pct(ev.p_conservative))),
            h("p", { class: "muted small" }, t("Probabilità che vinca il {0} secondo la stima blended, meno l'incertezza della stima.", side)),
          )),
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "2"), h("div", {},
            h("div", { class: "xstep-title" }, t("Costi reali")),
            h("p", {}, ev.quote_source === "book" ? t("Prezzi presi dal book di Polymarket in questo momento.") : t("Book non disponibile: prezzo stimato.")),
            h("p", { class: "formula" }, t("Prezzo medio {0} (a metà mercato {1}), commissioni {2}", ev.avg_price != null ? fmt.cents(ev.avg_price) : fmt.cents(ev.best_price), fmt.cents(ev.mid), money(ev.fee))),
            ev.net_edge != null ? h("p", { class: "formula" }, t("Margine netto: {0} − costo per quota = ", pct(ev.p_conservative)), h("b", {}, fmt.pts(ev.net_edge)),
              t(" (minimo {0} pt)", Math.round(data.preset.min_net_edge * 100))) : null,
          )),
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "3"), h("div", {},
            h("div", { class: "xstep-title" }, t("Tempo")),
            h("p", {}, t("Il mercato si risolve tra {0} giorni. Rendimento annuo prudente {1}; soglia {2} (tasso senza rischio + premio del preset).", Math.round(ev.days), aprText(ev.apr), pct(ev.hurdle_apr))),
          )),
          h("li", { class: "xstep" }, h("span", { class: "xstep-n" }, "4"), h("div", {},
            h("div", { class: "xstep-title" }, t("Quanto puntare")),
            h("p", { class: "formula" }, t("Kelly sul book {0} × {1} = {2}", money(ev.kelly_stake), fmt.dec(data.preset.kelly_scale), money(ev.target_stake))),
            h("table", { class: "caps" }, h("tbody", {}, Object.entries(ev.caps).map(([k, v]) => h("tr", { class: ev.limited_by === k ? "limiting" : "" },
              h("td", {}, CAP_LABELS[k] || k), h("td", { class: "num" }, money(v)),
              h("td", {}, ev.limited_by === k ? h("span", { class: "badge badge-warning" }, "limita") : ""),
            )))),
            h("p", { class: "muted small" }, t("Spazio ancora libero per ciascun limite del preset: la puntata è il minimo tra Kelly e questi valori.")),
          )),
        ),
      ),
      portfolioBar(ctx, market, data),
    );
  }

  const section = h("section", { class: "card econ", "aria-labelledby": "h-econ" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { id: "h-econ" }, t("Cosa fare")),
        h("p", { class: "muted small" }, t("Strategia di acquisto e vendita, con prezzi reali, costi, tempo e limiti di rischio.")),
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
    bar.append(h("p", {}, icon("check"), t(" Nel portafoglio simulato: {0} quote {1} a {2} ({3}). ", fmt.shares(b.shares), b.side === "YES" ? t("SÌ") : "NO", fmt.cents(b.avg_price), money(b.outlay)),
      h("a", { href: "#/portafoglio" }, t("Vedi il portafoglio"))));
  } else if (data.pending_orders?.length) {
    const o = data.pending_orders[0];
    bar.append(h("p", {}, icon("pause"), t(" Ordine limite in attesa: {0} quote {1} a {2} ({3}), fino a {4}. ", fmt.shares(o.shares), o.side === "YES" ? t("SÌ") : "NO", fmt.cents(o.limit_price), money(o.outlay), fmt.dateTime(o.expires_at)),
      h("a", { href: "#/portafoglio" }, t("Vedi il portafoglio"))));
  } else if (data.excluded_by) {
    const what = { market: t("questo mercato"), event: t("questo evento"), category: t("la categoria {0}", data.market.category) }[data.excluded_by];
    bar.append(h("p", {}, icon("pause"), t(" Escluso dal portafoglio simulato automatico: hai escluso {0}.", what)));
  } else if (ev.verdict !== "NO") {
    bar.append(h("p", {}, data.maker_price != null
      ? t("Le prossime previsioni con questo esito diventano un ordine limite nel portafoglio simulato, a {0}: si compra se qualcuno vende a quel prezzo.", fmt.cents(data.maker_price))
      : t("Le prossime previsioni con questo esito vengono aggiunte automaticamente al portafoglio simulato.")));
  }

  if (admin) {
    const actions = h("div", { class: "form-actions" });
    if (!data.open_bet && ev.verdict !== "NO") {
      const add = h("button", { class: "btn btn-primary btn-sm", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), t("Aggiungi ora al portafoglio simulato"));
      add.addEventListener("click", async () => {
        add.disabled = true;
        try {
          await api(`/markets/${encodeURIComponent(market.id)}/paper-bet`, { method: "POST" });
          toast(data.pending_orders?.length ? t("Scommessa simulata aggiunta al prezzo del book; l'ordine limite è annullato") : t("Scommessa simulata aggiunta"));
          refresh();
        } catch (e) { toast(e.message, { error: true }); add.disabled = false; }
      });
      actions.append(add);
    }
    const exclusions = [["market", market.id, t("Escludi questo mercato"), market.question]];
    if (data.market.event_slug) exclusions.push(["event", data.market.event_slug, t("Escludi tutto l'evento"), data.market.event_slug]);
    for (const [kind, value, label, desc] of exclusions) {
      const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, label);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await api("/portfolio/exclusions", { method: "POST", body: { kind, value, label: desc } });
          toast(t("Esclusione salvata: le scommesse automatiche lo salteranno. Le scommesse già aperte restano, puoi escluderle dal portafoglio."));
          refresh();
        } catch (e) { toast(e.message, { error: true }); btn.disabled = false; }
      });
      if (!data.excluded_by) actions.append(btn);
    }
    if (data.excluded_by) actions.append(h("a", { class: "btn btn-ghost btn-sm", href: "#/portafoglio" }, t("Gestisci le esclusioni")));
    bar.append(actions);
  }
  return bar;
}
