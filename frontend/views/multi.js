// "Più esiti": Polymarket events with several mutually exclusive outcomes, shown as a distribution.
// Series identity as everywhere: market price = orange bar, Jev = aqua diamond, blended = blue circle.
import { h, api, fmt, toast, icon, emptyState, infoTip, externalLink, debounce, withTooltip, ttRows } from "../ui.js";
import { economicsCard, verdictBadge } from "../economics.js";

const filters = { q: "", sort: "volume" };
const SORTS = [["volume", "Volume"], ["edge", "Edge più alto"], ["signal", "Ultima previsione"], ["end_date", "Scadenza"], ["news", "Notizie collegate"]];
const eventHref = (id) => `#/multi/${encodeURIComponent(id)}`;
const edgeCls = (e) => (e == null ? "" : e > 0 ? "pos" : e < 0 ? "neg" : "");

/** One row of the distribution: label, bar of the market price, markers for Jev and blended. */
function distRow(o, scale, pred, best) {
  const p = pred?.outcomes.find((x) => x.id === o.id);
  const market = p ? p.market : o.price;
  const pos = (v) => `${Math.min(100, (v / scale) * 100)}%`;
  const tip = () => ttRows(o.label, [
    ["market", "Prezzo (normalizzato)", fmt.pct(market)],
    ...(p ? [["jev", "Stima Jev", fmt.pct(p.model)], ["blended", "Blended", fmt.pct(p.blended)], [null, "Edge", fmt.pts(p.edge)]] : []),
  ]);
  const track = h("div", { class: "dist-track", role: "img",
    "aria-label": `${o.label}: prezzo ${fmt.pct(market)}${p ? `, Jev ${fmt.pct(p.model)}, blended ${fmt.pct(p.blended)}` : ""}` },
  h("span", { class: "dist-bar", style: { width: pos(market) } }),
  p ? h("span", { class: "pmark jev", style: { left: pos(p.model) } }) : null,
  p ? h("span", { class: "pmark blended", style: { left: pos(p.blended) } }) : null);
  withTooltip(track, tip);
  return h("div", { class: `dist-row${best ? " best" : ""}` },
    h("span", { class: "dist-label", title: o.label }, o.label),
    track,
    h("span", { class: "mono dist-value" }, fmt.pct(market)),
    p ? h("span", { class: `mono dist-edge ${edgeCls(p.edge)}` }, fmt.pts(p.edge)) : h("span", { class: "dist-edge" }));
}

function legend(pred) {
  return h("div", { class: "legend" },
    h("span", {}, h("span", { class: "key market" }), "Prezzo di mercato (normalizzato a 100%)"),
    pred ? h("span", {}, h("span", { class: "key jev" }), "Stima Jev") : null,
    pred ? h("span", {}, h("span", { class: "key blended" }), "Blended") : null);
}

function signalLine(pred, outcomes) {
  if (!pred) return h("span", { class: "muted small" }, "Nessuna previsione");
  const best = pred.outcomes.find((o) => o.id === pred.best_outcome_id) || outcomes.find((o) => o.id === pred.best_outcome_id);
  if (pred.signal === "BUY_YES" && best) {
    const ev = pred.economics?.[best.id];
    return h("span", { class: "signal-line" },
      h("span", { class: "badge badge-good" }, icon("up"), `Compra SÌ su ${best.label} · ${fmt.pts(pred.best_edge)}`),
      ev ? verdictBadge(ev.verdict) : null,
      ev && ev.verdict !== "NO" ? h("span", { class: "muted small" }, `${fmt.money(ev.outlay)} a max ${fmt.cents(ev.limit_price)}`) : null);
  }
  return h("span", { class: "badge" }, icon("pause"), "Attendi");
}

// ---------- List ----------

