// "Più esiti": Polymarket events with several mutually exclusive outcomes, shown as a distribution.
// Series identity as everywhere: market price = orange bar, Jev = aqua diamond, blended = blue circle.
import { t } from "../i18n.js";
import { h, api, fmt, toast, icon, emptyState, infoTip, externalLink, debounce, withTooltip, ttRows } from "../ui.js";
import { economicsCard, verdictBadge } from "../economics.js";

const filters = { q: "", sort: "volume" };
const SORTS = [["volume", t("Volume")], ["edge", t("Edge più alto")], ["signal", t("Ultima previsione")], ["end_date", t("Scadenza")], ["news", t("Notizie collegate")]];
const eventHref = (id) => `#/multi/${encodeURIComponent(id)}`;
const edgeCls = (e) => (e == null ? "" : e > 0 ? "pos" : e < 0 ? "neg" : "");

/** One row of the distribution: label, bar of the market price, markers for Jev and blended. */
function distRow(o, scale, pred, best) {
  const p = pred?.outcomes.find((x) => x.id === o.id);
  const market = p ? p.market : o.price;
  const pos = (v) => `${Math.min(100, (v / scale) * 100)}%`;
  const tip = () => ttRows(o.label, [
    ["market", t("Prezzo (normalizzato)"), fmt.pct(market)],
    ...(p ? [["jev", t("Stima Jev"), fmt.pct(p.model)], ["blended", t("Blended"), fmt.pct(p.blended)], [null, t("Edge"), fmt.pts(p.edge)]] : []),
  ]);
  const track = h("div", { class: "dist-track", role: "img",
    "aria-label": t("{0}: prezzo {1}{2}", o.label, fmt.pct(market), p ? t(", Jev {0}, blended {1}", fmt.pct(p.model), fmt.pct(p.blended)) : "") },
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
    h("span", {}, h("span", { class: "key market" }), t("Prezzo di mercato (normalizzato a 100%)")),
    pred ? h("span", {}, h("span", { class: "key jev" }), t("Stima Jev")) : null,
    pred ? h("span", {}, h("span", { class: "key blended" }), t("Blended")) : null);
}

const isBuy = (pred) => pred?.signal === "BUY_YES" || pred?.signal === "BUY_NO";
const sideLabel = (signal) => (signal === "BUY_NO" ? "NO" : t("SÌ"));

/** Guaranteed-profit gap: one share of every outcome costs less than it surely pays. */
function arbitrageBadge(arb) {
  if (!arb) return null;
  const what = arb.kind === "buy_all_yes" ? t("comprando il SÌ di tutti gli esiti") : t("comprando il NO di tutti gli esiti");
  return h("span", { class: "badge badge-warning", title: t("Costo {0} per un set che paga {1} in ogni caso, {2}, commissioni incluse. Solo al miglior prezzo del book: la quantità disponibile può essere piccola e il prezzo cambiare in fretta.", fmt.cents(arb.cost), fmt.money(arb.payout), what) },
    icon("alert"), t("Arbitraggio {0} per set", fmt.pts(arb.profit)));
}

function signalLine(pred, outcomes) {
  if (!pred) return h("span", { class: "muted small" }, t("Nessuna previsione"));
  const best = pred.outcomes.find((o) => o.id === pred.best_outcome_id) || outcomes.find((o) => o.id === pred.best_outcome_id);
  if (isBuy(pred) && best) {
    const ev = pred.economics?.[best.id];
    const no = pred.signal === "BUY_NO";
    return h("span", { class: "signal-line" },
      h("span", { class: `badge ${no ? "badge-critical" : "badge-good"}` }, icon(no ? "down" : "up"),
        t("Compra {0} su {1} · {2}", sideLabel(pred.signal), best.label, fmt.pts(pred.best_edge))),
      ev ? verdictBadge(ev.verdict) : null,
      ev && ev.verdict !== "NO" ? h("span", { class: "muted small" }, t("{0} a max {1}", fmt.money(ev.outlay), fmt.cents(ev.limit_price))) : null);
  }
  return h("span", { class: "badge" }, icon("pause"), t("Attendi"));
}

// ---------- List ----------

