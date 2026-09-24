// Backtest: Jev on resolved markets as of a past date, accuracy vs the price, and suggested parameters.
import { h, api, fmt, toast, icon, statTile, emptyState, infoTip, externalLink, CATEGORY_LABELS } from "../ui.js";
import { reliabilityChart } from "../charts.js";

const POLL_MS = 3000;
const STATUS = { running: ["badge-outline", "In corso"], done: ["badge-good", "Completato"], stopped: ["", "Interrotto"], failed: ["badge-critical", "Non riuscito"] };
const dayISO = (d) => d.toISOString().slice(0, 10);
const b3 = (v) => (v == null ? "–" : fmt.num3(v));
const statusBadge = (st) => { const [cls, label] = STATUS[st] || ["", st]; return h("span", { class: `badge ${cls}` }, label); };

export async function viewBacktest(ctx, runId) {
  const admin = ctx.isAdmin();
  const [runs, params] = await Promise.all([api("/backtest/runs"), api("/backtest/parameters")]);
  const selectedId = runId || runs.find((r) => r.status !== "running")?.id || runs[0]?.id;
  const selected = selectedId ? await api(`/backtest/runs/${selectedId}`).catch(() => null) : null;
  const running = runs.find((r) => r.running);

  return h("div", {},
    ctx.pageHead("Backtest",
      "Come avrebbe previsto Jev i mercati già risolti, con le notizie e il prezzo di allora. Serve a capire se e quanto fidarsi di Jev prima di usare soldi veri."),
    h("div", { class: "notice", role: "note" }, icon("alert"), h("div", {},
      h("p", {}, h("b", {}, "Attenzione al senno di poi. "), "Jev potrebbe conoscere già come sono finiti gli eventi passati. ",
        "I risultati sono affidabili solo per mercati risolti dopo la data fino a cui arrivano le conoscenze del modello di Jev; per quelli più vecchi possono sembrare migliori di quanto sono."),
      h("p", {}, h("b", {}, "Notizie di allora. "), "L'archivio di questa app contiene solo ciò che era già stato salvato a quella data: nessuna notizia successiva può entrare. ",
        "Google News copre qualsiasi periodo, ma i suoi filtri per data lasciano passare articoli aggiornati dopo: quei casi possono sembrare migliori di quanto sono. I risultati dei soli casi con l'archivio sono mostrati a parte."),
      h("p", { class: "muted small" }, "Il book storico non esiste: le scommesse simulate usano il prezzo di allora più metà dello spread tipico e le commissioni della categoria."))),
    running ? progressCard(ctx, running) : admin ? newRunCard(ctx, params) : null,
    parametersCard(ctx, params),
    runs.length ? runsCard(runs, selected?.id) : emptyState("Nessun backtest ancora",
      admin ? "Scegli un periodo e avvia il primo backtest qui sopra." : "Un amministratore deve avviare il primo backtest."),
    selected && selected.status !== "running" ? await resultsView(ctx, selected, params) : null,
  );
}

// ---------- New run ----------

