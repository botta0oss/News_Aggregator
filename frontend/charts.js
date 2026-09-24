// Charts. Series identity is fixed everywhere:
//   blended = blue (the probability behind the signal), market price = orange, raw Jev = aqua.
// Identity never relies on colour alone: every chart has a legend, marker shapes differ
// (circle / diamond), the blended line is dashed and every chart has a table or value labels.
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
    h("span", {}, h("span", { class: "key market" }), "Prezzo mercato ", h("b", {}, fmt.cents(market))),
    blended != null ? h("span", {}, h("span", { class: "key blended" }), "Blended ", h("b", {}, fmt.pct(blended))) : null,
    jev != null ? h("span", {}, h("span", { class: "key jev" }), "Stima Jev ", h("b", {}, fmt.pct(jev))) : null,
  );
}

/** 0–100 % track comparing market price, blended probability and raw Jev estimate. */
export function probTrack({ market, blended, jev }) {
  const marks = [];
  const tip = () => ttRows("Probabilità del SÌ", [
    ["market", "Prezzo mercato", fmt.cents(market)],
    ...(blended != null ? [["blended", "Blended", fmt.pct(blended)]] : []),
    ...(jev != null ? [["jev", "Stima Jev", fmt.pct(jev)]] : []),
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
    h("div", { class: "ptrack-bar", role: "img", "aria-label": `Prezzo ${fmt.pct(market)}${blended != null ? `, blended ${fmt.pct(blended)}` : ""}${jev != null ? `, Jev ${fmt.pct(jev)}` : ""}` },
      h("div", { class: "ptrack-line" }),
      h("div", { class: "ptrack-ticks" }, [0, 25, 50, 75, 100].map((t) => h("span", { style: { left: `${t}%` } }))),
      edge,
      mark("jev", jev, "Stima Jev"),
      mark("market", market, "Prezzo mercato"),
      mark("blended", blended, "Blended"),
    ),
    h("div", { class: "ptrack-axis", "aria-hidden": "true" }, ["0%", "25%", "50%", "75%", "100%"].map((t) => h("span", {}, t))),
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
    h("span", {}, h("span", { class: "key line market" }), "Prezzo mercato"),
    h("span", {}, h("span", { class: "key line blended" }), "Blended"),
    h("span", {}, h("span", { class: "key jev" }), "Stima Jev"),
  );
  const holder = h("div", {});
  const table = historyTable(points);
  table.hidden = true;
  const toggle = h("button", { class: "link-btn", type: "button", "aria-expanded": "false" }, "Mostra tabella");
  toggle.addEventListener("click", () => {
    table.hidden = !table.hidden;
    toggle.textContent = table.hidden ? "Mostra tabella" : "Nascondi tabella";
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

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": `Storico di ${points.length} previsioni: prezzo di mercato e probabilità blended` });
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
        ["market", "Prezzo mercato", fmt.cents(best.market)],
        ["blended", "Blended", fmt.pct(best.blended)],
        ["jev", "Stima Jev", fmt.pct(best.jev)],
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
        h("th", {}, "Data"), h("th", { class: "num" }, "Prezzo"), h("th", { class: "num" }, "Jev"),
        h("th", { class: "num" }, "Blended"), h("th", {}, "Segnale"),
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
            h("span", { class: "bar-ref-label", style: { left: `${(ref / max) * 100}%` } }, "0,25 = sempre 50%"),
          ] : h("div", { class: "bar-ref", style: { left: `${(ref / max) * 100}%` } }),
        ),
      );
    }),
  );
}