export async function viewMultiList(ctx) {
  const list = h("div", { class: "multi-grid" });
  const summary = h("p", { class: "muted small", role: "status" });
  const load = async () => {
    try {
      const data = localizeOther(await api("/multi", { params: { q: filters.q || undefined, sort: filters.sort, limit: 60 } }));
      summary.textContent = `${fmt.count(data.events.length, t("evento"), t("eventi"))}${data.total > data.events.length ? t(" su {0}", fmt.int(data.total)) : ""}`;
      list.replaceChildren(...(data.events.length ? data.events.map(eventCard) : [emptyState(t("Nessun evento"),
        filters.q ? t("Nessun evento o esito corrisponde alla ricerca.") : t("Gli eventi a più esiti arrivano con l'aggiornamento dei mercati."))]));
    } catch (e) {
      list.replaceChildren(emptyState(t("Impossibile caricare gli eventi"), e.message));
    }
  };
  const search = h("input", { id: "mx-search", class: "search", type: "search", placeholder: t("Cerca un evento o un esito (es. elezioni, Arsenal)"), value: filters.q, "aria-label": t("Cerca un evento o un esito") });
  search.addEventListener("input", debounce(() => { filters.q = search.value.trim(); load(); }, 300));
  const sort = h("select", { id: "mx-sort", class: "select" }, SORTS.map(([v, l]) => h("option", { value: v, selected: v === filters.sort }, l)));
  sort.addEventListener("change", () => { filters.sort = sort.value; load(); });
  await load();
  return h("div", {},
    ctx.pageHead(t("Mercati a più esiti"),
      t("Eventi con più risposte possibili, di cui una sola vincerà (elezioni, campionati, premi). Ogni esito è una quota SÌ/NO; qui si leggono insieme, come una distribuzione di probabilità.")),
    h("div", { class: "filters", role: "group", "aria-label": t("Filtri e ordinamento") },
      h("div", { class: "search-wrap" }, icon("search"), search),
      h("label", { class: "field", for: "mx-sort" }, t("Ordina per"), sort)),
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
      h("span", {}, t("Scade {0}", fmt.date(e.end_date))), h("span", {}, t("Volume {0}", fmt.usd(e.volume))),
      h("span", {}, fmt.count(e.outcome_count, t("esito"), t("esiti"))), h("span", {}, fmt.count(e.linked_articles, t("notizia"), t("notizie")))),
    arbitrageBadge(e.arbitrage),
    h("div", { class: "dist" }, shown.map((o) => distRow(o, scale, pred, pred?.best_outcome_id === o.id && isBuy(pred)))),
    e.outcome_count > shown.length ? h("p", { class: "muted small" }, `+ ${fmt.count(e.outcome_count - shown.length, t("altro esito"), t("altri esiti"))}`) : null,
    h("div", { class: "multi-foot" }, signalLine(pred, e.outcomes),
      pred ? h("span", { class: "muted small", title: fmt.dateTime(pred.created_at) }, fmt.ago(pred.created_at)) : null,
      h("a", { class: "btn btn-ghost btn-sm", href: eventHref(e.id) }, t("Dettaglio"))),
  );
}

// ---------- Detail ----------