function newRunCard(ctx, params) {
  const today = new Date();
  const field = (id, label, input, hint) => h("label", { class: "field-col", for: id }, h("span", { class: "field-label" }, label), input, hint ? h("span", { class: "muted small" }, hint) : null);
  const after = h("input", { id: "bt-after", class: "input", type: "date", value: dayISO(new Date(today - 150 * 86_400_000)), max: dayISO(today) });
  const before = h("input", { id: "bt-before", class: "input", type: "date", value: dayISO(new Date(today - 3 * 86_400_000)), max: dayISO(today) });
  const markets = h("input", { id: "bt-markets", class: "input", type: "number", min: "1", max: "300", value: "30", inputmode: "numeric" });
  const volume = h("input", { id: "bt-volume", class: "input", type: "number", min: "0", step: "10000", value: "50000", inputmode: "numeric" });
  const calls = h("input", { id: "bt-calls", class: "input", type: "number", min: "1", max: "1000", value: "60", inputmode: "numeric" });
  const decided = h("input", { id: "bt-decided", type: "checkbox", checked: true });
  const newsSource = h("select", { id: "bt-news", class: "input" },
    h("option", { value: "auto" }, "Archivio, poi Google News"),
    h("option", { value: "archive" }, "Solo archivio (nessun senno di poi)"),
    h("option", { value: "google" }, "Solo Google News"));
  const kinds = [["binary", "Sì / No"], ["multi", "Più esiti"]].map(([k, label]) => {
    const box = h("input", { type: "checkbox", value: k, checked: k === "binary", id: `bt-k-${k}` });
    return [box, h("label", { class: "chip", for: `bt-k-${k}` }, box, label)];
  });
  const horizons = [[1, "1 giorno prima"], [7, "7 giorni prima"], [30, "30 giorni prima"]].map(([d, label]) => {
    const box = h("input", { type: "checkbox", value: String(d), checked: d === 7, id: `bt-h${d}` });
    return [box, h("label", { class: "chip", for: `bt-h${d}` }, box, label)];
  });
  const estimate = h("p", { class: "small" });
  const paint = () => {
    const nh = horizons.filter(([b]) => b.checked).length;
    const planned = Number(markets.value || 0) * nh;
    const cap = Math.min(planned, Number(calls.value || 0));
    estimate.textContent = `Fino a ${fmt.count(planned, "caso", "casi")} (mercati × orizzonti), al massimo ${fmt.count(cap, "chiamata", "chiamate")} a Jev. `
      + "I casi senza notizie, senza prezzo storico o già decisi dal prezzo non consumano chiamate.";
  };
  [markets, calls, ...horizons.map(([b]) => b)].forEach((el) => el.addEventListener("input", paint));
  paint();

  const go = h("button", { class: "btn btn-primary", type: "button", disabled: !params.jev_enabled,
    title: params.jev_enabled ? null : "Serve TYPESAFE_API_KEY" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("play"), "Avvia backtest");
  go.addEventListener("click", async () => {
    const hs = horizons.filter(([b]) => b.checked).map(([b]) => Number(b.value));
    if (!hs.length) return toast("Scegli almeno un orizzonte", { error: true });
    const ks = kinds.filter(([b]) => b.checked).map(([b]) => b.value);
    if (!ks.length) return toast("Scegli almeno un tipo di mercato", { error: true });
    ctx.setBusy(go, true);
    try {
      await api("/backtest/runs", { method: "POST", body: {
        resolved_after: after.value, resolved_before: before.value, max_markets: Number(markets.value),
        min_volume: Number(volume.value), horizons: hs, max_calls: Number(calls.value), exclude_decided: decided.checked, kinds: ks,
        news_source: newsSource.value,
      } });
      toast("Backtest avviato: puoi continuare a usare la dashboard.");
      ctx.rerender();
    } catch (e) {
      toast(e.message, { error: true });
      ctx.setBusy(go, false);
    }
  });

  return h("section", { class: "card", "aria-labelledby": "h-bt-new", style: { marginBottom: "16px" } },
    h("h2", { id: "h-bt-new", style: { marginBottom: "12px" } }, "Nuovo backtest"),
    h("div", { class: "settings-grid" },
      field("bt-after", "Mercati chiusi dal", after, "Meglio dopo la data fino a cui arrivano le conoscenze di Jev"),
      field("bt-before", "al", before),
      field("bt-markets", "Mercati (i più scambiati)", markets),
      field("bt-volume", "Volume minimo ($)", volume),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, "Quando fare la previsione"),
        h("div", { class: "chips", role: "group", "aria-label": "Orizzonti" }, horizons.map(([, chip]) => chip))),
      field("bt-calls", "Limite di chiamate a Jev", calls),
      field("bt-news", "Notizie di allora", newsSource, "L'archivio copre solo il periodo in cui l'app era attiva"),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, "Tipo di mercati"),
        h("div", { class: "chips", role: "group", "aria-label": "Tipo di mercati" }, kinds.map(([, chip]) => chip)),
        h("span", { class: "muted small" }, "Più esiti: una chiamata per evento; gli esiti mostrati a Jev sono i più probabili secondo il prezzo di allora")),
    ),
    h("label", { class: "field", for: "bt-decided", style: { marginTop: "12px" } }, decided,
      "Salta i mercati già decisi dal prezzo (sotto il 3% o sopra il 97%)"),
    estimate,
    h("div", { class: "actions", style: { marginTop: "4px" } }, go),
  );
}