export async function viewMultiList(ctx) {
  const list = h("div", { class: "multi-grid" });
  const summary = h("p", { class: "muted small", role: "status" });
  const load = async () => {
    try {
      const data = await api("/multi", { params: { q: filters.q || undefined, sort: filters.sort, limit: 60 } });
      summary.textContent = `${fmt.count(data.events.length, "evento", "eventi")}${data.total > data.events.length ? ` su ${fmt.int(data.total)}` : ""}`;
      list.replaceChildren(...(data.events.length ? data.events.map(eventCard) : [emptyState("Nessun evento",
        filters.q ? "Nessun evento o esito corrisponde alla ricerca." : "Gli eventi a più esiti arrivano con l'aggiornamento dei mercati.")]));
    } catch (e) {
      list.replaceChildren(emptyState("Impossibile caricare gli eventi", e.message));
    }
  };
  const search = h("input", { id: "mx-search", class: "search", type: "search", placeholder: "Cerca un evento o un esito (es. elezioni, Arsenal)", value: filters.q, "aria-label": "Cerca un evento o un esito" });
  search.addEventListener("input", debounce(() => { filters.q = search.value.trim(); load(); }, 300));
  const sort = h("select", { id: "mx-sort", class: "select" }, SORTS.map(([v, l]) => h("option", { value: v, selected: v === filters.sort }, l)));
  sort.addEventListener("change", () => { filters.sort = sort.value; load(); });
  await load();
  return h("div", {},
    ctx.pageHead("Mercati a più esiti",
      "Eventi con più risposte possibili, di cui una sola vincerà (elezioni, campionati, premi). Ogni esito è una quota SÌ/NO; qui si leggono insieme, come una distribuzione di probabilità."),
    h("div", { class: "filters", role: "group", "aria-label": "Filtri e ordinamento" },
      h("div", { class: "search-wrap" }, icon("search"), search),
      h("label", { class: "field", for: "mx-sort" }, "Ordina per", sort)),
    summary, list);
}

export function eventCard(e) {
  const pred = e.latest_prediction;
  const shown = e.outcomes.slice(0, 4);
  const values = shown.flatMap((o) => { const p = pred?.outcomes.find((x) => x.id === o.id); return p ? [p.market, p.model, p.blended] : [o.price]; });
  const scale = Math.max(0.1, ...values.filter((v) => v != null)) * 1.1;
  return h("article", { class: "card multi-card" },
    h("h2", { class: "multi-title" }, h("a", { href: eventHref(e.id) }, e.title)),
    h("div", { class: "meta-row small" },
      h("span", {}, `Scade ${fmt.date(e.end_date)}`), h("span", {}, `Volume ${fmt.usd(e.volume)}`),
      h("span", {}, fmt.count(e.outcome_count, "esito", "esiti")), h("span", {}, fmt.count(e.linked_articles, "notizia", "notizie"))),
    h("div", { class: "dist" }, shown.map((o) => distRow(o, scale, pred, pred?.best_outcome_id === o.id && pred.signal === "BUY_YES"))),
    e.outcome_count > shown.length ? h("p", { class: "muted small" }, `+ ${fmt.count(e.outcome_count - shown.length, "altro esito", "altri esiti")}`) : null,
    h("div", { class: "multi-foot" }, signalLine(pred, e.outcomes),
      pred ? h("span", { class: "muted small", title: fmt.dateTime(pred.created_at) }, fmt.ago(pred.created_at)) : null,
      h("a", { class: "btn btn-ghost btn-sm", href: eventHref(e.id) }, "Dettaglio")),
  );
}

// ---------- Detail ----------

