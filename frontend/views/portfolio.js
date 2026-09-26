// Portafoglio simulato: results, equity curve, positions, preset and exclusions.
import { t, lang } from "../i18n.js";
import { h, api, fmt, toast, icon, statTile, emptyState, infoTip, CATEGORY_LABELS } from "../ui.js";
import { equityChart } from "../charts.js";

const money = fmt.money;
const sideBadge = (side) => h("span", { class: `badge ${side === "YES" ? "badge-accent" : "badge-outline"}` }, side === "YES" ? t("SÌ") : "NO");
const STATUS = {
  won: ["badge-good", "check", t("Vinta")], lost: ["badge-critical", "x", t("Persa")],
  void: ["badge-outline", "minus", t("Annullata 50-50")], sold: ["badge-accent", "down", t("Venduta")], open: ["badge-outline", "pause", t("Aperta")], excluded: ["", "minus", t("Esclusa")],
};
const statusBadge = (st) => { const [cls, ic, label] = STATUS[st]; return h("span", { class: `badge ${cls}` }, icon(ic), label); };
const pnlCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
// Exit plan of an open bet: hold with a sale target, or sell now
const planCell = (b) => {
  const p = b.plan;
  if (!p) return h("span", { class: "muted small" }, "–");
  if (p.action === "SELL") {
    return h("span", { class: "badge badge-warning", title: p.flipped ? t("La previsione si è girata contro questa posizione") : t("Il prezzo ha raggiunto la stima") },
      icon("down"), p.flipped ? t("Vendi: stima girata") : t("Vendi: obiettivo raggiunto"));
  }
  return h("span", { class: "small", title: t("Ordine limite di vendita: a quel prezzo incassare rende più che aspettare la risoluzione") },
    t("Tieni"), p.sell_above != null ? h("span", { class: "muted" }, t(" · vendi a {0}", fmt.cents(p.sell_above))) : null);
};