function progressCard(ctx, run) {
  const bar = h("progress", { max: String(Math.max(1, run.total)), value: String(run.done + run.skipped + run.failed) });
  const text = h("p", { class: "small mono" });
  const paint = (r) => {
    bar.max = Math.max(1, r.total);
    bar.value = r.done + r.skipped + r.failed;
    text.textContent = r.total
      ? `${fmt.int(r.done)} valutati · ${fmt.int(r.skipped)} saltati · ${fmt.int(r.failed)} errori · su ${fmt.int(r.total)}`
      : r.message || "Preparazione…";
  };
  paint(run);
  const stop = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, icon("pause"), "Interrompi");
  stop.addEventListener("click", async () => {
    stop.disabled = true;
    try { await api(`/backtest/runs/${run.id}/stop`, { method: "POST" }); } catch (e) { toast(e.message, { error: true }); stop.disabled = false; }
  });
  const card = h("section", { class: "card bulk-card", "aria-live": "polite", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("h2", {}, "Backtest in corso"), ctx.isAdmin() ? stop : null), bar, text);
  const poll = async () => {
    if (!card.isConnected) return;
    try {
      const r = await api(`/backtest/runs/${run.id}`);
      if (r.status !== "running" || !r.running) {
        toast(r.status === "done" ? "Backtest completato" : `Backtest: ${r.message || STATUS[r.status]?.[1] || r.status}`);
        window.location.hash = `#/backtest/${r.id}`;
        return ctx.rerender();
      }
      paint(r);
    } catch { /* retry */ }
    setTimeout(poll, POLL_MS);
  };
  setTimeout(poll, POLL_MS);
  return card;
}

// ---------- Parameters ----------

function parametersCard(ctx, params) {
  const p = params.parameters;
  const row = (label, key, format) => h("div", { class: "param" },
    h("span", { class: "muted small" }, label),
    h("b", { class: "mono" }, format(p[key].value)),
    p[key].overridden ? h("span", { class: "badge badge-outline", title: `Valore del file .env: ${format(p[key].default)}` }, "modificato dalla dashboard") : h("span", { class: "muted small" }, "dal file .env"));
  let reset = null;
  if (ctx.isAdmin() && Object.values(p).some((v) => v.overridden)) {
    reset = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, "Ripristina i valori del .env");
    reset.addEventListener("click", async () => {
      try { await api("/backtest/parameters/reset", { method: "POST" }); toast("Valori del .env ripristinati"); ctx.rerender(); }
      catch (e) { toast(e.message, { error: true }); }
    });
  }
  return h("section", { class: "card", "aria-labelledby": "h-bt-params", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("h2", { id: "h-bt-params" }, "Parametri in uso"), reset),
    h("div", { class: "params" },
      row("Peso massimo di Jev", "MODEL_WEIGHT_MAX", fmt.pct),
      row("Edge minimo per un segnale", "MIN_EDGE", (v) => fmt.pts(v).replace("+", "")),
      h("div", { class: "param" },
        h("span", { class: "muted small" }, "Calibrazione di Jev"),
        h("b", { class: "mono" }, calibText(p.JEV_CALIB_A.value, p.JEV_CALIB_B.value)),
        p.JEV_CALIB_A.overridden || p.JEV_CALIB_B.overridden
          ? h("span", { class: "badge badge-outline" }, "stimata dal backtest") : h("span", { class: "muted small" }, "nessuna correzione"))));
}

function calibText(a, b) {
  if (Math.abs(a) < 1e-9 && Math.abs(b - 1) < 1e-9) return "nessuna";
  return `a ${fmt.num3(a)} · b ${fmt.num3(b)}${b < 1 ? " (Jev troppo sicuro)" : b > 1 ? " (Jev troppo prudente)" : ""}`;
}

// ---------- Runs ----------

