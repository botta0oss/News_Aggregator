// Charts. Series identity is fixed everywhere:
//   blended = blue (the probability behind the signal), market price = orange, raw Jev = aqua.
// Identity never relies on colour alone: every chart has a legend, marker shapes differ
// (circle / diamond), the blended line is dashed and every chart has a table or value labels.
import { t, locale } from "./i18n.js";
import { h, fmt, withTooltip, ttRows, showTooltip, hideTooltip } from "./ui.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, String(v));
  for (const c of children.flat()) if (c) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

const clamp01 = (v) => Math.max(0, Math.min(1, v));

/** Legend with the current values: the visible label channel for every series. */
export function probLegend({ market, blended, jev }) {
  return h("div", { class: "legend" },
    h("span", {}, h("span", { class: "key market" }), t("Prezzo mercato "), h("b", {}, fmt.cents(market))),
    blended != null ? h("span", {}, h("span", { class: "key blended" }), t("Blended "), h("b", {}, fmt.pct(blended))) : null,
    jev != null ? h("span", {}, h("span", { class: "key jev" }), t("Stima Jev "), h("b", {}, fmt.pct(jev))) : null,
  );
}

/** 0–100 % track comparing market price, blended probability and raw Jev estimate. */
export function probTrack({ market, blended, jev }) {
  const marks = [];
  const tip = () => ttRows(t("Probabilità del SÌ"), [
    ["market", t("Prezzo mercato"), fmt.cents(market)],
    ...(blended != null ? [["blended", t("Blended"), fmt.pct(blended)]] : []),
    ...(jev != null ? [["jev", t("Stima Jev"), fmt.pct(jev)]] : []),
  ]);
  const mark = (cls, value, label) => {
    if (value == null) return null;
    const el = h("span", { class: `pmark ${cls}`, style: { left: `${clamp01(value) * 100}%` }, tabindex: "0", "aria-label": `${label}: ${fmt.pct(value)}` });
    marks.push(el);
    return withTooltip(el, tip);
  };
  let edge = null;
  if (blended != null && market != null) {
    const lo = Math.min(market, blended), hi = Math.max(market, blended);
    edge = h("span", { class: "ptrack-edge", style: { left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` } });
  }
  return h("div", { class: "ptrack" },
    h("div", { class: "ptrack-bar", role: "img", "aria-label": t("Prezzo {0}{1}{2}", fmt.pct(market), blended != null ? t(", blended {0}", fmt.pct(blended)) : "", jev != null ? t(", Jev {0}", fmt.pct(jev)) : "") },
      h("div", { class: "ptrack-line" }),
      h("div", { class: "ptrack-ticks" }, [0, 25, 50, 75, 100].map((x) => h("span", { style: { left: `${x}%` } }))),
      edge,
      mark("jev", jev, t("Stima Jev")),
      mark("market", market, t("Prezzo mercato")),
      mark("blended", blended, t("Blended")),
    ),
    h("div", { class: "ptrack-axis", "aria-hidden": "true" }, ["0%", "25%", "50%", "75%", "100%"].map((x) => h("span", {}, x))),
  );
}

/**
 * Prediction history: market price (solid) vs blended probability (dashed) over time,
 * raw Jev as diamonds. Crosshair tooltip, direct end labels, and a table view.
 */
export function historyChart(predictions) {
  const points = [...predictions]
    .map((p) => ({ t: new Date(p.created_at).getTime(), market: p.market_probability, blended: p.blended_probability, jev: p.model_probability, signal: p.signal }))
    .sort((a, b) => a.t - b.t);

  const wrap = h("div", { class: "chart" });
  const legend = h("div", { class: "chart-legend" },
    h("span", {}, h("span", { class: "key line market" }), t("Prezzo mercato")),
    h("span", {}, h("span", { class: "key line blended" }), t("Blended")),
    h("span", {}, h("span", { class: "key jev" }), t("Stima Jev")),
  );
  const holder = h("div", {});
  const table = historyTable(points);
  table.hidden = true;
  const toggle = h("button", { class: "link-btn", type: "button", "aria-expanded": "false" }, t("Mostra tabella"));
  toggle.addEventListener("click", () => {
    table.hidden = !table.hidden;
    toggle.textContent = table.hidden ? t("Mostra tabella") : t("Nascondi tabella");
    toggle.setAttribute("aria-expanded", String(!table.hidden));
  });
  wrap.append(legend, holder, toggle, table);

  const draw = () => {
    const width = Math.max(280, holder.clientWidth || 600);
    const height = 220;
    const m = { l: 40, r: 64, t: 12, b: 26 };
    const iw = width - m.l - m.r, ih = height - m.t - m.b;
    const t0 = points[0].t, t1 = points[points.length - 1].t;
    const x = (t) => m.l + (t1 === t0 ? iw / 2 : ((t - t0) / (t1 - t0)) * iw);
    const y = (v) => m.t + (1 - clamp01(v)) * ih;

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": t("Storico di {0} previsioni: prezzo di mercato e probabilità blended", points.length) });
    for (const v of [0, 0.25, 0.5, 0.75, 1]) {
      svg.append(s("line", { class: v === 0 ? "baseline" : "gridline", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      svg.append(s("text", { class: "tick", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, `${v * 100}%`));
    }
    const ticks = t1 === t0 ? [t0] : [t0, t0 + (t1 - t0) / 2, t1];
    ticks.forEach((t, i) => svg.append(s("text", {
      class: "tick", x: x(t), y: height - 6,
      "text-anchor": ticks.length === 1 ? "middle" : i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle",
    }, fmt.dateTime(t))));

    const path = (key) => points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
    if (points.length > 1) {
      svg.append(s("path", { d: path("market"), fill: "none", stroke: "var(--series-market)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      svg.append(s("path", { d: path("blended"), fill: "none", stroke: "var(--series-blended)", "stroke-width": 2, "stroke-dasharray": "6 4", "stroke-linejoin": "round", "stroke-linecap": "round" }));
    }
    for (const p of points) {
      const cx = x(p.t), cy = y(p.jev);
      svg.append(s("rect", { x: cx - 4, y: cy - 4, width: 8, height: 8, fill: "var(--series-jev)", stroke: "var(--surface)", "stroke-width": 2, transform: `rotate(45 ${cx} ${cy})` }));
    }
    const last = points[points.length - 1];
    for (const key of ["market", "blended"]) {
      svg.append(s("circle", { cx: x(last.t), cy: y(last[key]), r: 4.5, fill: `var(--series-${key})`, stroke: "var(--surface)", "stroke-width": 2 }));
    }
    // Direct end labels only when they don't collide; the legend and tooltip carry them otherwise
    if (Math.abs(y(last.market) - y(last.blended)) >= 16) {
      svg.append(s("text", { class: "endvalue", x: x(last.t) + 10, y: y(last.market) + 4 }, fmt.cents(last.market)));
      svg.append(s("text", { class: "endvalue", x: x(last.t) + 10, y: y(last.blended) + 4 }, fmt.pct(last.blended)));
    }

    const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    svg.append(cross);
    const overlay = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    overlay.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * width;
      let best = points[0];
      for (const p of points) if (Math.abs(x(p.t) - px) < Math.abs(x(best.t) - px)) best = p;
      cross.setAttribute("x1", x(best.t));
      cross.setAttribute("x2", x(best.t));
      cross.setAttribute("visibility", "visible");
      showTooltip(ttRows(fmt.dateTime(best.t), [
        ["market", t("Prezzo mercato"), fmt.cents(best.market)],
        ["blended", t("Blended"), fmt.pct(best.blended)],
        ["jev", t("Stima Jev"), fmt.pct(best.jev)],
      ]), e.clientX, e.clientY);
    });
    overlay.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTooltip(); });
    svg.append(overlay);

    holder.replaceChildren(svg);
  };

  requestAnimationFrame(draw);
  if ("ResizeObserver" in window) {
    let lastWidth = 0;
    new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w && w !== lastWidth) { lastWidth = w; draw(); }
    }).observe(holder);
  }
  return wrap;
}

function historyTable(points) {
  return h("div", { class: "table-wrap", style: { marginTop: "8px" } },
    h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, t("Data")), h("th", { class: "num" }, t("Prezzo")), h("th", { class: "num" }, t("Jev")),
        h("th", { class: "num" }, t("Blended")), h("th", {}, t("Segnale")),
      )),
      h("tbody", {}, [...points].reverse().map((p) => h("tr", {},
        h("td", {}, fmt.dateTime(p.t)), h("td", { class: "num" }, fmt.cents(p.market)), h("td", { class: "num" }, fmt.pct(p.jev)),
        h("td", { class: "num" }, fmt.pct(p.blended)), h("td", {}, p.signal),
      ))),
    ),
  );
}

/** Horizontal bars of Brier scores (lower is better) with the 0.25 "always 50%" reference. */
export function brierBars(rows) {
  const values = rows.map((r) => r.value).filter((v) => v != null);
  const max = Math.max(0.3, ...values) * 1.1;
  const ref = 0.25;
  return h("div", { class: "bars", role: "list" },
    rows.map((r) => {
      const w = r.value == null ? 0 : (r.value / max) * 100;
      const valueLabel = fmt.num3(r.value);
      return h("div", { class: "bar-row", role: "listitem", "aria-label": `${r.label}: ${valueLabel}` },
        h("div", { class: "bar-label" }, h("span", { class: `key ${r.key}` }), r.label),
        h("div", { class: "bar-area" },
          h("div", { class: `bar ${r.key}`, style: { width: `${w}%` } }),
          h("span", { class: "bar-value", style: { left: `calc(${w}% + 8px)` } }, valueLabel),
          r.first ? [
            h("div", { class: "bar-ref", style: { left: `${(ref / max) * 100}%` } }),
            h("span", { class: "bar-ref-label", style: { left: `${(ref / max) * 100}%` } }, t("0,25 = sempre 50%")),
          ] : h("div", { class: "bar-ref", style: { left: `${(ref / max) * 100}%` } }),
        ),
      );
    }),
  );
}

/** Equity curve of the simulated portfolio: one series, dashed reference at the initial capital. */
export function equityChart(points, initial) {
  const data = points.map((p) => ({ t: new Date(p.t).getTime(), v: p.equity })).sort((a, b) => a.t - b.t);
  const wrap = h("div", { class: "chart" });
  const holder = h("div", {});
  wrap.append(holder);

  const draw = () => {
    const width = Math.max(280, holder.clientWidth || 600);
    const height = 200;
    const m = { l: 56, r: 72, t: 12, b: 26 };
    const iw = width - m.l - m.r, ih = height - m.t - m.b;
    const values = data.map((d) => d.v).concat(initial);
    let lo = Math.min(...values), hi = Math.max(...values);
    const pad = Math.max((hi - lo) * 0.15, initial * 0.02);
    lo -= pad; hi += pad;
    const t0 = data[0].t, t1 = data[data.length - 1].t;
    const x = (t) => m.l + (t1 === t0 ? iw / 2 : ((t - t0) / (t1 - t0)) * iw);
    const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * ih;

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img",
      "aria-label": t("Capitale da {0} a {1}", fmt.money(data[0].v), fmt.money(data[data.length - 1].v)) });
    const ticks = [lo + (hi - lo) * 0.1, (lo + hi) / 2, hi - (hi - lo) * 0.1];
    for (const v of ticks) {
      svg.append(s("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      svg.append(s("text", { class: "tick", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, Math.round(v).toLocaleString(locale)));
    }
    svg.append(s("line", { x1: m.l, x2: m.l + iw, y1: y(initial), y2: y(initial), stroke: "var(--axis)", "stroke-dasharray": "4 4" }));
    svg.append(s("text", { class: "tick", x: m.l + iw + 8, y: y(initial) + 4 }, "iniziale"));
    const line = data.map((d, i) => `${i ? "L" : "M"}${x(d.t).toFixed(1)},${y(d.v).toFixed(1)}`).join("");
    if (data.length > 1) {
      svg.append(s("path", { d: `${line}L${x(t1)},${m.t + ih}L${x(t0)},${m.t + ih}Z`, fill: "var(--series-blended)", opacity: 0.1 }));
      svg.append(s("path", { d: line, fill: "none", stroke: "var(--series-blended)", "stroke-width": 2, "stroke-linejoin": "round" }));
    }
    const last = data[data.length - 1];
    svg.append(s("circle", { cx: x(last.t), cy: y(last.v), r: 4.5, fill: "var(--series-blended)", stroke: "var(--surface)", "stroke-width": 2 }));
    svg.append(s("text", { class: "endvalue", x: x(last.t) + 10, y: y(last.v) + 4 }, fmt.dollars(Math.round(last.v).toLocaleString(locale))));
    [t0, t1].forEach((t, i) => svg.append(s("text", { class: "tick", x: x(t), y: height - 6, "text-anchor": i ? "end" : "start" }, fmt.date(t))));

    const cross = s("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    svg.append(cross);
    const overlay = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    overlay.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * width;
      let best = data[0];
      for (const d of data) if (Math.abs(x(d.t) - px) < Math.abs(x(best.t) - px)) best = d;
      cross.setAttribute("x1", x(best.t)); cross.setAttribute("x2", x(best.t)); cross.setAttribute("visibility", "visible");
      showTooltip(ttRows(fmt.dateTime(best.t), [["blended", t("Capitale"), fmt.money(best.v)], [null, t("Da inizio"), fmt.signedMoney(best.v - initial)]]), e.clientX, e.clientY);
    });
    overlay.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTooltip(); });
    svg.append(overlay);
    holder.replaceChildren(svg);
  };
  requestAnimationFrame(draw);
  if ("ResizeObserver" in window) {
    let lastWidth = 0;
    new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w && w !== lastWidth) { lastWidth = w; draw(); }
    }).observe(holder);
  }
  return wrap;
}

/**
 * Reliability diagram: for each probability bin, mean forecast (x) vs how often YES happened (y).
 * On the diagonal = well calibrated. Point size grows with the number of cases in the bin.
 * Jev = aqua diamonds (solid), blended = blue circles (dashed), market = orange circles (thin).
 */
export function reliabilityChart(calibration) {
  const series = [
    ["market", t("Prezzo mercato"), calibration.market, "circle", "var(--series-market)", null, 1.5],
    ["jev", t("Stima Jev"), calibration.model, "diamond", "var(--series-jev)", null, 2],
    ["blended", t("Blended"), calibration.blended, "circle", "var(--series-blended)", "5 4", 2],
  ];
  const maxN = Math.max(1, ...series.flatMap(([, , bins]) => bins.map((b) => b.n)));
  const wrap = h("div", { class: "chart reliability" });
  const holder = h("div", {});
  wrap.append(holder);

  const draw = () => {
    const width = Math.min(520, Math.max(260, holder.clientWidth || 420));
    const height = Math.round(width * 0.8);
    const m = { l: 44, r: 12, t: 12, b: 34 };
    const iw = width - m.l - m.r, ih = height - m.t - m.b;
    const x = (v) => m.l + clamp01(v) * iw;
    const y = (v) => m.t + (1 - clamp01(v)) * ih;
    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img",
      "aria-label": t("Calibrazione: probabilità prevista contro frequenza reale del SÌ") });
    for (const v of [0, 0.25, 0.5, 0.75, 1]) {
      svg.append(s("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      svg.append(s("text", { class: "tick", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, `${v * 100}%`));
      svg.append(s("text", { class: "tick", x: x(v), y: m.t + ih + 16, "text-anchor": "middle" }, `${v * 100}%`));
    }
    svg.append(s("text", { class: "tick", x: m.l + iw / 2, y: height - 2, "text-anchor": "middle" }, t("probabilità prevista")));
    svg.append(s("line", { x1: x(0), y1: y(0), x2: x(1), y2: y(1), stroke: "var(--axis)", "stroke-dasharray": "3 4" }));
    svg.append(s("text", { class: "tick", x: x(0.97), y: y(0.97) + 16, "text-anchor": "end" }, t("calibrazione perfetta")));

    for (const [key, label, bins, shape, color, dash, sw] of series) {
      if (!bins.length) continue;
      const d = bins.map((b, i) => `${i ? "L" : "M"}${x(b.mean_forecast).toFixed(1)},${y(b.frequency).toFixed(1)}`).join("");
      svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": sw, "stroke-dasharray": dash, opacity: 0.9 }));
      for (const b of bins) {
        const r = 3 + 5 * Math.sqrt(b.n / maxN);
        const cx = x(b.mean_forecast), cy = y(b.frequency);
        const mark = shape === "diamond"
          ? s("path", { d: `M${cx},${cy - r - 1}L${cx + r + 1},${cy}L${cx},${cy + r + 1}L${cx - r - 1},${cy}Z`, fill: color, stroke: "var(--surface)", "stroke-width": 1.5 })
          : s("circle", { cx, cy, r, fill: key === "market" ? "var(--surface)" : color, stroke: color, "stroke-width": 2 });
        mark.setAttribute("tabindex", "0");
        mark.setAttribute("aria-label", t("{0}: previsto {1}, accaduto {2} su {3} casi", label, fmt.pct(b.mean_forecast), fmt.pct(b.frequency), b.n));
        const tip = (e) => showTooltip(ttRows(label, [[key, t("Previsto in media"), fmt.pct(b.mean_forecast)], [null, t("SÌ accaduto"), fmt.pct(b.frequency)], [null, t("Casi"), fmt.int(b.n)]]), e.clientX, e.clientY);
        mark.addEventListener("mousemove", tip);
        mark.addEventListener("mouseleave", hideTooltip);
        mark.addEventListener("focus", () => { const rc = mark.getBoundingClientRect(); tip({ clientX: rc.right, clientY: rc.top }); });
        mark.addEventListener("blur", hideTooltip);
        svg.append(mark);
      }
    }
    holder.replaceChildren(svg);
  };
  requestAnimationFrame(draw);
  if ("ResizeObserver" in window) {
    let lastWidth = 0;
    new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w && w !== lastWidth) { lastWidth = w; draw(); }
    }).observe(holder);
  }

  const legend = h("div", { class: "legend" },
    h("span", {}, h("span", { class: "key market" }), t("Prezzo mercato")),
    h("span", {}, h("span", { class: "key jev" }), t("Stima Jev")),
    h("span", {}, h("span", { class: "key blended" }), t("Blended")),
    h("span", { class: "muted small" }, t("punti più grandi = più casi")));
  const rows = [];
  series.forEach(([key, label, bins]) => bins.forEach((b) => rows.push(h("tr", {},
    h("td", {}, label), h("td", { class: "num" }, `${Math.round(b.lo * 100)}–${Math.round(b.hi * 100)}%`),
    h("td", { class: "num" }, fmt.pct(b.mean_forecast)), h("td", { class: "num" }, fmt.pct(b.frequency)), h("td", { class: "num" }, fmt.int(b.n))))));
  const table = h("details", { class: "chart-table" }, h("summary", {}, t("Mostra tabella")),
    h("div", { class: "table-wrap" }, h("table", { class: "compact-table" },
      h("thead", {}, h("tr", {}, ...[t("Serie"), t("Fascia"), t("Previsto"), t("Accaduto"), t("Casi")].map((t, i) => h("th", { scope: "col", class: i ? "num" : null }, t)))),
      h("tbody", {}, rows))));
  return h("div", {}, legend, wrap, table);
}

/**
 * Daily bars, one series (magnitude over time): rounded tops anchored to the baseline,
 * 2px gaps, recessive grid, hover tooltip per day, direct label only on the highest bar.
 * `days`: [{day: "2026-09-01", value, tip: [[key, label, value], ...]}]
 */
export function dailyBars(days, { format, label }) {
  const wrap = h("div", { class: "chart" });
  const holder = h("div", {});
  wrap.append(holder);
  const draw = () => {
    const width = Math.max(280, holder.clientWidth || 600);
    const height = 180;
    const m = { l: 48, r: 8, t: 18, b: 24 };
    const iw = width - m.l - m.r, ih = height - m.t - m.b;
    const max = Math.max(...days.map((d) => d.value), 0) || 1;
    const step = iw / days.length;
    const bw = Math.max(2, step - 2);
    const y = (v) => m.t + ih - (v / max) * ih;
    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": label });
    for (const f of [0, 0.5, 1]) {
      svg.append(s("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: y(max * f), y2: y(max * f) }));
      svg.append(s("text", { class: "tick", x: m.l - 8, y: y(max * f) + 4, "text-anchor": "end" }, format(max * f)));
    }
    const peak = days.reduce((a, b) => (b.value > a.value ? b : a), days[0]);
    days.forEach((d, i) => {
      const x = m.l + i * step + 1;
      const hgt = Math.max(0, (d.value / max) * ih);
      if (hgt > 0) {
        const r = Math.min(4, bw / 2, hgt);
        const top = m.t + ih - hgt;
        svg.append(s("path", { fill: "var(--series-blended)",
          d: `M${x},${m.t + ih}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${m.t + ih}Z` }));
      }
      if (d === peak && d.value > 0) {
        svg.append(s("text", { class: "endvalue", x: x + bw / 2, y: m.t + ih - hgt - 5, "text-anchor": "middle" }, format(d.value)));
      }
      const hit = s("rect", { x: m.l + i * step, y: m.t, width: step, height: ih, fill: "transparent", tabindex: "0",
        "aria-label": `${fmt.date(d.day)}: ${format(d.value)}` });
      const show = (cx, cy) => showTooltip(ttRows(fmt.date(d.day), [[null, t("Totale"), format(d.value)], ...d.tip]), cx, cy);
      hit.addEventListener("mousemove", (e) => show(e.clientX, e.clientY));
      hit.addEventListener("mouseleave", hideTooltip);
      hit.addEventListener("focus", () => { const rc = hit.getBoundingClientRect(); show(rc.right, rc.top); });
      hit.addEventListener("blur", hideTooltip);
      svg.append(hit);
    });
    [0, days.length - 1].forEach((i, k) => svg.append(s("text", { class: "tick", x: m.l + i * step + (k ? step : 0), y: height - 6, "text-anchor": k ? "end" : "start" }, fmt.date(days[i].day))));
    holder.replaceChildren(svg);
  };
  requestAnimationFrame(draw);
  if ("ResizeObserver" in window) {
    let lastWidth = 0;
    new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w && w !== lastWidth) { lastWidth = w; draw(); }
    }).observe(holder);
  }
  return wrap;
}
