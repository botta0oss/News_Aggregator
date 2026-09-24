// Portafoglio simulato: results, equity curve, positions, preset and exclusions.
import { h, api, fmt, toast, icon, statTile, emptyState, infoTip, CATEGORY_LABELS } from "../ui.js";
import { equityChart } from "../charts.js";

const money = fmt.money;
const sideBadge = (side) => h("span", { class: `badge ${side === "YES" ? "badge-accent" : "badge-outline"}` }, side === "YES" ? "SÌ" : "NO");
const STATUS = {
  won: ["badge-good", "check", "Vinta"], lost: ["badge-critical", "x", "Persa"],
  open: ["badge-outline", "pause", "Aperta"], excluded: ["", "minus", "Esclusa"],
};
const statusBadge = (st) => { const [cls, ic, label] = STATUS[st]; return h("span", { class: `badge ${cls}` }, icon(ic), label); };
const pnlCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

export async function viewPortfolio(ctx) {
  const [data, bets, exclusions, categories] = await Promise.all([
    api("/portfolio"), api("/portfolio/bets", { params: { status: "all", limit: 500 } }),
    api("/portfolio/exclusions"), api("/sources/categories").catch(() => []),
  ]);
  const admin = ctx.isAdmin();
  const refresh = () => ctx.rerender();
  const s = data.settings;
  const open = bets.filter((b) => b.status === "open");
  const settled = bets.filter((b) => b.status === "won" || b.status === "lost");
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
    const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button", title: "Le prossime scommesse automatiche salteranno questo mercato" }, "Escludi mercato");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api("/portfolio/exclusions", { method: "POST", body: { kind: "market", value: bet.market_id, label: bet.question } });
        toast("Mercato escluso dalle scommesse automatiche");
        refresh();
      } catch (e) { toast(e.message, { error: true }); btn.disabled = false; }
    });
    return btn;
  };
  const marketLink = (b) => h("a", { href: `#/mercati/${encodeURIComponent(b.market_id)}` }, b.question);

  const openTable = open.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, "Mercato"), h("th", {}, "Lato"), h("th", { class: "num" }, "Quote"), h("th", { class: "num" }, "Prezzo medio"),
      h("th", { class: "num" }, "Costo"), h("th", { class: "num" }, "Prezzo attuale"), h("th", { class: "num" }, "Valore"),
      h("th", { class: "num" }, "Profitto latente"), h("th", {}, "Scade"), admin ? h("th", {}, h("span", { class: "sr-only" }, "Azioni")) : null,
    )),
    h("tbody", {}, open.map((b) => h("tr", {},
      h("td", { class: "q-cell" }, marketLink(b)), h("td", {}, sideBadge(b.side)),
      h("td", { class: "num" }, fmt.shares(b.shares)), h("td", { class: "num" }, fmt.cents(b.avg_price)),
      h("td", { class: "num" }, money(b.outlay)), h("td", { class: "num" }, fmt.cents(b.current_price)),
      h("td", { class: "num" }, money(b.current_value)),
      h("td", { class: `num ${pnlCls(b.unrealized_pnl)}` }, fmt.signedMoney(b.unrealized_pnl)),
      h("td", { class: "nowrap" }, fmt.date(b.end_date)),
      admin ? h("td", { class: "row-actions" },
        betAction(b, `/portfolio/bets/${b.id}/exclude`, "Escludi", "Scommessa esclusa dalla simulazione"), excludeMarket(b)) : null,
    ))),
  )) : h("p", { class: "secondary" }, "Nessuna posizione aperta.");

  const settledTable = settled.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", {}, "Mercato"), h("th", {}, "Lato"), h("th", { class: "num" }, "Costo"), h("th", {}, "Esito"),
      h("th", { class: "num" }, "Atteso"), h("th", { class: "num" }, "Risultato"), h("th", {}, "Chiusa"),
      admin ? h("th", {}, h("span", { class: "sr-only" }, "Azioni")) : null,
    )),
    h("tbody", {}, settled.map((b) => h("tr", {},
      h("td", { class: "q-cell" }, marketLink(b)), h("td", {}, sideBadge(b.side)), h("td", { class: "num" }, money(b.outlay)),
      h("td", {}, statusBadge(b.status)), h("td", { class: `num ${pnlCls(b.expected_profit)}` }, fmt.signedMoney(b.expected_profit)),
      h("td", { class: `num ${pnlCls(b.pnl)}` }, fmt.signedMoney(b.pnl)), h("td", {}, fmt.date(b.settled_at)),
      admin ? h("td", { class: "row-actions" }, betAction(b, `/portfolio/bets/${b.id}/exclude`, "Escludi", "Scommessa esclusa dai risultati")) : null,
    ))),
  )) : h("p", { class: "secondary" }, "Nessuna scommessa chiusa: si chiudono quando i mercati si risolvono.");

  const excludedList = excluded.length ? h("details", { class: "card rules" },
    h("summary", {}, `Scommesse escluse dalla simulazione (${excluded.length})`),
    h("ul", { class: "plain-list" }, excluded.map((b) => h("li", {},
      h("span", {}, sideBadge(b.side), " ", marketLink(b), h("span", { class: "muted small" }, ` · ${money(b.outlay)} · ${fmt.date(b.created_at)}`)),
      admin ? betAction(b, `/portfolio/bets/${b.id}/include`, "Riammetti", "Scommessa riammessa") : null,
    ))),
  ) : null;

  const diff = data.realized_pnl - data.expected_pnl_settled;
  const curveCard = h("section", { class: "card", "aria-labelledby": "h-curve" },
    h("div", { class: "card-head" }, h("h2", { id: "h-curve" }, "Andamento del capitale"),
      h("span", { class: "muted small" }, `dal ${fmt.date(s.started_at)}`)),
    data.equity_curve.length > 1 ? equityChart(data.equity_curve, s.bankroll)
      : h("p", { class: "secondary" }, "La curva compare quando si chiude la prima scommessa."),
    settled.length ? h("p", { class: "muted small", style: { marginTop: "10px" } },
      `Sulle ${settled.length} scommesse chiuse il modello si aspettava ${fmt.signedMoney(data.expected_pnl_settled)}; il risultato reale è ${fmt.signedMoney(data.realized_pnl)} (${fmt.signedMoney(diff)} di differenza). `,
      settled.length < 30 ? "Con meno di 30 scommesse chiuse la differenza dipende molto dal caso." : "") : null,
  );

  return h("div", {},
    ctx.pageHead("Portafoglio simulato",
      "Ogni segnale che conviene diventa una scommessa virtuale al prezzo reale del momento, chiusa quando il mercato si risolve. Serve a capire se i segnali fanno guadagnare prima di usare soldi veri.",
      h("a", { class: "btn btn-ghost", href: "#/metodo" }, icon("help"), "Come funziona")),
    h("div", { class: "kpis" },
      statTile("Valore attuale", money(data.total_value), data.roi != null ? `${fmt.pts(data.roi).replace(" pt", "%")} da capitale ${money(s.bankroll)}` : ""),
      statTile("Profitti realizzati", fmt.signedMoney(data.realized_pnl), `${data.counts.won} vinte · ${data.counts.lost} perse`),
      statTile("Profitti latenti", fmt.signedMoney(data.unrealized_pnl), `${data.counts.open} aperte · ${money(data.invested)} investiti`),
      statTile("Scommesse vinte", data.hit_rate != null ? fmt.pct(data.hit_rate) : "–", `liquidità ${money(data.cash)}`),
    ),
    !bets.length ? h("p", { class: "note", style: { marginBottom: "16px" } }, icon("alert"),
      s.auto_paper ? "Ancora nessuna scommessa: la prima arriva con la prossima previsione che supera i controlli economici del preset."
        : "Le scommesse automatiche sono disattivate: attivale qui sotto o aggiungile dal dettaglio di un mercato.") : null,
    h("div", { class: "grid-2", style: { marginBottom: "16px" } }, curveCard, settingsCard(ctx, data, refresh)),
    h("div", { class: "stack" },
      h("section", { class: "card", "aria-labelledby": "h-open" }, h("div", { class: "card-head" }, h("h2", { id: "h-open" }, `Posizioni aperte (${open.length})`)), openTable),
      h("section", { class: "card", "aria-labelledby": "h-settled" }, h("div", { class: "card-head" }, h("h2", { id: "h-settled" }, `Scommesse chiuse (${settled.length})`)), settledTable),
      excludedList,
      exclusionsCard(ctx, exclusions, categories, refresh),
    ),
  );
}