function runsCard(runs, selectedId) {
  const rows = runs.map((r) => {
    const o = r.overall;
    const p = r.params || {};
    const href = `#/backtest/${r.id}`;
    return h("tr", { class: `clickable${r.id === selectedId ? " selected" : ""}`, on: { click: (e) => { if (!e.target.closest("a")) window.location.hash = href; } } },
      h("td", {}, h("a", { href, "aria-current": r.id === selectedId ? "true" : null }, fmt.dateTime(r.created_at))),
      h("td", { class: "nowrap small" }, `${fmt.date(p.resolved_after)} – ${fmt.date(p.resolved_before)}`),
      h("td", { class: "small" }, (p.horizons || []).map((d) => `${d} gg`).join(", ")),
      h("td", {}, statusBadge(r.status)),
      h("td", { class: "num" }, fmt.int(r.done)),
      h("td", { class: "num" }, o ? b3(o.brier_model) : "–"),
      h("td", { class: "num" }, o ? b3(o.brier_market) : "–"),
      h("td", { class: `num ${o?.pnl > 0 ? "pos" : o?.pnl < 0 ? "neg" : ""}` }, o?.bets ? fmt.signedMoney(o.pnl) : "–"),
    );
  });
  return h("section", { class: "card", "aria-labelledby": "h-bt-runs", style: { marginBottom: "16px" } },
    h("h2", { id: "h-bt-runs", style: { marginBottom: "10px" } }, "Backtest eseguiti"),
    h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
      h("thead", {}, h("tr", {}, ...["Avviato", "Mercati chiusi", "Orizzonti", "Stato", "Casi", "Brier Jev", "Brier prezzo", "Profitto"]
        .map((t, i) => h("th", { scope: "col", class: i >= 4 ? "num" : null }, t)))),
      h("tbody", {}, rows))));
}

// ---------- Results ----------

// Brier gain over the price with its 95% interval (bootstrap by market)
function gainText(g) {
  if (!g) return "–";
  const range = g.lo == null ? "" : ` (95%: da ${fmt.num3(g.lo)} a ${fmt.num3(g.hi)})`;
  return `${g.mean > 0 ? "+" : ""}${fmt.num3(g.mean)}${range}`;
}

function gainVerdict(g, who) {
  if (!g || g.lo == null) return null;
  if (g.lo > 0) return `${who} batte il prezzo anche nel caso peggiore dell'intervallo: il vantaggio non sembra dovuto al caso.`;
  if (g.hi < 0) return `${who} fa peggio del prezzo anche nel caso migliore dell'intervallo.`;
  return `L'intervallo comprende lo zero: con questi mercati non si può dire se ${who.toLowerCase()} batta davvero il prezzo.`;
}

function verdictLine(o) {
  if (!o.n) return "Nessun caso valutato.";
  const diff = o.brier_market - o.brier_model;
  if (Math.abs(diff) < 0.005) return "Jev e il prezzo di mercato hanno previsto con la stessa accuratezza.";
  return diff > 0
    ? `Jev ha previsto meglio del prezzo di mercato (Brier ${b3(o.brier_model)} contro ${b3(o.brier_market)}: più basso è meglio).`
    : `Il prezzo di mercato ha previsto meglio di Jev (Brier ${b3(o.brier_market)} contro ${b3(o.brier_model)}: più basso è meglio).`;
}

function metricsTable(rows, firstLabel, firstValue) {
  return h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
    h("thead", {}, h("tr", {}, ...[firstLabel, "Casi", "Brier prezzo", "Brier Jev", "Brier blended", "Segnali giusti", "Scommesse", "Profitto", "ROI"]
      .map((t, i) => h("th", { scope: "col", class: i ? "num" : null }, t)))),
    h("tbody", {}, rows.map((r) => {
      const best = Math.min(...[r.brier_market, r.brier_model, r.brier_blended].filter((v) => v != null));
      const cell = (v) => h("td", { class: `num${v === best ? " best" : ""}` }, b3(v));
      return h("tr", {},
        h("th", { scope: "row" }, firstValue(r)), h("td", { class: "num" }, fmt.int(r.n)),
        cell(r.brier_market), cell(r.brier_model), cell(r.brier_blended),
        h("td", { class: "num" }, r.signal_hit_rate == null ? "–" : `${fmt.pct(r.signal_hit_rate)} di ${r.signals}`),
        h("td", { class: "num" }, fmt.int(r.bets)),
        h("td", { class: `num ${r.pnl > 0 ? "pos" : r.pnl < 0 ? "neg" : ""}` }, r.bets ? fmt.signedMoney(r.pnl) : "–"),
        h("td", { class: "num" }, r.roi == null ? "–" : fmt.pct(r.roi)));
    }))));
}