export async function viewPortfolio(ctx) {
  const [data, bets, exclusions, categories, pending] = await Promise.all([
    api("/portfolio"), api("/portfolio/bets", { params: { status: "all", limit: 500 } }),
    api("/portfolio/exclusions"), api("/sources/categories").catch(() => []),
    api("/portfolio/orders").catch(() => []),
  ]);
  const admin = ctx.isAdmin();
  const refresh = () => ctx.rerender();
  const s = data.settings;
  const open = bets.filter((b) => b.status === "open");
  const settled = bets.filter((b) => ["won", "lost", "void", "sold"].includes(b.status));
  const excluded = bets.filter((b) => b.status === "excluded");

  const betAction = (bet, path, label, done) => {
    const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, label);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await api(path, { method: "POST" }); toast(done); refresh(); } catch (e) { toast(e.message, { error: true }); btn.disabled = false; }
    });
    return btn;
  };
  const excludeMarket = (bet) => {
    const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button", title: t("Le prossime scommesse automatiche salteranno questo mercato") }, t("Escludi mercato"));
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api("/portfolio/exclusions", { method: "POST", body: { kind: "market", value: bet.market_id, label: bet.question } });
        toast(t("Mercato escluso dalle scommesse automatiche"));
        refresh();
      } catch (e) { toast(e.message, { error: true }); btn.disabled = false; }
    });
    return btn;
  };
  // Manual sale: asks first, and offers to keep the automatic buys off this market, otherwise
  // the next forecast that still says "worth it" would buy it back at a higher price
  const sellNow = (bet) => {
    const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, t("Vendi ora"));
    btn.addEventListener("click", () => {
      const cell = btn.closest("td");
      const previous = [...cell.childNodes];
      const box = h("input", { type: "checkbox", id: `sell-excl-${bet.id}`, checked: true });
      const go = h("button", { class: "btn btn-danger-solid btn-sm", type: "button" }, t("Vendi"));
      const cancel = h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: () => cell.replaceChildren(...previous) } }, t("Annulla"));
      go.addEventListener("click", async () => {
        go.disabled = cancel.disabled = true;
        try {
          await api(`/portfolio/bets/${bet.id}/sell`, { method: "POST" });
          if (box.checked) await api("/portfolio/exclusions", { method: "POST", body: { kind: "market", value: bet.market_id, label: bet.question } });
          toast(box.checked ? t("Scommessa venduta; il mercato è escluso dagli acquisti automatici") : t("Scommessa venduta al prezzo del book"));
          refresh();
        } catch (e) { toast(e.message, { error: true }); go.disabled = cancel.disabled = false; }
      });
      cell.replaceChildren(h("div", { class: "confirm", role: "group", "aria-label": t("Conferma la vendita") },
        h("p", { class: "small" }, t("Vendere {0} quote al miglior prezzo del book, circa {1}?", fmt.shares(bet.shares), money(bet.current_value))),
        h("label", { class: "small", for: `sell-excl-${bet.id}` }, box, " ", t("Escludi il mercato dagli acquisti automatici")),
        h("div", { class: "confirm-actions" }, go, cancel)));
      go.focus();
    });
    return btn;
  };
  const marketLink = (b) => h("a", { href: b.multi_event_id ? `#/multi/${encodeURIComponent(b.multi_event_id)}` : `#/mercati/${encodeURIComponent(b.market_id)}` }, b.question);

  const openTable = open.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, t("Mercato")), h("th", {}, t("Lato")), h("th", { class: "num" }, t("Quote")), h("th", { class: "num" }, t("Prezzo medio")),
      h("th", { class: "num" }, t("Costo")), h("th", { class: "num" }, t("Prezzo di vendita"), infoTip(t("Miglior prezzo di acquisto offerto nel book all'ultimo aggiornamento: quanto si incasserebbe vendendo ora. Valore e profitto latente sono calcolati a questo prezzo, al netto della commissione di vendita; al passaggio del mouse, il prezzo di mercato."))),
      h("th", { class: "num" }, t("Valore")),
      h("th", { class: "num" }, t("Profitto latente")), h("th", {}, t("Piano")), h("th", {}, t("Scade")), admin ? h("th", {}, h("span", { class: "sr-only" }, t("Azioni"))) : null,
    )),
    h("tbody", {}, open.map((b) => h("tr", {},
      h("td", { class: "q-cell" }, marketLink(b)), h("td", {}, sideBadge(b.side)),
      h("td", { class: "num" }, fmt.shares(b.shares)),
      h("td", { class: "num" }, fmt.cents(b.avg_price), b.entry === "maker"
        ? h("div", { class: "muted small", title: t("Comprata con un ordine limite eseguito, senza commissione") }, t("ordine limite")) : null),
      h("td", { class: "num" }, money(b.outlay)), h("td", { class: "num", title: b.current_price != null ? t("Prezzo di mercato {0}", fmt.cents(b.current_price)) : "" }, fmt.cents(b.bid_price)),
      h("td", { class: "num", title: b.mid_value != null ? t("Al prezzo di mercato: {0}", money(b.mid_value)) : "" }, money(b.current_value)),
      h("td", { class: `num ${pnlCls(b.unrealized_pnl)}` }, fmt.signedMoney(b.unrealized_pnl)),
      h("td", { class: "nowrap" }, planCell(b)),
      h("td", { class: "nowrap" }, fmt.date(b.end_date)),
      admin ? h("td", { class: "row-actions" },
        sellNow(b),
        betAction(b, `/portfolio/bets/${b.id}/exclude`, t("Escludi"), t("Scommessa esclusa dalla simulazione")), excludeMarket(b)) : null,
    ))),
  )) : h("p", { class: "secondary" }, t("Nessuna posizione aperta."));

  const os = data.orders || { counts: {}, mode: "taker" };
  const ordersCard = os.mode === "maker" || pending.length || os.counts.filled ? h("section", { class: "card", "aria-labelledby": "h-orders" },
    h("div", { class: "card-head" }, h("h2", { id: "h-orders" }, t("Ordini limite in attesa ({0})", pending.length),
      infoTip(t("Gli acquisti automatici non prendono dal book: aspettano un tick sopra il miglior prezzo di acquisto, senza commissione. Si eseguono se un aggiornamento dei mercati mostra qualcuno che vende a quel prezzo; altrimenti scadono. Il denaro resta riservato finché aspettano."))),
      h("span", { class: "muted small" }, t("{0} eseguiti · {1} scaduti · {2} annullati", os.counts.filled || 0, os.counts.expired || 0, os.counts.cancelled || 0))),
    pending.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, t("Mercato")), h("th", {}, t("Lato")), h("th", { class: "num" }, t("Quote")), h("th", { class: "num" }, t("Prezzo limite")),
        h("th", { class: "num" }, t("Miglior offerta"), infoTip(t("Prezzo più basso a cui qualcuno vende ora: l'ordine si esegue quando arriva al prezzo limite."))),
        h("th", { class: "num" }, t("Riservato")), h("th", {}, t("Scade")), admin ? h("th", {}, h("span", { class: "sr-only" }, t("Azioni"))) : null)),
      h("tbody", {}, pending.map((o) => h("tr", {},
        h("td", { class: "q-cell" }, h("a", { href: `#/mercati/${encodeURIComponent(o.market_id)}` }, o.question)), h("td", {}, sideBadge(o.side)),
        h("td", { class: "num" }, fmt.shares(o.shares)),
        h("td", { class: "num", title: o.taker_price != null ? t("Prendendo dal book: {0}", fmt.cents(o.taker_price)) : "" }, fmt.cents(o.limit_price)),
        h("td", { class: "num" }, fmt.cents(o.best_ask)), h("td", { class: "num" }, money(o.outlay)),
        h("td", { class: "nowrap" }, fmt.dateTime(o.expires_at)),
        admin ? h("td", { class: "row-actions" }, betAction(o, `/portfolio/orders/${o.id}/cancel`, t("Annulla"), t("Ordine annullato"))) : null,
      ))))) : h("p", { class: "secondary" }, t("Nessun ordine in attesa.")),
    os.fill_rate != null || os.saved ? h("p", { class: "muted small", style: { marginTop: "10px" } },
      os.fill_rate != null ? t("Eseguito il {0} degli ordini arrivati a conclusione. ", fmt.pct(os.fill_rate)) : "",
      os.saved ? t("Rispetto al prezzo del book quando sono stati inseriti, gli ordini eseguiti hanno risparmiato {0}.", money(os.saved)) : "") : null,
  ) : null;

  const shadowRows = data.shadow || [];
  const shadowCard = shadowRows.length ? h("section", { class: "card", "aria-labelledby": "h-shadow" },
    h("div", { class: "card-head" }, h("h2", { id: "h-shadow" }, t("Cosa hanno bloccato i filtri"),
      infoTip(t("Ogni scommessa automatica bloccata solo da un filtro viene seguita senza soldi, come se fosse stata comprata al prezzo del book, fino alla risoluzione. Un risultato ipotetico negativo vuol dire che il filtro ha evitato perdite; positivo, che sta costando guadagni."))),
      h("span", { class: "muted small" }, t("scommesse ombra, senza soldi"))),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, t("Filtro")), h("th", { class: "num" }, t("Bloccate")), h("th", { class: "num" }, t("Aperte")),
        h("th", { class: "num" }, t("Vinte / perse")), h("th", { class: "num" }, t("Risultato ipotetico")),
        h("th", { class: "num" }, t("Per dollaro")),
        h("th", { class: "num" }, t("Prezzo dopo il blocco"), infoTip(t("Movimento medio del prezzo del lato che si sarebbe comprato: fino alla chiusura del mercato, o finora. Positivo = il mercato è andato verso il segnale bloccato."))))),
      h("tbody", {}, shadowRows.map((r) => h("tr", {},
        h("td", {}, r.label), h("td", { class: "num" }, fmt.int(r.n)), h("td", { class: "num" }, fmt.int(r.open)),
        h("td", { class: "num" }, `${r.won} / ${r.lost}`),
        h("td", { class: `num ${pnlCls(r.pnl)}` }, r.pnl != null ? fmt.signedMoney(r.pnl) : "–"),
        h("td", { class: `num ${pnlCls(r.roi)}` }, r.roi != null ? fmt.pct(r.roi) : "–"),
        h("td", { class: `num ${pnlCls(r.avg_move)}` }, r.avg_move != null ? fmt.pts(r.avg_move) : "–"),
      ))))),
    h("p", { class: "muted small", style: { marginTop: "10px" } },
      t("Negativo: il filtro ha evitato perdite, tienilo. Positivo su molte scommesse: il filtro costa guadagni, valuta di allentarlo. Con poche scommesse risolte il risultato dipende molto dal caso.")),
  ) : null;

  const settledTable = settled.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, t("Mercato")), h("th", {}, t("Lato")), h("th", { class: "num" }, t("Costo")), h("th", {}, t("Esito")),
      h("th", { class: "num" }, t("Atteso")), h("th", { class: "num" }, t("Risultato")), h("th", {}, t("Chiusa")),
      admin ? h("th", {}, h("span", { class: "sr-only" }, t("Azioni"))) : null,
    )),
    h("tbody", {}, settled.map((b) => h("tr", {},
      h("td", { class: "q-cell" }, marketLink(b)), h("td", {}, sideBadge(b.side)), h("td", { class: "num" }, money(b.outlay)),
      h("td", {}, statusBadge(b.status), b.status === "sold"
        ? h("div", { class: "muted small", title: b.exit_reason === "Venduta a mano" ? t("Venduta a mano") : b.exit_reason || "" }, t("a {0}", fmt.cents(b.exit_price)) + (b.exit_reason === "Venduta a mano" ? t(" · a mano") : "")) : null),
      h("td", { class: `num ${pnlCls(b.expected_profit)}` }, fmt.signedMoney(b.expected_profit)),
      h("td", { class: `num ${pnlCls(b.pnl)}` }, fmt.signedMoney(b.pnl)), h("td", {}, fmt.date(b.settled_at)),
      admin ? h("td", { class: "row-actions" }, betAction(b, `/portfolio/bets/${b.id}/exclude`, t("Escludi"), t("Scommessa esclusa dai risultati"))) : null,
    ))),
  )) : h("p", { class: "secondary" }, t("Nessuna scommessa chiusa: si chiudono quando i mercati si risolvono o quando il piano dice di vendere."));

  const excludedList = excluded.length ? h("details", { class: "card rules" },
    h("summary", {}, t("Scommesse escluse dalla simulazione ({0})", excluded.length)),
    h("ul", { class: "plain-list" }, excluded.map((b) => h("li", {},
      h("span", {}, sideBadge(b.side), " ", marketLink(b), h("span", { class: "muted small" }, ` · ${money(b.outlay)} · ${fmt.date(b.created_at)}`)),
      admin ? betAction(b, `/portfolio/bets/${b.id}/include`, t("Riammetti"), t("Scommessa riammessa")) : null,
    ))),
  ) : null;

  const diff = data.realized_pnl - data.expected_pnl_settled;
  const curveCard = h("section", { class: "card", "aria-labelledby": "h-curve" },
    h("div", { class: "card-head" }, h("h2", { id: "h-curve" }, t("Andamento del capitale")),
      h("span", { class: "muted small" }, t("dal {0}", fmt.date(s.started_at)))),
    data.equity_curve.length > 1 ? equityChart(data.equity_curve, s.bankroll)
      : h("p", { class: "secondary" }, t("La curva compare quando si chiude la prima scommessa.")),
    settled.length ? h("p", { class: "muted small", style: { marginTop: "10px" } },
      t("Sulle {0} scommesse chiuse il modello si aspettava {1}; il risultato reale è {2} ({3} di differenza). ", settled.length, fmt.signedMoney(data.expected_pnl_settled), fmt.signedMoney(data.realized_pnl), fmt.signedMoney(diff)),
      settled.length < 30 ? t("Con meno di 30 scommesse chiuse la differenza dipende molto dal caso.") : "") : null,
  );

  return h("div", {},
    ctx.pageHead(t("Portafoglio simulato"),
      t("Ogni segnale che conviene diventa una scommessa virtuale al prezzo reale del momento, venduta quando il piano lo dice o chiusa quando il mercato si risolve. Serve a capire se i segnali fanno guadagnare prima di usare soldi veri."),
      exportLinks(),
      h("a", { class: "btn btn-ghost", href: "#/metodo" }, icon("help"), t("Come funziona"))),
    h("div", { class: "kpis" },
      statTile(t("Valore attuale"), money(data.total_value), data.roi != null ? t("{0} da capitale {1}", fmt.pts(data.roi).replace(` ${t("pt")}`, "%"), money(s.bankroll)) : ""),
      statTile(t("Profitti realizzati"), fmt.signedMoney(data.realized_pnl), t("{0} vinte · {1} perse{2}{3}", data.counts.won, data.counts.lost, data.counts.sold ? t(" · {0} vendute", data.counts.sold) : "", data.counts.void ? t(" · {0} annullate", data.counts.void) : "")),
      statTile(h("span", {}, t("Profitti latenti"), infoTip(t("Vendendo ora le posizioni aperte al miglior prezzo offerto, commissione compresa. Al prezzo di mercato sarebbero {0}.", fmt.signedMoney(data.unrealized_pnl_mid)))),
        fmt.signedMoney(data.unrealized_pnl), t("{0} aperte · {1} investiti", data.counts.open, money(data.invested))),
      statTile(t("Scommesse vinte"), data.hit_rate != null ? fmt.pct(data.hit_rate) : "–",
        t("liquidità {0}", money(data.cash)) + (data.orders?.reserved ? t(" · {0} negli ordini", money(data.orders.reserved)) : "")),
      statTile(t("Prezzo di chiusura"), data.clv?.n ? fmt.pts(data.clv.avg) : "–",
        data.clv?.n ? t("{0} comprate sotto la chiusura · {1}", fmt.pct(data.clv.share_positive), fmt.count(data.clv.n, t("mercato chiuso"), t("mercati chiusi")))
          : data.clv_open?.n ? t("finora {0} sulle aperte", fmt.pts(data.clv_open.avg)) : t("nessun mercato chiuso ancora")),
    ),
    data.guard?.paused ? h("div", { class: "notice", role: "alert", style: { marginBottom: "16px" } }, icon("alert"),
      h("div", {}, h("p", {}, h("b", {}, t("Scommesse automatiche in pausa. ")), data.guard.reason || ""),
        h("p", { class: "small" }, t("Il prezzo si muove contro le scommesse recenti: i segnali non stanno anticipando il mercato. Riprendi quando hai cambiato qualcosa (calibrazione, preset, categorie): le scommesse fatte finora non verranno ricontate.")),
        ctx.isAdmin() ? h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("/portfolio/guard/resume", { method: "POST" }); toast(t("Scommesse automatiche riprese")); ctx.rerender(); }
          catch (err) { toast(err.message, { error: true }); e.currentTarget.disabled = false; }
        } } }, t("Riprendi le scommesse automatiche")) : null)) : null,
    !bets.length ? h("p", { class: "note", style: { marginBottom: "16px" } }, icon("alert"),
      s.auto_paper ? t("Ancora nessuna scommessa: la prima arriva con la prossima previsione che supera i controlli economici del preset.")
        : t("Le scommesse automatiche sono disattivate: attivale qui sotto o aggiungile dal dettaglio di un mercato.")) : null,
    h("div", { class: "grid-2", style: { marginBottom: "16px" } }, curveCard, settingsCard(ctx, data, refresh)),
    h("div", { class: "stack" },
      ordersCard,
      h("section", { class: "card", "aria-labelledby": "h-open" }, h("div", { class: "card-head" }, h("h2", { id: "h-open" }, t("Posizioni aperte ({0})", open.length))), openTable),
      h("section", { class: "card", "aria-labelledby": "h-settled" }, h("div", { class: "card-head" }, h("h2", { id: "h-settled" }, t("Scommesse chiuse ({0})", settled.length))), settledTable),
      excludedList,
      shadowCard,
      exclusionsCard(ctx, exclusions, categories, refresh),
    ),
  );
}