export async function viewMultiDetail(ctx, id) {
  const e = localizeOther(await api(`/multi/${encodeURIComponent(id)}`));
  const status = ctx.status;
  const pred = e.latest_prediction;
  const rows = pred
    ? [...pred.outcomes].sort((a, b) => b.market - a.market)
    : e.outcomes.map((o) => ({ id: o.id, label: o.label, market: o.price }));
  const scale = Math.max(0.1, ...rows.flatMap((r) => [r.market, r.model, r.blended]).filter((v) => v != null)) * 1.1;

  const predictBtn = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"),
    pred ? t("Nuova previsione") : t("Chiedi una previsione a Jev"));
  let blocker = null;
  if (!ctx.isAdmin()) blocker = t("Solo gli amministratori possono chiedere previsioni (sono chiamate a pagamento).");
  else if (!status?.jev_enabled) blocker = t("Serve TYPESAFE_API_KEY nel file .env.");
  else if (e.closed) blocker = t("L'evento è chiuso.");
  else if (!e.evidence.length) blocker = t("Nessuna notizia recente collegata a questo evento.");
  predictBtn.disabled = Boolean(blocker);
  predictBtn.addEventListener("click", async () => {
    ctx.setBusy(predictBtn, true);
    try {
      const p = localizeOther(await api(`/multi/${encodeURIComponent(id)}/predict`, { method: "POST" }));
      const best = p.outcomes.find((o) => o.id === p.best_outcome_id);
      toast(isBuy(p) ? t("Previsione salvata: compra {0} su {1} ({2})", sideLabel(p.signal), best.label, fmt.pts(p.best_edge)) : t("Previsione salvata: nessun esito abbastanza lontano dal suo prezzo"));
      ctx.rerender();
    } catch (err) {
      toast(err.message, { error: true });
      ctx.setBusy(predictBtn, false);
    }
  });

  const winner = e.winner_id ? e.outcomes.find((o) => o.id === e.winner_id) : null;
  const table = h("details", { class: "chart-table" }, h("summary", {}, t("Mostra tabella")),
    h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
      h("thead", {}, h("tr", {}, ...[t("Esito"), t("Quota SÌ"), t("Prezzo normalizzato"), t("Jev"), t("Blended"), t("Edge")].map((t, i) => h("th", { scope: "col", class: i ? "num" : null }, t)))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("th", { scope: "row" }, r.label), h("td", { class: "num" }, fmt.cents(r.price ?? r.market)), h("td", { class: "num" }, fmt.pct(r.market)),
        h("td", { class: "num" }, fmt.pct(r.model)), h("td", { class: "num" }, fmt.pct(r.blended)),
        h("td", { class: `num ${edgeCls(r.edge)}` }, r.edge == null ? "–" : fmt.pts(r.edge))))))));

  return h("div", {},
    h("a", { class: "back", href: "#/multi" }, icon("back"), t("Tutti gli eventi")),
    h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "eyebrow" }, t("Evento Polymarket a più esiti")),
      h("h1", { style: { marginTop: "4px" } }, e.title),
      h("div", { class: "meta-row" },
        h("span", { class: `badge ${e.closed ? "" : "badge-outline"}` }, e.closed ? t("Chiuso") : t("Aperto")),
        winner ? h("span", { class: "badge badge-good" }, icon("check"), t("Ha vinto: {0}", winner.label)) : null,
        h("span", {}, t("Scade {0}", fmt.date(e.end_date))), h("span", {}, t("Volume {0}", fmt.usd(e.volume))),
        h("span", {}, fmt.count(e.outcome_count, t("esito"), t("esiti"))),
        arbitrageBadge(e.arbitrage),
        e.url ? externalLink(e.url, t("Apri su Polymarket")) : null)),
    h("section", { class: "card", "aria-labelledby": "h-mx-dist", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" },
        h("h2", { id: "h-mx-dist" }, pred ? t("Distribuzione: mercato e Jev") : t("Distribuzione secondo il mercato")),
        signalLine(pred, e.outcomes)),
      h("p", { class: "muted small" }, pred
        ? t("Jev ha letto {0} e ha stimato la probabilità di ogni esito senza vedere i prezzi. Evidenze {1}: la sua stima pesa per il {2}. Previsione {3}. ", fmt.count(pred.article_count, t("notizia"), t("notizie")), fmt.pct(pred.evidence_strength), fmt.pct(pred.model_weight), fmt.ago(pred.created_at))
        : t("I prezzi delle quote SÌ sono normalizzati in modo che la somma faccia 100%. "),
      infoTip(t("Edge = blended − prezzo della quota SÌ (quello che si paga davvero). Il segnale indica l'esito più lontano dal suo prezzo, se l'edge supera la soglia minima e le notizie sono abbastanza forti: SÌ se è sottovalutato, NO se è sopravvalutato (spesso un favorito su cui il mercato è troppo ottimista). Jev valuta i 12 esiti più probabili; gli altri sono sommati in «Altri esiti»."), t("Come si legge?"))),
      legend(pred),
      h("div", { class: "dist dist-full" }, rows.map((r) => distRow({ id: r.id, label: r.label, price: r.market }, scale, pred,
        isBuy(pred) && pred.best_outcome_id === r.id))),
      table,
      h("div", { class: "predict-bar" }, predictBtn, h("span", { class: "muted small" }, blocker || t("Una chiamata all'API TypeSafe per tutto l'evento.")))),
    pred ? economicsSection(ctx, pred) : null,
    h("section", { class: "card", "aria-labelledby": "h-mx-news", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" }, h("h2", { id: "h-mx-news" }, t("Notizie collegate")),
        h("span", { class: "muted small" }, t("Una per storia, dalla più utile"))),
      e.evidence.length ? h("div", {}, e.evidence.map((ev) => h("div", { class: "ev" },
        h("div", {},
          externalLink(ev.url, h("span", { class: "ev-title" }, ev.title)),
          h("div", { class: "article-meta", style: { margin: "4px 0 0" } }, h("span", {}, ev.source_name),
            h("span", { title: fmt.dateTime(ev.published_at) }, fmt.ago(ev.published_at)),
            ev.corroboration > 1 ? h("span", { class: "badge badge-outline" }, t("{0} fonti", ev.corroboration)) : null),
          ev.matched_terms.length ? h("div", { class: "terms" }, ev.matched_terms.map((x) => h("span", { class: "term" }, x))) : null),
        h("div", { class: "ev-stats" },
          h("span", {}, t("Pertinenza "), h("b", { class: "mono" }, fmt.pct(ev.match_score))),
          ev.relevance != null ? h("span", {}, t("Rilevanza "), h("b", { class: "mono" }, fmt.pct(ev.relevance))) : null,
          ev.favours ? h("span", { class: "badge badge-outline" }, icon("up"), t("Favorisce {0}", ev.favours)) : null),
      ))) : h("p", { class: "secondary" }, t("Nessuna notizia recente riguarda questo evento."))),
    e.predictions.length > 1 ? h("section", { class: "card", "aria-labelledby": "h-mx-hist", style: { marginBottom: "16px" } },
      h("h2", { id: "h-mx-hist", style: { marginBottom: "10px" } }, t("Storico delle previsioni")),
      h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
        h("thead", {}, h("tr", {}, ...[t("Quando"), t("Esito più lontano dal prezzo"), t("Edge"), t("Evidenze"), t("Segnale")].map((t, i) => h("th", { scope: "col", class: i === 2 || i === 3 ? "num" : null }, t)))),
        h("tbody", {}, e.predictions.map((p) => {
          const best = p.outcomes.find((o) => o.id === p.best_outcome_id);
          return h("tr", {}, h("td", {}, fmt.dateTime(p.created_at)), h("td", {}, best?.label || "–"),
            h("td", { class: `num ${edgeCls(p.best_edge)}` }, fmt.pts(p.best_edge)), h("td", { class: "num" }, fmt.pct(p.evidence_strength)),
            h("td", {}, isBuy(p) ? t("Compra {0}", sideLabel(p.signal)) : t("Attendi")));
        }))))) : null,
    e.description ? h("details", { class: "card rules" }, h("summary", {}, t("Regole di risoluzione")), h("p", { class: "secondary", style: { whiteSpace: "pre-line", marginTop: "10px" } }, e.description)) : null,
  );
}

