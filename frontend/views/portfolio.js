// Portafoglio simulato: results, equity curve, positions, preset and exclusions.
import { t } from "../i18n.js";
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
  const [data, bets, exclusions, categories] = await Promise.all([
    api("/portfolio"), api("/portfolio/bets", { params: { status: "all", limit: 500 } }),
    api("/portfolio/exclusions"), api("/sources/categories").catch(() => []),
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
  const marketLink = (b) => h("a", { href: b.multi_event_id ? `#/multi/${encodeURIComponent(b.multi_event_id)}` : `#/mercati/${encodeURIComponent(b.market_id)}` }, b.question);

  const openTable = open.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, t("Mercato")), h("th", {}, t("Lato")), h("th", { class: "num" }, t("Quote")), h("th", { class: "num" }, t("Prezzo medio")),
      h("th", { class: "num" }, t("Costo")), h("th", { class: "num" }, t("Prezzo attuale")), h("th", { class: "num" }, t("Valore")),
      h("th", { class: "num" }, t("Profitto latente")), h("th", {}, t("Piano")), h("th", {}, t("Scade")), admin ? h("th", {}, h("span", { class: "sr-only" }, t("Azioni"))) : null,
    )),
    h("tbody", {}, open.map((b) => h("tr", {},
      h("td", { class: "q-cell" }, marketLink(b)), h("td", {}, sideBadge(b.side)),
      h("td", { class: "num" }, fmt.shares(b.shares)), h("td", { class: "num" }, fmt.cents(b.avg_price)),
      h("td", { class: "num" }, money(b.outlay)), h("td", { class: "num" }, fmt.cents(b.current_price)),
      h("td", { class: "num" }, money(b.current_value)),
      h("td", { class: `num ${pnlCls(b.unrealized_pnl)}` }, fmt.signedMoney(b.unrealized_pnl)),
      h("td", { class: "nowrap" }, planCell(b)),
      h("td", { class: "nowrap" }, fmt.date(b.end_date)),
      admin ? h("td", { class: "row-actions" },
        betAction(b, `/portfolio/bets/${b.id}/sell`, t("Vendi ora"), t("Scommessa venduta al prezzo del book")),
        betAction(b, `/portfolio/bets/${b.id}/exclude`, t("Escludi"), t("Scommessa esclusa dalla simulazione")), excludeMarket(b)) : null,
    ))),
  )) : h("p", { class: "secondary" }, t("Nessuna posizione aperta."));

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
      h("a", { class: "btn btn-ghost", href: "#/metodo" }, icon("help"), t("Come funziona"))),
    h("div", { class: "kpis" },
      statTile(t("Valore attuale"), money(data.total_value), data.roi != null ? t("{0} da capitale {1}", fmt.pts(data.roi).replace(` ${t("pt")}`, "%"), money(s.bankroll)) : ""),
      statTile(t("Profitti realizzati"), fmt.signedMoney(data.realized_pnl), t("{0} vinte · {1} perse{2}{3}", data.counts.won, data.counts.lost, data.counts.sold ? t(" · {0} vendute", data.counts.sold) : "", data.counts.void ? t(" · {0} annullate", data.counts.void) : "")),
      statTile(t("Profitti latenti"), fmt.signedMoney(data.unrealized_pnl), t("{0} aperte · {1} investiti", data.counts.open, money(data.invested))),
      statTile(t("Scommesse vinte"), data.hit_rate != null ? fmt.pct(data.hit_rate) : "–", t("liquidità {0}", money(data.cash))),
      statTile(t("Prezzo di chiusura"), data.clv?.n ? fmt.pts(data.clv.avg) : "–",
        data.clv?.n ? t("{0} comprate sotto la chiusura · {1}", fmt.pct(data.clv.share_positive), fmt.count(data.clv.n, t("mercato chiuso"), t("mercati chiusi")))
          : data.clv_open?.n ? t("finora {0} sulle aperte", fmt.pts(data.clv_open.avg)) : t("nessun mercato chiuso ancora")),
    ),
    !bets.length ? h("p", { class: "note", style: { marginBottom: "16px" } }, icon("alert"),
      s.auto_paper ? t("Ancora nessuna scommessa: la prima arriva con la prossima previsione che supera i controlli economici del preset.")
        : t("Le scommesse automatiche sono disattivate: attivale qui sotto o aggiungile dal dettaglio di un mercato.")) : null,
    h("div", { class: "grid-2", style: { marginBottom: "16px" } }, curveCard, settingsCard(ctx, data, refresh)),
    h("div", { class: "stack" },
      h("section", { class: "card", "aria-labelledby": "h-open" }, h("div", { class: "card-head" }, h("h2", { id: "h-open" }, t("Posizioni aperte ({0})", open.length))), openTable),
      h("section", { class: "card", "aria-labelledby": "h-settled" }, h("div", { class: "card-head" }, h("h2", { id: "h-settled" }, t("Scommesse chiuse ({0})", settled.length))), settledTable),
      excludedList,
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
            t("Kelly ×{0} · max {1}% per mercato · margine ≥ {2} pt · ≤ {3} giorni", fmt.dec(p.kelly_scale), Math.round(p.max_market_frac * 100), Math.round(p.min_net_edge * 100), p.max_days)),
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
      admin ? h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: askReset } }, "Ricomincia…") : null,
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