export async function viewMultiDetail(ctx, id) {
  const e = await api(`/multi/${encodeURIComponent(id)}`);
  const status = ctx.status;
  const pred = e.latest_prediction;
  const rows = pred
    ? [...pred.outcomes].sort((a, b) => b.market - a.market)
    : e.outcomes.map((o) => ({ id: o.id, label: o.label, market: o.price }));
  const scale = Math.max(0.1, ...rows.flatMap((r) => [r.market, r.model, r.blended]).filter((v) => v != null)) * 1.1;

  const predictBtn = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"),
    pred ? "Nuova previsione" : "Chiedi una previsione a Jev");
  let blocker = null;
  if (!ctx.isAdmin()) blocker = "Solo gli amministratori possono chiedere previsioni (sono chiamate a pagamento).";
  else if (!status?.jev_enabled) blocker = "Serve TYPESAFE_API_KEY nel file .env.";
  else if (e.closed) blocker = "L'evento è chiuso.";
  else if (!e.evidence.length) blocker = "Nessuna notizia recente collegata a questo evento.";
  predictBtn.disabled = Boolean(blocker);
  predictBtn.addEventListener("click", async () => {
    ctx.setBusy(predictBtn, true);
    try {
      const p = await api(`/multi/${encodeURIComponent(id)}/predict`, { method: "POST" });
      const best = p.outcomes.find((o) => o.id === p.best_outcome_id);
      toast(p.signal === "BUY_YES" ? `Previsione salvata: compra SÌ su ${best.label} (${fmt.pts(p.best_edge)})` : "Previsione salvata: nessun esito abbastanza sottovalutato");
      ctx.rerender();
    } catch (err) {
      toast(err.message, { error: true });
      ctx.setBusy(predictBtn, false);
    }
  });

  const winner = e.winner_id ? e.outcomes.find((o) => o.id === e.winner_id) : null;
  const table = h("details", { class: "chart-table" }, h("summary", {}, "Mostra tabella"),
    h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
      h("thead", {}, h("tr", {}, ...["Esito", "Quota SÌ", "Prezzo normalizzato", "Jev", "Blended", "Edge"].map((t, i) => h("th", { scope: "col", class: i ? "num" : null }, t)))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("th", { scope: "row" }, r.label), h("td", { class: "num" }, fmt.cents(r.price ?? r.market)), h("td", { class: "num" }, fmt.pct(r.market)),
        h("td", { class: "num" }, fmt.pct(r.model)), h("td", { class: "num" }, fmt.pct(r.blended)),
        h("td", { class: `num ${edgeCls(r.edge)}` }, r.edge == null ? "–" : fmt.pts(r.edge))))))));

  return h("div", {},
    h("a", { class: "back", href: "#/multi" }, icon("back"), "Tutti gli eventi"),
    h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "eyebrow" }, "Evento Polymarket a più esiti"),
      h("h1", { style: { marginTop: "4px" } }, e.title),
      h("div", { class: "meta-row" },
        h("span", { class: `badge ${e.closed ? "" : "badge-outline"}` }, e.closed ? "Chiuso" : "Aperto"),
        winner ? h("span", { class: "badge badge-good" }, icon("check"), `Ha vinto: ${winner.label}`) : null,
        h("span", {}, `Scade ${fmt.date(e.end_date)}`), h("span", {}, `Volume ${fmt.usd(e.volume)}`),
        h("span", {}, fmt.count(e.outcome_count, "esito", "esiti")),
        e.url ? externalLink(e.url, "Apri su Polymarket") : null)),
    h("section", { class: "card", "aria-labelledby": "h-mx-dist", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" },
        h("h2", { id: "h-mx-dist" }, pred ? "Distribuzione: mercato e Jev" : "Distribuzione secondo il mercato"),
        signalLine(pred, e.outcomes)),
      h("p", { class: "muted small" }, pred
        ? `Jev ha letto ${fmt.count(pred.article_count, "notizia", "notizie")} e ha stimato la probabilità di ogni esito senza vedere i prezzi. Evidenze ${fmt.pct(pred.evidence_strength)}: la sua stima pesa per il ${fmt.pct(pred.model_weight)}. Previsione ${fmt.ago(pred.created_at)}. `
        : "I prezzi delle quote SÌ sono normalizzati in modo che la somma faccia 100%. ",
      infoTip("Edge = blended − prezzo normalizzato. Il segnale indica l'esito più sottovalutato, se l'edge supera la soglia minima e le notizie sono abbastanza forti. Jev valuta i 12 esiti più probabili; gli altri sono sommati in «Altri esiti».", "Come si legge?")),
      legend(pred),
      h("div", { class: "dist dist-full" }, rows.map((r) => distRow({ id: r.id, label: r.label, price: r.market }, scale, pred,
        pred?.signal === "BUY_YES" && pred.best_outcome_id === r.id))),
      table,
      h("div", { class: "predict-bar" }, predictBtn, h("span", { class: "muted small" }, blocker || "Una chiamata all'API TypeSafe per tutto l'evento."))),
    pred ? economicsSection(ctx, pred) : null,
    h("section", { class: "card", "aria-labelledby": "h-mx-news", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" }, h("h2", { id: "h-mx-news" }, "Notizie collegate"),
        h("span", { class: "muted small" }, "Una per storia, dalla più utile")),
      e.evidence.length ? h("div", {}, e.evidence.map((ev) => h("div", { class: "ev" },
        h("div", {},
          externalLink(ev.url, h("span", { class: "ev-title" }, ev.title)),
          h("div", { class: "article-meta", style: { margin: "4px 0 0" } }, h("span", {}, ev.source_name),
            h("span", { title: fmt.dateTime(ev.published_at) }, fmt.ago(ev.published_at)),
            ev.corroboration > 1 ? h("span", { class: "badge badge-outline" }, `${ev.corroboration} fonti`) : null),
          ev.matched_terms.length ? h("div", { class: "terms" }, ev.matched_terms.map((t) => h("span", { class: "term" }, t))) : null),
        h("div", { class: "ev-stats" },
          h("span", {}, "Pertinenza ", h("b", { class: "mono" }, fmt.pct(ev.match_score))),
          ev.relevance != null ? h("span", {}, "Rilevanza ", h("b", { class: "mono" }, fmt.pct(ev.relevance))) : null,
          ev.favours ? h("span", { class: "badge badge-outline" }, icon("up"), `Favorisce ${ev.favours}`) : null),
      ))) : h("p", { class: "secondary" }, "Nessuna notizia recente riguarda questo evento.")),
    e.predictions.length > 1 ? h("section", { class: "card", "aria-labelledby": "h-mx-hist", style: { marginBottom: "16px" } },
      h("h2", { id: "h-mx-hist", style: { marginBottom: "10px" } }, "Storico delle previsioni"),
      h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
        h("thead", {}, h("tr", {}, ...["Quando", "Esito più sottovalutato", "Edge", "Evidenze", "Segnale"].map((t, i) => h("th", { scope: "col", class: i === 2 || i === 3 ? "num" : null }, t)))),
        h("tbody", {}, e.predictions.map((p) => {
          const best = p.outcomes.find((o) => o.id === p.best_outcome_id);
          return h("tr", {}, h("td", {}, fmt.dateTime(p.created_at)), h("td", {}, best?.label || "–"),
            h("td", { class: `num ${edgeCls(p.best_edge)}` }, fmt.pts(p.best_edge)), h("td", { class: "num" }, fmt.pct(p.evidence_strength)),
            h("td", {}, p.signal === "BUY_YES" ? "Compra SÌ" : "Attendi"));
        }))))) : null,
    e.description ? h("details", { class: "card rules" }, h("summary", {}, "Regole di risoluzione"), h("p", { class: "secondary", style: { whiteSpace: "pre-line", marginTop: "10px" } }, e.description)) : null,
  );
}

/** "Conviene?" for one outcome: the same evaluation as YES/NO markets, on that outcome's YES share. */
function economicsSection(ctx, pred) {
  const candidates = pred.outcomes.filter((o) => o.id !== "other" && o.edge > 0).sort((a, b) => b.edge - a.edge);
  if (!candidates.length) return null;
  let selected = pred.best_outcome_id && candidates.some((o) => o.id === pred.best_outcome_id) ? pred.best_outcome_id : candidates[0].id;
  const holder = h("div", {});
  const paint = () => {
    const o = candidates.find((c) => c.id === selected);
    holder.replaceChildren(economicsCard(ctx, { id: o.id, question: `${o.label} (SÌ)` }));
  };
  const select = h("select", { id: "mx-outcome", class: "select", style: { minWidth: "260px", maxWidth: "100%" } },
    candidates.map((o) => h("option", { value: o.id, selected: o.id === selected }, `${o.label} · edge ${fmt.pts(o.edge)}`)));
  select.addEventListener("change", () => { selected = select.value; paint(); });
  paint();
  return h("div", { style: { marginBottom: "16px" } },
    h("label", { class: "field", for: "mx-outcome", style: { marginBottom: "8px" } }, "Esito da valutare", select),
    holder);
}