/** "Conviene?" for one outcome: the same evaluation as YES/NO markets, on its YES share if
 *  underpriced or its NO share if overpriced. */
function economicsSection(ctx, pred) {
  const candidates = pred.outcomes.filter((o) => o.id !== "other" && o.edge !== 0).sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge));
  if (!candidates.length) return null;
  let selected = pred.best_outcome_id && candidates.some((o) => o.id === pred.best_outcome_id) ? pred.best_outcome_id : candidates[0].id;
  const holder = h("div", {});
  const paint = () => {
    const o = candidates.find((c) => c.id === selected);
    holder.replaceChildren(economicsCard(ctx, { id: o.id, question: `${o.label} (${o.edge > 0 ? t("SÌ") : "NO"})` }));
  };
  const select = h("select", { id: "mx-outcome", class: "select", style: { minWidth: "260px", maxWidth: "100%" } },
    candidates.map((o) => h("option", { value: o.id, selected: o.id === selected }, t("{0} · {1} · edge {2}", o.label, o.edge > 0 ? t("SÌ") : "NO", fmt.pts(o.edge)))));
  select.addEventListener("change", () => { selected = select.value; paint(); });
  paint();
  return h("div", { style: { marginBottom: "16px" } },
    h("label", { class: "field", for: "mx-outcome", style: { marginBottom: "8px" } }, t("Esito da valutare"), select),
    holder);
}

/** The "other outcomes" bucket is labelled by the server when the forecast is made: show it in the UI language. */
function localizeOther(value) {
  if (Array.isArray(value)) value.forEach(localizeOther);
  else if (value && typeof value === "object") {
    if (value.id === "other" && typeof value.label === "string") {
      const n = value.label.match(/\((\d+)\)/);
      value.label = n ? t("Altri esiti ({0})", n[1]) : t("Altri esiti");
    }
    Object.values(value).forEach((v) => { if (v && typeof v === "object") localizeOther(v); });
  }
  return value;
}