async function resultsView(ctx, run, params) {
  const s = run.summary;
  if (!s || (!s.overall.n && !s.multi)) {
    return h("section", { class: "card" }, h("h2", {}, "Risultati"),
      h("p", { class: "secondary" }, run.message || "Nessun caso valutato con Jev in questo backtest."),
      s?.skipped && Object.keys(s.skipped).length ? skippedList(s.skipped) : null);
  }
  const o = s.overall;
  const allCases = await api(`/backtest/runs/${run.id}/cases`);
  const cases = allCases.filter((c) => c.kind !== "multi");
  const multiCases = allCases.filter((c) => c.kind === "multi");
  if (!o.n) return h("div", { class: "stack" }, multiCard(run, s.multi, multiCases), casesCard([], s.skipped));
  const small = o.markets < 30;
  const lf = s.leak_free;
  const sources = s.news_sources || {};
  return h("div", { class: "stack" },
    h("section", { class: "card", "aria-labelledby": "h-bt-res" },
      h("div", { class: "card-head" },
        h("div", {}, h("h2", { id: "h-bt-res" }, "Risultati"),
          h("p", { class: "muted small" }, `Backtest del ${fmt.dateTime(run.created_at)} · ${statusBadge(run.status).textContent}${run.message ? ` · ${run.message}` : ""}`)),
        infoTip("Brier score: errore medio al quadrato tra probabilità prevista ed esito (0 = perfetto, 0,25 = dire sempre 50%). Più basso è meglio.")),
      h("p", { style: { margin: "4px 0 4px" } }, verdictLine(o)),
      gainVerdict(o.gain_blended, "Il blended") ? h("p", { class: "secondary small", style: { margin: "0 0 12px" } }, gainVerdict(o.gain_blended, "Il blended")) : null,
      small ? h("p", { class: "notice small" }, icon("alert"), `Solo ${fmt.count(o.markets, "mercato", "mercati")} diversi: i numeri possono cambiare molto con altri mercati. Ne servono almeno 30, meglio 100.`) : null,
      h("div", { class: "kpis" },
        statTile("Casi valutati", fmt.int(o.n), `${fmt.count(o.markets, "mercato", "mercati")} · ${fmt.int(run.skipped)} saltati`),
        statTile("Brier Jev", b3(o.brier_model), `prezzo ${b3(o.brier_market)} · blended ${b3(o.brier_blended)}`),
        statTile("Vantaggio del blended sul prezzo", gainText(o.gain_blended), "Brier del prezzo − Brier del blended; > 0 è meglio"),
        statTile("Segnali giusti", o.signal_hit_rate == null ? "–" : fmt.pct(o.signal_hit_rate), `${fmt.count(o.signals, "segnale", "segnali")}`),
        statTile("Scommesse simulate", o.bets ? fmt.signedMoney(o.pnl) : "–", o.bets ? `${o.bets_won} vinte su ${o.bets} · ROI ${fmt.pct(o.roi)}` : "nessuna scommessa"),
      ),
      h("p", { class: "muted small", style: { marginTop: "10px" } },
        `Notizie: ${fmt.int(sources.archive || 0)} casi dall'archivio, ${fmt.int(sources.google || 0)} da Google News. `,
        lf ? `Solo archivio (nessun senno di poi): ${fmt.count(lf.n, "caso", "casi")}, Brier Jev ${b3(lf.brier_model)} contro prezzo ${b3(lf.brier_market)}, vantaggio del blended ${gainText(lf.gain_blended)}.`
          : "Nessun caso con le sole notizie dell'archivio: i risultati possono risentire del senno di poi."),
    ),
    s.by_horizon.length > 1 || s.by_category.length > 1 ? h("section", { class: "card", "aria-labelledby": "h-bt-split" },
      h("h2", { id: "h-bt-split", style: { marginBottom: "10px" } }, "Per orizzonte e per categoria"),
      metricsTable(s.by_horizon, "Previsione fatta", (r) => `${r.horizon_days} ${r.horizon_days === 1 ? "giorno" : "giorni"} prima`),
      h("div", { style: { height: "12px" } }),
      metricsTable(s.by_category, "Categoria", (r) => CATEGORY_LABELS[r.category] || r.category)) : null,
    h("section", { class: "card", "aria-labelledby": "h-bt-cal" },
      h("div", { class: "card-head" }, h("h2", { id: "h-bt-cal" }, "Calibrazione"),
        infoTip("Se Jev dice 70% per 10 mercati, circa 7 dovrebbero risolversi SÌ. I punti sopra la diagonale indicano previsioni troppo prudenti, quelli sotto troppo ottimiste.")),
      reliabilityChart(s.calibration)),
    suggestionCard(ctx, s.suggestion, params),
    casesCard(cases, s.skipped),
    s.multi ? multiCard(run, s.multi, multiCases) : null,
  );
}

