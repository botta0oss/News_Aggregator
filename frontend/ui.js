import { t, lang, locale } from "./i18n.js";
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
  play: ["M7 4.5v15l12-7.5z"],
  bell: ["M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9", "M13.7 21a2 2 0 0 1-3.4 0"],
  sidebar: ["M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z", "M9 4v16"],
  chevron: ["M6 9l6 6 6-6"],
  gauge: ["M3.5 17a9 9 0 1 1 17 0", "M12 14l4-4", "M12 14h.01"],
  more: ["M4 12h2", "M11 12h2", "M18 12h2"],
  wallet: ["M3 7a2 2 0 0 1 2-2h13v4", "M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2z", "M16 14h.01"],
  target: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M12 12h.01"],
  history: ["M3 12a9 9 0 1 0 3-6.7L3 8", "M3 3v5h5", "M12 7v5l3 2"],
  list: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  toggle: ["M8 6h8a6 6 0 0 1 0 12H8A6 6 0 0 1 8 6z", "M16 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  check: ["M20 6L9 17l-5-5"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  alert: ["M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
  arrowUp: ["M12 19V5", "M5 12l7-7 7 7"],
  arrowDown: ["M12 5v14", "M19 12l-7 7-7-7"],
  minus: ["M5 12h14"],
  plus: ["M12 5v14", "M5 12h14"],
  sort: ["M7 15l5 5 5-5", "M7 9l5-5 5 5"],
  settings: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"],
  help: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z", "M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3", "M12 17h.01"],
  user: ["M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2", "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  lock: ["M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z", "M7 11V7a5 5 0 0 1 10 0v4"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
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
const pctFmt = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 });
const centFmt = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
const usdFmt = new Intl.NumberFormat(locale, { style: "currency", currency: "USD", currencyDisplay: "narrowSymbol", notation: "compact", maximumFractionDigits: 1 });
const intFmt = new Intl.NumberFormat(locale);
const moneyFmt = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sharesFmt = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
const numFmt = new Intl.NumberFormat(locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const dec1Fmt = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
// Dollar amounts: "41,02 $" in Italian, "$41.02" in English
const dollars = (text) => (lang === "it" ? `${text} $` : `$${text}`);
const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });

export const fmt = {
  pct: (p) => (p == null ? "–" : pctFmt.format(p)),
  // Polymarket quotes a YES share in cents: price 0.35 = 35¢ = 35% implied probability
  cents: (p) => (p == null ? "–" : `${centFmt.format(p * 100)}¢`),
  pts: (edge) => (edge == null ? "–" : `${edge > 0 ? "+" : edge < 0 ? "−" : ""}${dec1Fmt.format(Math.abs(edge * 100))} ${t("pt")}`),
  /** Plain decimal in the interface language: 0.25 → "0,25" in Italian, "0.25" in English. */
  dec: (v, digits) => (v == null ? "–" : new Intl.NumberFormat(locale, digits == null
    ? { maximumFractionDigits: 4 } : { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v)),
  usd: (v) => (v == null ? "–" : usdFmt.format(v)),
  /** "41,02 $" in Italian, "$41.02" in English, for an already formatted number */
  dollars: (text) => dollars(text),
  money: (v) => (v == null ? "–" : dollars(moneyFmt.format(v))),
  signedMoney: (v) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${dollars(moneyFmt.format(Math.abs(v)))}`),
  shares: (v) => (v == null ? "–" : sharesFmt.format(v)),
  int: (v) => (v == null ? "–" : intFmt.format(v)),
  num3: (v) => (v == null ? "–" : numFmt.format(v)),
  date: (d) => (d ? new Date(d).toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" }) : "–"),
  dateTime: (d) => (d ? new Date(d).toLocaleString(locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–"),
  /** "1 notizia" / "3 notizie" */
  count: (n, one, many) => `${intFmt.format(n)} ${n === 1 ? one : many}`,
  ago(d) {
    if (!d) return "–";
    const diff = (new Date(d).getTime() - Date.now()) / 1000;
    const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    for (const [unit, secs] of units) if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
    return t("adesso");
  },
};

// ---------- API ----------
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let csrfToken = null;

/** Set after login / session check; sent on every state-changing request. */
export function setCsrfToken(token) {
  csrfToken = token;
}

/**
 * Calls the API with the session cookie. A 401 means the session ended: the app listens for
 * the "auth:required" event and shows the login screen.
 */
export async function api(path, { method = "GET", params, body, handle401 = true } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  // X-Lang: texts the server writes (plans, reasons, errors) come back in the interface language
  const headers = { Accept: "application/json", "X-Lang": lang };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res;
  try {
    res = await fetch(url, {
      method, headers, credentials: "same-origin",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, t("Impossibile contattare il server. Controlla la connessione e riprova."));
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : t("Errore {0}", res.status);
    if (res.status === 401 && handle401) window.dispatchEvent(new CustomEvent("auth:required"));
    throw new ApiError(res.status, message);
  }
  return data;
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
  BUY_YES: { cls: "badge-good", icon: "up", label: t("Compra SÌ") },
  BUY_NO: { cls: "badge-critical", icon: "down", label: t("Compra NO") },
  HOLD: { cls: "", icon: "pause", label: t("Attendi") },
};

export function signalBadge(signal) {
  const s = SIGNALS[signal] || SIGNALS.HOLD;
  return h("span", { class: `badge ${s.cls}` }, icon(s.icon), s.label);
}

const IMPACTS = {
  raises_yes: { cls: "badge-good", icon: "arrowUp", label: t("Favorisce SÌ") },
  lowers_yes: { cls: "badge-critical", icon: "arrowDown", label: t("Favorisce NO") },
  neutral: { cls: "", icon: "minus", label: t("Neutra") },
};

export function impactBadge(impact) {
  if (!impact) return h("span", { class: "badge badge-outline" }, t("Non ancora valutata"));
  const s = IMPACTS[impact] || IMPACTS.neutral;
  return h("span", { class: `badge ${s.cls}` }, icon(s.icon), s.label);
}

export function marketStateBadge(market) {
  if (market.resolved_yes === true) return h("span", { class: "badge badge-good" }, icon("check"), t("Risolto SÌ"));
  if (market.resolved_yes === false) return h("span", { class: "badge badge-critical" }, icon("x"), t("Risolto NO"));
  if (market.closed) return h("span", { class: "badge" }, t("Chiuso"));
  return h("span", { class: "badge badge-outline" }, t("Aperto"));
}

export function meter(value, label) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return h("div", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": "1", "aria-valuenow": v.toFixed(2), "aria-label": label || t("valore") },
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

// ---------- Labels ----------
export const CATEGORY_LABELS = {
  Politics: t("Politica"), Economy: t("Economia"), Crypto: t("Crypto"), Technology: t("Tecnologia"),
  "Foreign Affairs": t("Esteri"), Science: t("Scienza e salute"), Sports: t("Sport"), Culture: t("Cultura"),
};
export const REGION_LABELS = {
  "North America": t("Nord America"), Europe: t("Europa"), "Middle East & Africa": t("Medio Oriente e Africa"),
  "Asia-Pacific": "Asia-Pacifico", "Latin America": t("America Latina"), Global: t("Globale"),
};

// ---------- Glossary ----------
export const GLOSSARY = {
  price: t("Prezzo di una quota SÌ su Polymarket, in centesimi. Una quota paga 1 $ se l'evento accade: 35¢ equivale a una probabilità implicita del 35%."),
  jev: t("Probabilità che il mercato si risolva SÌ secondo Jev, calcolata leggendo le regole del mercato e le notizie collegate. Jev non vede il prezzo."),
  evidence: t("Quanto le notizie collegate dicono qualcosa di concreto sull'esito, da 0% (nulla di rilevante) a 100% (informazione decisiva). Stabilisce quanto pesa la stima di Jev."),
  weight: t("Peso della stima di Jev nella probabilità finale: peso massimo × forza delle evidenze. Il resto del peso va al prezzo di mercato."),
  blended: t("Probabilità finale usata per il segnale: una media pesata tra la stima di Jev e il prezzo di mercato."),
  edge: t("Differenza tra la probabilità blended e il prezzo, in punti percentuali. Positivo: il SÌ sembra sottovalutato. Negativo: il NO sembra sottovalutato."),
  kelly: t("Quota del capitale secondo il criterio di Kelly sulla probabilità blended e sul prezzo, ridotta per prudenza (Kelly frazionario). È indicativa: non tiene conto di book, commissioni, incertezza e limiti del preset. La puntata effettiva è quella della scheda «Conviene?»."),
  brier: t("Errore quadratico medio tra probabilità prevista ed esito reale (1 se SÌ, 0 se NO). Più basso è meglio; dire sempre 50% vale 0,25."),
  relevance: t("Quanto la notizia può cambiare la probabilità di un evento futuro verificabile su cui si scommette (elezioni, tassi, conflitti, sentenze, prezzi, partite)."),
};

/** Small focusable (i) button showing a definition on hover and focus. */
export function infoTip(text, label = t("Che cos'è?")) {
  const btn = h("button", { class: "info-tip", type: "button", "aria-label": `${label} ${text}` }, "i");
  withTooltip(btn, () => text);
  btn.addEventListener("click", (e) => e.preventDefault());
  return btn;
}

/** Turns text with \u0002…\u0003 match markers (from the search API) into safe DOM with <mark>. */
export function highlight(text) {
  const out = [];
  const parts = String(text || "").split(/(\u0002[^\u0003]*\u0003)/);
  for (const part of parts) {
    if (part.startsWith("\u0002")) out.push(h("mark", {}, part.slice(1, -1)));
    else if (part) out.push(part.replace(/[\u0002\u0003]/g, ""));
  }
  return out;
}

export function selectField(id, label, options, value, onChange) {
  const select = h("select", { id, class: "select" },
    options.map(([v, text]) => h("option", { value: v, selected: String(v) === String(value ?? "") }, text)));
  select.addEventListener("change", () => onChange(select.value));
  return h("label", { class: "field", for: id }, label, select);
}