function settingsCard(ctx, data, refresh) {
  const admin = ctx.isAdmin();
  const s = data.settings;
  const presetCards = h("div", { class: "presets", role: "radiogroup", "aria-label": t("Preset di rischio") },
    data.presets.map((p) => {
      const input = h("input", { type: "radio", name: "preset", value: p.key, id: `preset-${p.key}`, checked: p.key === s.preset, disabled: !admin });
      input.addEventListener("change", async () => {
        try { await api("/portfolio/settings", { method: "PUT", body: { preset: p.key } }); toast(t("Preset {0} attivo per le prossime scommesse", p.label.toLowerCase())); refresh(); }
        catch (e) { toast(e.message, { error: true }); }
      });
      return h("label", { class: "preset", for: `preset-${p.key}` }, input,
        h("span", { class: "preset-body" },
          h("span", { class: "preset-name" }, p.label),
          h("span", { class: "preset-desc" }, p.description),
          h("span", { class: "preset-params mono" },
            t("Kelly ×{0} · max {1}% per mercato · margine ≥ {2} pt · rendimento ≥ {3}% · da {4} ore a {5} giorni dalla scadenza", fmt.dec(p.kelly_scale), Math.round(p.max_market_frac * 100), Math.round(p.min_net_edge * 100), Math.round((p.min_roi ?? 0) * 100), p.min_hours_to_end ?? "–", p.max_days)),
        ));
    }));

  const auto = h("input", { type: "checkbox", class: "switch", id: "auto-paper", checked: s.auto_paper, disabled: !admin });
  auto.addEventListener("change", async () => {
    try { await api("/portfolio/settings", { method: "PUT", body: { auto_paper: auto.checked } }); toast(auto.checked ? t("Scommesse automatiche attivate") : t("Scommesse automatiche disattivate")); }
    catch (e) { auto.checked = !auto.checked; toast(e.message, { error: true }); }
  });

  const autoSell = h("input", { type: "checkbox", class: "switch", id: "auto-sell", checked: s.auto_sell !== false, disabled: !admin });
  autoSell.addEventListener("change", async () => {
    try { await api("/portfolio/settings", { method: "PUT", body: { auto_sell: autoSell.checked } }); toast(autoSell.checked ? t("Vendite automatiche attivate") : t("Vendite automatiche disattivate: le posizioni restano aperte fino alla risoluzione")); }
    catch (e) { autoSell.checked = !autoSell.checked; toast(e.message, { error: true }); }
  });

  const resetArea = h("div", {});
  const paintReset = () => resetArea.replaceChildren(
    h("div", { class: "reset-row" },
      h("span", {}, t("Capitale iniziale "), h("b", { class: "mono" }, money(s.bankroll))),
      admin ? h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: askReset } }, t("Ricomincia…")) : null,
    ));
  function askReset() {
    const amount = h("input", { id: "reset-amount", class: "input", type: "number", min: "10", step: "10", value: String(s.bankroll), style: { maxWidth: "160px" } });
    const go = h("button", { class: "btn btn-danger-solid btn-sm", type: "button" }, t("Cancella e ricomincia"));
    go.addEventListener("click", async () => {
      const bankroll = Number(amount.value);
      if (!(bankroll >= 10)) return toast(t("Il capitale deve essere almeno 10 $"), { error: true });
      go.disabled = true;
      try { await api("/portfolio/reset", { method: "POST", body: { bankroll } }); toast(t("Portafoglio azzerato")); refresh(); }
      catch (e) { toast(e.message, { error: true }); go.disabled = false; }
    });
    resetArea.replaceChildren(h("div", { class: "confirm", role: "alert", style: { maxWidth: "none" } },
      h("label", { class: "field", for: "reset-amount" }, t("Nuovo capitale ($)"), amount),
      h("p", { class: "small" }, t("Tutte le scommesse simulate verranno cancellate. Le esclusioni restano.")),
      h("div", { class: "confirm-actions" }, go, h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: paintReset } }, t("Annulla"))),
    ));
    amount.focus();
  }
  paintReset();

  return h("section", { class: "card", "aria-labelledby": "h-psettings" },
    h("h2", { id: "h-psettings", style: { marginBottom: "10px" } }, t("Impostazioni della simulazione")),
    presetCards,
    h("label", { class: "field", for: "auto-paper", style: { margin: "14px 0 6px" } }, auto, t("Aggiungi automaticamente ogni scommessa che conviene")),
    h("label", { class: "field", for: "auto-sell", style: { margin: "0 0 6px" } }, autoSell,
      t("Vendi automaticamente quando il piano lo dice (prezzo arrivato alla stima, o previsione girata)")),
    resetArea,
    h("p", { class: "muted small", style: { marginTop: "10px" } },
      t("Tasso senza rischio {0}. ", fmt.pct(data.risk_free_rate)),
      data.orders?.mode === "maker" ? t("Acquisti automatici con ordini limite (maker), a mano al prezzo del book. ") : t("Acquisti automatici al prezzo del book (taker). "),
      data.guard?.enabled ? t("Controllo del prezzo di chiusura: sulle ultime {0} scommesse il prezzo si è mosso in media di {1}; sotto {2} le scommesse automatiche si fermano (servono almeno {3} scommesse). ",
        data.guard.n, data.guard.avg_move != null ? fmt.pts(data.guard.avg_move) : "–", fmt.pts(data.guard.threshold), data.guard.min_bets) : "",
      data.calibration_factor !== 1 ? t("Incertezza corretta ×{0} in base ai risultati passati.", fmt.dec(data.calibration_factor, 2))
        : t("L'incertezza verrà corretta con i risultati reali dopo 30 mercati risolti."),
      infoTip(t("Se le previsioni passate sono state peggiori del prezzo di mercato, l'incertezza della stima viene allargata e le puntate si riducono (e viceversa).")),
    ),
  );
}