function multiSignalCell(c) {
  if (c.signal !== "BUY_YES" && c.signal !== "BUY_NO") return h("span", { class: "muted small" }, "Attendi");
  const right = (c.details?.best === c.details?.winner) === (c.signal === "BUY_YES");
  return h("span", { class: right ? "pos" : "neg" }, icon(right ? "check" : "x"), ` ${c.signal === "BUY_YES" ? "SÌ" : "NO"} su ${c.details?.best}`);
}

function multiCard(run, m, cases) {
  const b = (v) => (v == null ? "–" : fmt.num3(v));
  const better = m.brier_model < m.brier_market - 0.005 ? "Jev ha previsto meglio del mercato"
    : m.brier_model > m.brier_market + 0.005 ? "Il mercato ha previsto meglio di Jev" : "Jev e il mercato hanno previsto con la stessa accuratezza";
  const rows = [...cases].sort((a, c) => Math.abs(c.pnl ?? 0) - Math.abs(a.pnl ?? 0)).slice(0, 30).map((c) => h("tr", {},
    h("td", { class: "q-cell" }, c.url ? externalLink(c.url, c.question) : c.question),
    h("td", { class: "num nowrap" }, `${c.horizon_days} gg`, h("div", { class: "muted small" }, fmt.date(c.as_of))),
    h("td", {}, c.details?.winner || "–"),
    h("td", { class: "num" }, fmt.pct(c.price)), h("td", { class: "num" }, fmt.pct(c.model_probability)),
    h("td", {}, multiSignalCell(c)),
    h("td", { class: `num ${c.pnl > 0 ? "pos" : c.pnl < 0 ? "neg" : ""}` }, c.pnl == null ? "–" : fmt.signedMoney(c.pnl))));
  return h("section", { class: "card", "aria-labelledby": "h-bt-multi" },
    h("div", { class: "card-head" }, h("h2", { id: "h-bt-multi" }, "Mercati a più esiti"),
      infoTip("Brier a più esiti: somma degli errori al quadrato su tutti gli esiti (0 = perfetto, 2 = certo e sbagliato). «Favorito giusto»: quante volte l'esito più probabile ha vinto davvero.")),
    h("p", { style: { margin: "4px 0 4px" } }, `${better} (Brier ${b(m.brier_model)} contro ${b(m.brier_market)}: più basso è meglio).`),
    h("p", { class: "secondary small", style: { margin: "0 0 12px" } },
      `Vantaggio del blended sul prezzo: ${gainText(m.gain_blended)}. `, gainVerdict(m.gain_blended, "Il blended") || "",
      m.winner_listed != null && m.winner_listed < 1 ? ` In ${fmt.pct(1 - m.winner_listed)} degli eventi il vincitore era tra gli «altri esiti» (poco probabile per il prezzo di allora).` : ""),
    m.n < 30 ? h("p", { class: "notice small" }, icon("alert"), `Solo ${m.n} eventi: servono molti più casi per conclusioni affidabili.`) : null,
    h("div", { class: "kpis" },
      statTile("Eventi valutati", fmt.int(m.n), `blended ${b(m.brier_blended)}`),
      statTile("Probabilità data al vincitore", fmt.pct(m.winner_prob_model), `Jev · mercato ${fmt.pct(m.winner_prob_market)}`),
      statTile("Favorito giusto", fmt.pct(m.favourite_right_model), `Jev · mercato ${fmt.pct(m.favourite_right_market)}`),
      statTile("Scommesse simulate", m.bets ? fmt.signedMoney(m.pnl) : "–", m.bets ? `${m.bets_won} vinte su ${m.bets} · ROI ${fmt.pct(m.roi)}` : "nessuna scommessa")),
    cases.length ? h("div", { class: "table-wrap", style: { marginTop: "12px" } }, h("table", { class: "compact-table cases-table" },
      h("thead", {}, h("tr", {}, ...["Evento", "Quando", "Ha vinto", "Mercato sul vincitore", "Jev sul vincitore", "Segnale", "Scommessa"]
        .map((t, i) => h("th", { scope: "col", class: [1, 3, 4, 6].includes(i) ? "num" : null }, t)))),
      h("tbody", {}, rows))) : null);
}