function settingsCard(ctx, data, refresh) {
  const admin = ctx.isAdmin();
  const s = data.settings;
  const presetCards = h("div", { class: "presets", role: "radiogroup", "aria-label": "Preset di rischio" },
    data.presets.map((p) => {
      const input = h("input", { type: "radio", name: "preset", value: p.key, id: `preset-${p.key}`, checked: p.key === s.preset, disabled: !admin });
      input.addEventListener("change", async () => {
        try { await api("/portfolio/settings", { method: "PUT", body: { preset: p.key } }); toast(`Preset ${p.label.toLowerCase()} attivo per le prossime scommesse`); refresh(); }
        catch (e) { toast(e.message, { error: true }); }
      });
      return h("label", { class: "preset", for: `preset-${p.key}` }, input,
        h("span", { class: "preset-body" },
          h("span", { class: "preset-name" }, p.label),
          h("span", { class: "preset-desc" }, p.description),
          h("span", { class: "preset-params mono" },
            `Kelly ×${String(p.kelly_scale).replace(".", ",")} · max ${Math.round(p.max_market_frac * 100)}% per mercato · margine ≥ ${Math.round(p.min_net_edge * 100)} pt · ≤ ${p.max_days} giorni`),
        ));
    }));

  const auto = h("input", { type: "checkbox", class: "switch", id: "auto-paper", checked: s.auto_paper, disabled: !admin });
  auto.addEventListener("change", async () => {
    try { await api("/portfolio/settings", { method: "PUT", body: { auto_paper: auto.checked } }); toast(auto.checked ? "Scommesse automatiche attivate" : "Scommesse automatiche disattivate"); }
    catch (e) { auto.checked = !auto.checked; toast(e.message, { error: true }); }
  });

  const resetArea = h("div", {});
  const paintReset = () => resetArea.replaceChildren(
    h("div", { class: "reset-row" },
      h("span", {}, "Capitale iniziale ", h("b", { class: "mono" }, money(s.bankroll))),
      admin ? h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: askReset } }, "Ricomincia…") : null,
    ));
  function askReset() {
    const amount = h("input", { id: "reset-amount", class: "input", type: "number", min: "10", step: "10", value: String(s.bankroll), style: { maxWidth: "160px" } });
    const go = h("button", { class: "btn btn-danger-solid btn-sm", type: "button" }, "Cancella e ricomincia");
    go.addEventListener("click", async () => {
      const bankroll = Number(amount.value);
      if (!(bankroll >= 10)) return toast("Il capitale deve essere almeno 10 $", { error: true });
      go.disabled = true;
      try { await api("/portfolio/reset", { method: "POST", body: { bankroll } }); toast("Portafoglio azzerato"); refresh(); }
      catch (e) { toast(e.message, { error: true }); go.disabled = false; }
    });
    resetArea.replaceChildren(h("div", { class: "confirm", role: "alert", style: { maxWidth: "none" } },
      h("label", { class: "field", for: "reset-amount" }, "Nuovo capitale ($)", amount),
      h("p", { class: "small" }, "Tutte le scommesse simulate verranno cancellate. Le esclusioni restano."),
      h("div", { class: "confirm-actions" }, go, h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: paintReset } }, "Annulla")),
    ));
    amount.focus();
  }
  paintReset();

  return h("section", { class: "card", "aria-labelledby": "h-psettings" },
    h("h2", { id: "h-psettings", style: { marginBottom: "10px" } }, "Impostazioni della simulazione"),
    presetCards,
    h("label", { class: "field", for: "auto-paper", style: { margin: "14px 0 6px" } }, auto, "Aggiungi automaticamente ogni scommessa che conviene"),
    resetArea,
    h("p", { class: "muted small", style: { marginTop: "10px" } },
      `Tasso senza rischio ${fmt.pct(data.risk_free_rate)}. `,
      data.calibration_factor !== 1 ? `Incertezza corretta ×${data.calibration_factor.toFixed(2).replace(".", ",")} in base ai risultati passati.`
        : "L'incertezza verrà corretta con i risultati reali dopo 30 mercati risolti.",
      infoTip("Se le previsioni passate sono state peggiori del prezzo di mercato, l'incertezza della stima viene allargata e le puntate si riducono (e viceversa)."),
    ),
  );
}