function exclusionsCard(ctx, exclusions, categories, refresh) {
  const admin = ctx.isAdmin();
  const KIND = { market: t("Mercato"), event: t("Evento"), category: t("Categoria") };
  const list = exclusions.length ? h("ul", { class: "plain-list" }, exclusions.map((x) => h("li", {},
    h("span", {}, h("span", { class: "badge badge-outline" }, KIND[x.kind]), " ",
      x.kind === "category" ? (CATEGORY_LABELS[x.value] || x.value) : x.kind === "market"
        ? h("a", { href: `#/mercati/${encodeURIComponent(x.value)}` }, x.label || x.value) : (x.label || x.value)),
    admin ? h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: async (e) => {
      e.currentTarget.disabled = true;
      try { await api(`/portfolio/exclusions/${x.id}`, { method: "DELETE" }); toast(t("Esclusione rimossa")); refresh(); } catch (err) { toast(err.message, { error: true }); }
    } } }, t("Rimuovi")) : null,
  ))) : h("p", { class: "secondary" }, t("Nessuna esclusione: tutte le scommesse che convengono entrano nella simulazione."));

  let adder = null;
  if (admin) {
    const taken = new Set(exclusions.filter((x) => x.kind === "category").map((x) => x.value));
    const options = categories.filter((c) => !taken.has(c));
    if (options.length) {
      const select = h("select", { id: "excl-cat", class: "select" }, options.map((c) => h("option", { value: c }, CATEGORY_LABELS[c] || c)));
      const add = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, icon("plus"), t("Escludi categoria"));
      add.addEventListener("click", async () => {
        add.disabled = true;
        try {
          await api("/portfolio/exclusions", { method: "POST", body: { kind: "category", value: select.value, label: CATEGORY_LABELS[select.value] || select.value } });
          toast(t("Categoria esclusa dalle scommesse automatiche")); refresh();
        } catch (e) { toast(e.message, { error: true }); add.disabled = false; }
      });
      adder = h("div", { class: "form-inline", style: { marginTop: "12px" } }, h("label", { class: "field", for: "excl-cat" }, t("Categoria"), select), add);
    }
  }

  return h("section", { class: "card", "aria-labelledby": "h-excl" },
    h("div", { class: "card-head" }, h("div", {},
      h("h2", { id: "h-excl" }, t("Esclusioni")),
      h("p", { class: "muted small" }, t("Le scommesse automatiche saltano questi mercati, eventi e categorie. Mercati ed eventi si escludono dal loro dettaglio o dalle posizioni aperte.")),
    )),
    list, adder,
  );
}

/** Download of the whole portfolio as it is now: Excel workbook, or the bets as CSV. */
function exportLinks() {
  const url = (format) => new URL(`/portfolio/export?format=${format}&lang=${lang}`, window.location.origin).href;
  return h("div", { class: "export-links", role: "group", "aria-label": t("Esporta il portafoglio") },
    h("a", { class: "btn btn-ghost", href: url("xlsx"), download: "",
      title: t("Riepilogo, scommesse con tutti i dettagli, curva del capitale ed esclusioni, in un file Excel (si apre anche con Google Sheets e LibreOffice)") },
      icon("download"), t("Esporta Excel")),
    h("a", { class: "btn btn-ghost", href: url("csv"), download: "", title: t("Solo le scommesse, in CSV (separatore virgola, decimali con il punto)") }, t("CSV")));
}