function suggestionCard(ctx, sg, params) {
  if (!sg) return null;
  const w = sg.model_weight_max;
  const e = sg.min_edge;
  const c = sg.calibration;
  const v = sg.validation || {};
  const sameW = Math.abs(w.suggested - w.current) < 0.001;
  const sameE = e.suggested == null || Math.abs(e.suggested - e.current) < 0.001;
  const sameC = Math.abs(c.suggested.a - c.current.a) < 0.001 && Math.abs(c.suggested.b - c.current.b) < 0.001;
  const useBlend = c.recommended && (!sameW || !sameC);
  const useEdge = e.recommended && !sameE;
  const conf = { bassa: "badge-critical", media: "", alta: "badge-good" }[sg.confidence];
  let apply = null;
  if (ctx.isAdmin() && (useBlend || useEdge)) {
    apply = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Applica i valori consigliati");
    apply.addEventListener("click", async () => {
      const body = { note: `Backtest su ${sg.markets} mercati, verificato sui più recenti` };
      if (useBlend) Object.assign(body, { MODEL_WEIGHT_MAX: w.suggested, JEV_CALIB_A: c.suggested.a, JEV_CALIB_B: c.suggested.b });
      if (useEdge) body.MIN_EDGE = e.suggested;
      ctx.setBusy(apply, true);
      try { await api("/backtest/parameters", { method: "PUT", body }); toast("Parametri aggiornati: valgono per le prossime previsioni"); ctx.rerender(); }
      catch (err) { toast(err.message, { error: true }); ctx.setBusy(apply, false); }
    });
  }
  const status = (recommended, same) => same ? "il valore attuale è già il migliore"
    : !sg.validated ? "non verificato: servono più mercati"
    : recommended ? "consigliato: migliora anche sui mercati più recenti" : "non consigliato: sui mercati più recenti non migliora";
  const edgeRows = e.table.filter((r) => r.bets).map((r) => h("tr", { class: r.min_edge === e.suggested ? "best-row" : null },
    h("td", { class: "num" }, `${Math.round(r.min_edge * 100)} pt`), h("td", { class: "num" }, fmt.int(r.bets)),
    h("td", { class: "num" }, fmt.int(r.wins)), h("td", { class: `num ${r.profit > 0 ? "pos" : r.profit < 0 ? "neg" : ""}` }, fmt.signedMoney(r.profit)),
    h("td", { class: "num" }, r.roi == null ? "–" : fmt.pct(r.roi))));
  const check = sg.validated
    ? `Verifica: parametri stimati sui ${v.train_markets} mercati chiusi prima e provati sugli altri ${v.test_markets}. `
      + `Brier del blended su questi ultimi: ${b3(v.brier_test_current)} con i valori attuali, ${b3(v.brier_test_suggested)} con quelli stimati.`
    : `Non verificabile: servono almeno 10 mercati sia per stimare sia per provare (qui ${v.train_markets ?? 0} + ${v.test_markets ?? 0}). Nessun valore viene consigliato.`;
  return h("section", { class: "card", "aria-labelledby": "h-bt-sug" },
    h("div", { class: "card-head" }, h("h2", { id: "h-bt-sug" }, "Parametri suggeriti"),
      h("span", { class: `badge ${conf}` }, `affidabilità ${sg.confidence} · ${fmt.count(sg.markets, "mercato", "mercati")}`)),
    h("div", { class: "params" },
      h("div", { class: "param" }, h("span", { class: "muted small" }, "Calibrazione di Jev"),
        h("b", { class: "mono" }, `${calibText(c.current.a, c.current.b)} → ${calibText(c.suggested.a, c.suggested.b)}`),
        h("span", { class: "muted small" }, status(c.recommended, sameC))),
      h("div", { class: "param" }, h("span", { class: "muted small" }, "Peso massimo di Jev"),
        h("b", { class: "mono" }, `${fmt.pct(w.current)} → ${fmt.pct(w.suggested)}`),
        h("span", { class: "muted small" }, status(w.recommended, sameW && sameC))),
      h("div", { class: "param" }, h("span", { class: "muted small" }, "Edge minimo"),
        h("b", { class: "mono" }, e.suggested == null ? "–" : `${Math.round(e.current * 100)} → ${Math.round(e.suggested * 100)} pt`),
        h("span", { class: "muted small" }, e.suggested == null ? "servono almeno 10 scommesse per suggerirlo" : status(e.recommended, sameE)))),
    h("p", { class: "small", style: { marginTop: "8px" } }, check),
    edgeRows.length ? h("details", { class: "chart-table" }, h("summary", {}, "Risultato per soglia di edge (1 $ per segnale, tutti i mercati)"),
      h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
        h("thead", {}, h("tr", {}, ...["Edge minimo", "Scommesse", "Vinte", "Profitto", "ROI"].map((t) => h("th", { scope: "col", class: "num" }, t)))),
        h("tbody", {}, edgeRows)))) : null,
    h("p", { class: "muted small", style: { marginTop: "8px" } },
      "La calibrazione corregge le probabilità di Jev (troppo sicure o troppo prudenti) prima di unirle al prezzo. ",
      "Un valore è consigliato solo se, stimato sui mercati più vecchi, migliora anche i più recenti; i valori mostrati sono poi ristimati su tutti. ",
      "Valgono per le previsioni future; puoi tornare ai valori del .env quando vuoi."),
    apply ? h("div", { class: "actions", style: { marginTop: "10px" } }, apply) : null,
  );
}

