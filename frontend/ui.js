// Small DOM, formatting and API helpers shared by every view.

// ---------- DOM ----------
/** Creates an element. Strings become text nodes (never HTML), so untrusted text is always safe. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) el.addEventListener(ev, fn);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "href") el.setAttribute("href", safeHref(value));
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

/** Only in-app hashes and http(s) links are allowed (feed/market data is untrusted). */
export function safeHref(url) {
  if (typeof url !== "string") return "#";
  if (url.startsWith("#")) return url;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "#";
  } catch {
    return "#";
  }
}

export function externalLink(url, ...children) {
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, ...children);
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ---------- Icons (one stroke family: 24px grid, 2px round strokes) ----------
const ICONS = {
  refresh: ["M21 12a9 9 0 1 1-3-6.7L21 8", "M21 3v5h-5"],
  sync: ["M7 4v16", "M3 8l4-4 4 4", "M17 20V4", "M21 16l-4 4-4-4"],
  sun: ["M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M12 2v2", "M12 20v2", "M4.9 4.9l1.4 1.4", "M17.7 17.7l1.4 1.4", "M2 12h2", "M20 12h2", "M4.9 19.1l1.4-1.4", "M17.7 6.3l1.4-1.4"],
  moon: ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"],
  external: ["M14 4h6v6", "M10 14L20 4", "M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"],
  back: ["M19 12H5", "M12 19l-7-7 7-7"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z", "M21 21l-4.3-4.3"],
  up: ["M3 17l6-6 4 4 8-8", "M15 7h6v6"],
  down: ["M3 7l6 6 4-4 8 8", "M15 17h6v-6"],
  pause: ["M9 5v14", "M15 5v14"],
  check: ["M20 6L9 17l-5-5"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  alert: ["M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
  arrowUp: ["M12 19V5", "M5 12l7-7 7 7"],
  arrowDown: ["M12 5v14", "M19 12l-7 7-7-7"],
  minus: ["M5 12h14"],
  news: ["M4 4h13a1 1 0 0 1 1 1v14a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2z", "M18 9h2a1 1 0 0 1 1 1v9a2 2 0 0 1-2 2", "M8 8h6", "M8 12h6", "M8 16h4"],
};

/** Decorative SVG icon (aria-hidden: always paired with visible text or an aria-label). */
export function icon(name, cls = "icon-svg") {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", cls);
  for (const d of ICONS[name] || []) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

/** Replaces every `<span data-icon="name">` placeholder in static markup. */
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => el.replaceWith(icon(el.dataset.icon)));
}

// ---------- Formatting ----------
const pctFmt = new Intl.NumberFormat("it-IT", { style: "percent", maximumFractionDigits: 1 });
const centFmt = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
const usdFmt = new Intl.NumberFormat("it-IT", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const intFmt = new Intl.NumberFormat("it-IT");
const numFmt = new Intl.NumberFormat("it-IT", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const rtf = new Intl.RelativeTimeFormat("it", { numeric: "auto" });

export const fmt = {
  pct: (p) => (p == null ? "–" : pctFmt.format(p)),
  // Polymarket quotes a YES share in cents: price 0.35 = 35¢ = 35% implied probability
  cents: (p) => (p == null ? "–" : `${centFmt.format(p * 100)}¢`),
  pts: (edge) => (edge == null ? "–" : `${edge > 0 ? "+" : edge < 0 ? "−" : ""}${Math.abs(edge * 100).toFixed(1)} pt`),
  usd: (v) => (v == null ? "–" : usdFmt.format(v)),
  int: (v) => (v == null ? "–" : intFmt.format(v)),
  num3: (v) => (v == null ? "–" : numFmt.format(v)),
  date: (d) => (d ? new Date(d).toLocaleDateString("it-IT", { day: "numeric", month: "short", year: "numeric" }) : "–"),
  dateTime: (d) => (d ? new Date(d).toLocaleString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–"),
  ago(d) {
    if (!d) return "–";
    const diff = (new Date(d).getTime() - Date.now()) / 1000;
    const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    for (const [unit, secs] of units) if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
    return "adesso";
  },
};

// ---------- API ----------
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", params } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  let res;
  try {
    res = await fetch(url, { method, headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError(0, "Impossibile contattare il server");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && body.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `Errore ${res.status}`;
    throw new ApiError(res.status, message);
  }
  return body;
}

// ---------- Feedback ----------
export function toast(message, { error = false, timeout = 4000 } = {}) {
  const el = h("div", { class: `toast${error ? " error" : ""}` }, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), timeout);
}

const tooltipEl = () => document.getElementById("tooltip");

/** Shows the shared tooltip near (x, y) viewport coordinates. `content` is a Node or text. */
export function showTooltip(content, x, y) {
  const tip = tooltipEl();
  clear(tip).append(content instanceof Node ? content : document.createTextNode(content));
  tip.hidden = false;
  const { width, height } = tip.getBoundingClientRect();
  const left = Math.min(Math.max(8, x + 14), window.innerWidth - width - 8);
  const top = y - height - 12 < 8 ? y + 16 : y - height - 12;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

export function hideTooltip() {
  tooltipEl().hidden = true;
}

/** Tooltip on hover and keyboard focus. `build` returns the tooltip content lazily. */
export function withTooltip(el, build) {
  const show = (e) => {
    const rect = el.getBoundingClientRect();
    const x = e && e.clientX != null ? e.clientX : rect.left + rect.width / 2;
    const y = e && e.clientY != null ? e.clientY : rect.top;
    showTooltip(build(), x, y);
  };
  el.addEventListener("mouseenter", show);
  el.addEventListener("mousemove", show);
  el.addEventListener("mouseleave", hideTooltip);
  el.addEventListener("focus", () => show());
  el.addEventListener("blur", hideTooltip);
  return el;
}

export function ttRows(title, rows) {
  return h("div", {},
    title ? h("div", { class: "tt-title" }, title) : null,
    rows.map(([key, label, value]) => h("div", { class: "tt-row" }, key ? h("span", { class: `key ${key}` }) : null, `${label}: `, h("b", {}, value))),
  );
}

// ---------- Shared components ----------
const SIGNALS = {
  BUY_YES: { cls: "badge-good", icon: "up", label: "Compra SÌ" },
  BUY_NO: { cls: "badge-critical", icon: "down", label: "Compra NO" },
  HOLD: { cls: "", icon: "pause", label: "Attendi" },
};

export function signalBadge(signal) {
  const s = SIGNALS[signal] || SIGNALS.HOLD;
  return h("span", { class: `badge ${s.cls}` }, icon(s.icon), s.label);
}

const IMPACTS = {
  raises_yes: { cls: "badge-good", icon: "arrowUp", label: "Favorisce SÌ" },
  lowers_yes: { cls: "badge-critical", icon: "arrowDown", label: "Favorisce NO" },
  neutral: { cls: "", icon: "minus", label: "Neutra" },
};

export function impactBadge(impact) {
  if (!impact) return h("span", { class: "badge badge-outline" }, "Non ancora valutata");
  const s = IMPACTS[impact] || IMPACTS.neutral;
  return h("span", { class: `badge ${s.cls}` }, icon(s.icon), s.label);
}

export function marketStateBadge(market) {
  if (market.resolved_yes === true) return h("span", { class: "badge badge-good" }, icon("check"), "Risolto SÌ");
  if (market.resolved_yes === false) return h("span", { class: "badge badge-critical" }, icon("x"), "Risolto NO");
  if (market.closed) return h("span", { class: "badge" }, "Chiuso");
  return h("span", { class: "badge badge-outline" }, "Aperto");
}

export function meter(value, label) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return h("div", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": "1", "aria-valuenow": v.toFixed(2), "aria-label": label || "valore" },
    h("div", { class: "meter-track" }, h("div", { class: "meter-fill", style: { width: `${v * 100}%` } })),
    h("span", { class: "meter-value" }, fmt.pct(v)),
  );
}

export function statTile(label, value, sub) {
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value),
    sub ? h("div", { class: "tile-sub" }, sub) : null,
  );
}

export function emptyState(title, body, ...extra) {
  return h("div", { class: "card empty" }, h("h3", {}, title), body ? h("p", {}, body) : null, ...extra);
}

export function skeleton(n = 3) {
  return h("div", { class: "stack" }, Array.from({ length: n }, () => h("div", { class: "skeleton" })));
}