function exclusionsCard(ctx, exclusions, categories, refresh) {
  const admin = ctx.isAdmin();
  const KIND = { market: "Mercato", event: "Evento", category: "Categoria" };
  const list = exclusions.length ? h("ul", { class: "plain-list" }, exclusions.map((x) => h("li", {},
    h("span", {}, h("span", { class: "badge badge-outline" }, KIND[x.kind]), " ",
      x.kind === "category" ? (CATEGORY_LABELS[x.value] || x.value) : x.kind === "market"
        ? h("a", { href: `#/mercati/${encodeURIComponent(x.value)}` }, x.label || x.value) : (x.label || x.value)),
    admin ? h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: async (e) => {
      e.currentTarget.disabled = true;
      try { await api(`/portfolio/exclusions/${x.id}`, { method: "DELETE" }); toast("Esclusione rimossa"); refresh(); } catch (err) { toast(err.message, { error: true }); }
    } } }, "Rimuovi") : null,
  ))) : h("p", { class: "secondary" }, "Nessuna esclusione: tutte le scommesse che convengono entrano nella simulazione.");

  let adder = null;
  if (admin) {
    const taken = new Set(exclusions.filter((x) => x.kind === "category").map((x) => x.value));
    const options = categories.filter((c) => !taken.has(c));
    if (options.length) {
      const select = h("select", { id: "excl-cat", class: "select" }, options.map((c) => h("option", { value: c }, CATEGORY_LABELS[c] || c)));
      const add = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, icon("plus"), "Escludi categoria");
      add.addEventListener("click", async () => {
        add.disabled = true;
        try {
          await api("/portfolio/exclusions", { method: "POST", body: { kind: "category", value: select.value, label: CATEGORY_LABELS[select.value] || select.value } });
          toast("Categoria esclusa dalle scommesse automatiche"); refresh();
        } catch (e) { toast(e.message, { error: true }); add.disabled = false; }
      });
      adder = h("div", { class: "form-inline", style: { marginTop: "12px" } }, h("label", { class: "field", for: "excl-cat" }, "Categoria", select), add);
    }
  }

  return h("section", { class: "card", "aria-labelledby": "h-excl" },
    h("div", { class: "card-head" }, h("div", {},
      h("h2", { id: "h-excl" }, "Esclusioni"),
      h("p", { class: "muted small" }, "Le scommesse automatiche saltano questi mercati, eventi e categorie. Mercati ed eventi si escludono dal loro dettaglio o dalle posizioni aperte."),
    )),
    list, adder,
  );
}