function skippedList(skipped) {
  return h("ul", { class: "bullets small" }, Object.entries(skipped).map(([k, n]) => h("li", {}, `${k}: ${fmt.int(n)}`)));
}

const CASES_PAGE = 15;

function casesCard(cases, skipped) {
  const rowOf = (c) => {
    const outcome = c.resolved_yes ? "SÌ" : "NO";
    const right = c.signal === "HOLD" ? null : (c.signal === "BUY_YES") === c.resolved_yes;
    return h("tr", {},
      h("td", { class: "q-cell" }, c.url ? externalLink(c.url, c.question) : c.question,
        c.news?.length ? h("details", { class: "small" }, h("summary", {}, fmt.count(c.news.length, "notizia", "notizie")),
          h("ul", { class: "bullets" }, c.news.map((n) => h("li", {}, `${n.title} · ${n.source} · ${fmt.date(n.published_at)}`)))) : null),
      h("td", { class: "num nowrap" }, `${c.horizon_days} gg`, h("div", { class: "muted small" }, fmt.date(c.as_of))),
      h("td", { class: "num" }, fmt.cents(c.price)),
      h("td", { class: "num" }, fmt.pct(c.model_probability)),
      h("td", { class: "num" }, fmt.pct(c.blended_probability)),
      h("td", { class: "num" }, h("b", {}, outcome)),
      h("td", { class: "nowrap" }, c.signal === "HOLD" ? h("span", { class: "muted small" }, "Attendi")
        : h("span", { class: right ? "pos" : "neg" }, icon(right ? "check" : "x"), c.signal === "BUY_YES" ? " Compra SÌ" : " Compra NO")),
      h("td", { class: `num ${c.pnl > 0 ? "pos" : c.pnl < 0 ? "neg" : ""}` }, c.pnl == null ? "–" : fmt.signedMoney(c.pnl)),
    );
  };
  // Biggest wins and losses first, then the rest: the interesting cases are on top
  const sorted = [...cases].sort((a, b) => Math.abs(b.pnl ?? 0) - Math.abs(a.pnl ?? 0));
  const tbody = h("tbody", {}, sorted.slice(0, CASES_PAGE).map(rowOf));
  let more = null;
  if (sorted.length > CASES_PAGE) {
    more = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, `Mostra tutti i ${sorted.length} casi`);
    more.addEventListener("click", () => { tbody.replaceChildren(...sorted.map(rowOf)); more.remove(); });
  }
  return h("section", { class: "card", "aria-labelledby": "h-bt-cases" },
    h("div", { class: "card-head" }, h("h2", { id: "h-bt-cases" }, `Casi valutati (${cases.length})`),
      h("span", { class: "muted small" }, "prima le vincite e le perdite più grandi")),
    h("div", { class: "table-wrap" }, h("table", { class: "compact-table cases-table" },
      h("thead", {}, h("tr", {}, ...["Mercato", "Quando", "Prezzo", "Jev", "Blended", "Esito", "Segnale", "Scommessa"]
        .map((t, i) => h("th", { scope: "col", class: i && i !== 6 ? "num" : null }, t)))),
      tbody)),
    more ? h("div", { style: { marginTop: "10px" } }, more) : null,
    skipped && Object.keys(skipped).length ? h("div", { style: { marginTop: "10px" } }, h("p", { class: "muted small" }, "Casi saltati:"), skippedList(skipped)) : null,
  );
}
