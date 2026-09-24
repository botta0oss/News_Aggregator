import {
  h, clear, api, setCsrfToken, fmt, toast, debounce, icon, hydrateIcons, externalLink,
  signalBadge, impactBadge, marketStateBadge, meter, statTile, emptyState, skeleton, infoTip, GLOSSARY,
} from "./ui.js";
import { probTrack, probLegend, historyChart, brierBars } from "./charts.js";
import { explainCard, explainSentence } from "./explain.js";
import { viewNews } from "./views/news.js";
import { viewSettings } from "./views/settings.js";
import { viewMethod } from "./views/method.js";
import { viewPortfolio } from "./views/portfolio.js";
import { economicsCard, verdictBadge } from "./economics.js";

const view = document.getElementById("view");
let status = null;
let me = null; // { username, role } once signed in
let renderToken = 0;
let afterLogin = null; // hash to return to after signing in

const isAdmin = () => me?.role === "admin";

// ---------- Session ----------
function setSignedIn(info) {
  me = info.user;
  setCsrfToken(info.csrf_token);
  for (const id of ["tabs", "actions", "footer"]) document.getElementById(id).hidden = false;
  document.getElementById("user-name").textContent = me.username;
  document.getElementById("user-link").setAttribute("title", `${me.username} (${me.role === "admin" ? "amministratore" : "sola lettura"}): account e password`);
  // Jobs and paid API calls are admin-only: viewers don't get the buttons
  document.querySelectorAll(".admin-only").forEach((el) => { el.hidden = !isAdmin(); });
}

function setSignedOut() {
  me = null;
  status = null;
  setCsrfToken(null);
  for (const id of ["tabs", "actions", "footer", "banner"]) document.getElementById(id).hidden = true;
}

async function checkSession() {
  try {
    setSignedIn(await api("/auth/me", { handle401: false }));
    return true;
  } catch (e) {
    if (e.status !== 401) toast(e.message, { error: true });
    return false;
  }
}

function showLogin(message) {
  if (!afterLogin && !/^#\/(account)?$/.test(window.location.hash)) afterLogin = window.location.hash || null;
  setSignedOut();
  ++renderToken;
  clear(view).append(loginView(message));
  view.querySelector("#login-username")?.focus();
}

function loginView(message) {
  const error = h("p", { class: "form-error", role: "alert", id: "login-error" }, message || "");
  error.hidden = !message;
  const username = h("input", { id: "login-username", class: "input", name: "username", autocomplete: "username", required: true, autocapitalize: "none", spellcheck: "false", maxlength: "64" });
  const password = h("input", { id: "login-password", class: "input", name: "password", type: "password", autocomplete: "current-password", required: true, maxlength: "256" });
  const reveal = h("button", { class: "input-addon", type: "button", "aria-label": "Mostra password", "aria-pressed": "false" }, icon("eye"));
  reveal.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(show));
    reveal.setAttribute("aria-label", show ? "Nascondi password" : "Mostra password");
  });
  const submit = h("button", { class: "btn btn-primary btn-block", type: "submit" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Accedi");
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "form-field" }, h("label", { for: "login-username" }, "Username"), username),
    h("div", { class: "form-field" }, h("label", { for: "login-password" }, "Password"), h("div", { class: "input-group" }, password, reveal)),
    error, submit,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!username.value.trim() || !password.value) {
      error.textContent = "Inserisci username e password.";
      error.hidden = false;
      (username.value.trim() ? password : username).focus();
      return;
    }
    setBusy(submit, true);
    error.hidden = true;
    try {
      const info = await api("/auth/login", { method: "POST", body: { username: username.value, password: password.value }, handle401: false });
      setSignedIn(info);
      password.value = "";
      const target = afterLogin && afterLogin !== window.location.hash ? afterLogin : null;
      afterLogin = null;
      await loadStatus();
      if (target) window.location.hash = target;
      else route();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      password.select();
      password.focus();
      setBusy(submit, false);
    }
  });
  return h("div", { class: "login-wrap" },
    h("section", { class: "card login-card", "aria-labelledby": "login-title" },
      h("div", { class: "login-head" }, h("span", { class: "login-icon" }, icon("lock")), h("div", {},
        h("h1", { id: "login-title" }, "Accedi"),
        h("p", { class: "secondary small" }, "Dashboard privata di News × Markets."),
      )),
      form,
      h("p", { class: "muted small" }, "Non hai un account? Chiedi a chi gestisce il server di crearlo con ",
        h("code", {}, "python -m backend.auth.cli create-user"), "."),
    ),
  );
}

async function logout() {
  try {
    await api("/auth/logout", { method: "POST", handle401: false });
  } catch { /* the session is gone either way */ }
  afterLogin = null;
  showLogin("Sei uscito. A presto.");
  window.history.replaceState(null, "", "#/opportunita");
}

// ---------- App shell ----------
async function loadStatus() {
  if (!me) return null;
  try {
    status = await api("/status");
  } catch {
    status = null;
  }
  renderBanner();
  renderFooter();
  return status;
}

function renderBanner() {
  const banner = document.getElementById("banner");
  clear(banner);
  if (!status) {
    banner.append(icon("alert"), h("span", {}, "Il server non risponde. Controlla che l'API sia avviata e che il database sia raggiungibile."));
    banner.hidden = false;
  } else if (!status.jev_enabled) {
    banner.append(icon("alert"), h("span", {},
      "Previsioni disattivate: manca la chiave TypeSafe. Aggiungi ", h("code", {}, "TYPESAFE_API_KEY"),
      " al file .env e riavvia. Nel frattempo le notizie vengono classificate con un'euristica locale.",
    ));
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

function renderFooter() {
  const el = document.getElementById("footer-status");
  if (!status) return (el.textContent = "");
  const parts = [
    `${fmt.int(status.articles)} notizie`,
    status.last_article_at ? `ultima ${fmt.ago(status.last_article_at)}` : null,
    `${fmt.int(status.open_markets)} mercati aperti`,
    `Jev ${status.jev_enabled ? "attivo" : "non configurato"}`,
    status.prediction_auto ? "previsioni automatiche attive" : "previsioni manuali",
  ];
  el.textContent = parts.filter(Boolean).join(" · ");
}

function setBusy(btn, busy) {
  btn.disabled = busy;
  btn.classList.toggle("loading", busy);
  btn.setAttribute("aria-busy", String(busy));
}

/** Starts a background job, then refreshes the page a few times while it runs. */
async function runJob(btn, path, startedMessage) {
  setBusy(btn, true);
  try {
    await api(path, { method: "POST" });
    toast(startedMessage);
    // The job runs in the background: refresh after 4 s, 10 s and 20 s
    for (const wait of [4000, 6000, 10000]) {
      await new Promise((r) => setTimeout(r, wait));
      await loadStatus();
      route({ quiet: true });
    }
  } catch (e) {
    toast(e.message, { error: true });
  } finally {
    setBusy(btn, false);
  }
}

function setupShell() {
  hydrateIcons();
  document.getElementById("btn-logout").addEventListener("click", logout);
  window.addEventListener("auth:required", () => {
    if (me) showLogin("La sessione è scaduta. Accedi di nuovo.");
  });
  const ingest = document.getElementById("btn-ingest");
  const sync = document.getElementById("btn-sync");
  ingest.addEventListener("click", () => runJob(ingest, "/ingest", "Aggiornamento notizie avviato. La pagina si aggiorna da sola."));
  sync.addEventListener("click", () => runJob(sync, "/markets/sync", "Aggiornamento mercati avviato. La pagina si aggiorna da sola."));

  const themeBtn = document.getElementById("btn-theme");
  const paintThemeButton = () => {
    const light = document.documentElement.dataset.theme === "light";
    themeBtn.replaceChildren(icon(light ? "moon" : "sun"));
    themeBtn.setAttribute("aria-label", light ? "Passa al tema scuro" : "Passa al tema chiaro");
    document.querySelector('meta[name="theme-color"]').setAttribute("content", light ? "#f1f5f9" : "#020617");
  };
  themeBtn.addEventListener("click", () => {
    const light = document.documentElement.dataset.theme !== "light";
    if (light) document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try { localStorage.setItem("theme", light ? "light" : "dark"); } catch {}
    paintThemeButton();
  });
  paintThemeButton();
}

// ---------- Router ----------
const routes = [
  [/^#\/opportunita$/, "opportunita", viewOpportunities],
  [/^#\/mercati$/, "mercati", viewMarkets],
  [/^#\/mercati\/(.+)$/, "mercati", viewMarketDetail],
  [/^#\/notizie(?:\?(.*))?$/, "notizie", (qs) => viewNews(ctx, Object.fromEntries(new URLSearchParams(qs || "")))],
  [/^#\/calibrazione$/, "calibrazione", viewCalibration],
  [/^#\/account$/, "account", viewAccount],
  [/^#\/impostazioni$/, "impostazioni", () => viewSettings(ctx)],
  [/^#\/metodo$/, "metodo", () => viewMethod(ctx)],
  [/^#\/portafoglio$/, "portafoglio", () => viewPortfolio(ctx)],
];

async function route({ quiet = false } = {}) {
  if (!me) return showLogin();
  const hash = window.location.hash || "#/opportunita";
  const match = routes.find(([re]) => re.test(hash));
  if (!match) {
    window.location.hash = "#/opportunita";
    return;
  }
  const [re, tab, render] = match;
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const settingsLink = document.getElementById("settings-link");
  if (tab === "impostazioni") settingsLink.setAttribute("aria-current", "page");
  else settingsLink.removeAttribute("aria-current");
  const token = ++renderToken;
  if (!quiet) {
    clear(view).append(skeleton(3));
    window.scrollTo(0, 0);
  }
  const params = hash.match(re).slice(1).map((p) => (p == null ? p : decodeURIComponent(p)));
  try {
    const content = await render(...params);
    if (token !== renderToken) return; // a newer navigation won
    clear(view).append(content);
    if (!quiet) view.focus({ preventScroll: true });
  } catch (e) {
    if (token !== renderToken || e.status === 401) return;
    clear(view).append(emptyState("Impossibile caricare la pagina", e.message,
      h("button", { class: "btn btn-ghost", type: "button", on: { click: () => route() } }, icon("refresh"), "Riprova")));
  }
}

function pageHead(title, subtitle, ...right) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, title), subtitle ? h("p", {}, subtitle) : null),
    right.length ? h("div", { class: "actions" }, right) : null,
  );
}

function rangeField(id, label, { min, max, step, value, format }, onInput) {
  const input = h("input", { id, type: "range", min, max, step, value });
  const out = h("output", { for: id }, format(Number(value)));
  input.addEventListener("input", () => {
    out.textContent = format(Number(input.value));
    onInput(Number(input.value));
  });
  return h("label", { class: "field", for: id }, label, input, out);
}

function checkField(id, label, checked, onChange) {
  const input = h("input", { id, type: "checkbox", checked });
  input.addEventListener("change", () => onChange(input.checked));
  return h("label", { class: "field", for: id }, input, label);
}

const marketHref = (id) => `#/mercati/${encodeURIComponent(id)}`;

// ---------- Opportunità ----------
const oppFilters = { minEdge: null, minEvidence: 0, includeHold: false };

async function viewOpportunities() {
  await loadStatus();
  if (oppFilters.minEdge == null) oppFilters.minEdge = status ? Math.round(status.min_edge * 100) : 5;

  const list = h("div", { class: "opps" }, skeleton(2));
  const kpis = h("div", { class: "kpis" });
  const reload = async () => {
    try {
      const opps = await api("/predictions/opportunities", {
        params: { min_edge: oppFilters.minEdge / 100, min_evidence: oppFilters.minEvidence / 100, include_hold: oppFilters.includeHold, limit: 100 },
      });
      const active = opps.filter((o) => o.prediction.signal !== "HOLD").length;
      kpis.replaceChildren(
        statTile("Mercati aperti", fmt.int(status?.open_markets ?? 0), "binari Sì/No su Polymarket"),
        statTile("Con notizie collegate", fmt.int(status?.linked_markets ?? 0), "notizie recenti simili alla domanda"),
        statTile("Previsioni Jev", fmt.int(status?.predictions ?? 0), status?.last_prediction_at ? `ultima ${fmt.ago(status.last_prediction_at)}` : "nessuna ancora"),
        statTile("Segnali attivi", fmt.int(active), `edge ≥ ${oppFilters.minEdge} pt`),
      );
      list.replaceChildren(...(opps.length ? opps.map(opportunityCard) : [opportunitiesEmpty()]));
    } catch (e) {
      list.replaceChildren(emptyState("Impossibile caricare le opportunità", e.message));
    }
  };
  const reloadSoon = debounce(reload, 250);

  const filters = h("div", { class: "filters", role: "group", "aria-label": "Filtri" },
    rangeField("f-edge", "Edge minimo", { min: 0, max: 30, step: 1, value: oppFilters.minEdge, format: (v) => `${v} pt` }, (v) => { oppFilters.minEdge = v; reloadSoon(); }),
    rangeField("f-evidence", "Evidenze minime", { min: 0, max: 100, step: 5, value: oppFilters.minEvidence, format: (v) => `${v}%` }, (v) => { oppFilters.minEvidence = v; reloadSoon(); }),
    checkField("f-hold", "Mostra anche «Attendi»", oppFilters.includeHold, (v) => { oppFilters.includeHold = v; reload(); }),
  );

  await reload();
  return h("div", {},
    pageHead("Opportunità", "Mercati in cui la stima di Jev, pesata per la forza delle notizie, si discosta dal prezzo. Ordinati per edge.",
      h("a", { class: "btn btn-ghost", href: "#/metodo" }, icon("help"), "Come funziona")),
    kpis, filters, list,
  );
}

function opportunitiesEmpty() {
  if (!status) return emptyState("Nessun dato", "Il server non risponde.");
  if (status.open_markets === 0) {
    return emptyState("Nessun mercato ancora", isAdmin() ? "Scarica i mercati Sì/No più scambiati da Polymarket per iniziare." : "Un amministratore deve scaricare i mercati da Polymarket.",
      isAdmin() ? h("button", { class: "btn btn-primary", type: "button", on: { click: () => document.getElementById("btn-sync").click() } }, icon("sync"), "Aggiorna mercati") : null);
  }
  if (status.linked_markets === 0) {
    return emptyState("Nessuna notizia collegata ai mercati", "Le notizie recenti non somigliano ancora a nessuna domanda di mercato. Aggiorna le notizie o abbassa MARKET_MATCH_THRESHOLD nel file .env.",
      isAdmin() ? h("button", { class: "btn btn-primary", type: "button", on: { click: () => document.getElementById("btn-ingest").click() } }, icon("refresh"), "Aggiorna notizie") : null);
  }
  if (!status.jev_enabled) {
    return emptyState("Previsioni non disponibili", "Configura TYPESAFE_API_KEY per ottenere le stime di Jev sui mercati con notizie collegate.");
  }
  if (status.predictions === 0) {
    return emptyState("Nessuna previsione ancora", `${fmt.int(status.linked_markets)} mercati hanno notizie collegate. Apri un mercato e chiedi una previsione a Jev.`,
      h("a", { class: "btn btn-primary", href: "#/mercati" }, "Vedi i mercati con notizie"));
  }
  return emptyState("Nessuna opportunità con questi filtri", "Abbassa l'edge o le evidenze minime, oppure mostra anche i mercati in attesa.");
}

function opportunityCard({ market, prediction: p }) {
  const edgeCls = p.edge > 0 ? "pos" : p.edge < 0 ? "neg" : "";
  return h("article", { class: "card opp" },
    h("div", {},
      h("a", { class: "opp-title", href: marketHref(market.id) }, market.question),
      h("div", { class: "opp-meta" },
        h("span", {}, `Scade ${fmt.date(market.end_date)}`),
        h("span", {}, `Volume ${fmt.usd(market.volume)}`),
        h("span", {}, `${p.article_count} notizie analizzate`),
        h("span", { title: fmt.dateTime(p.created_at) }, `Previsione ${fmt.ago(p.created_at)}`),
      ),
      probTrack({ market: p.market_probability, blended: p.blended_probability, jev: p.model_probability }),
      probLegend({ market: p.market_probability, blended: p.blended_probability, jev: p.model_probability }),
      h("p", { class: "opp-explain" }, explainSentence(p, status), " ", h("a", { href: marketHref(market.id) }, "Vedi il calcolo")),
    ),
    h("div", { class: "opp-side" },
      h("div", { class: "opp-side-top" },
        signalBadge(p.signal),
        market.url ? externalLink(market.url, "Polymarket ", icon("external")) : null,
      ),
      h("div", { class: "opp-figures" },
        h("div", {}, h("div", { class: "fig-label" }, "Edge", infoTip(GLOSSARY.edge)), h("div", { class: `fig-value ${edgeCls}` }, fmt.pts(p.edge))),
        p.economics
          ? h("div", {}, h("div", { class: "fig-label" }, "Conviene?", infoTip("Valutazione economica al momento della previsione: prezzo reale dal book, costi, incertezza, tempo e limiti del preset.")),
            verdictBadge(p.economics.verdict),
            p.economics.verdict !== "NO" ? h("div", { class: "fig-label", style: { marginTop: "4px" } }, `Puntata ${fmt.money(p.economics.outlay)}`) : null)
          : h("div", {}, h("div", { class: "fig-label" }, "Puntata suggerita", infoTip(GLOSSARY.kelly)), h("div", { class: "fig-value" }, p.kelly_fraction > 0 ? `${fmt.pct(p.kelly_fraction)}` : "–"),
            p.kelly_fraction > 0 ? h("div", { class: "fig-label" }, "del bankroll") : null),
      ),
      h("div", {}, h("div", { class: "fig-label" }, "Forza delle evidenze", infoTip(GLOSSARY.evidence)), meter(p.evidence_strength, "Forza delle evidenze")),
    ),
  );
}

// ---------- Mercati ----------
const marketFilters = { q: "", onlyLinked: null, includeClosed: false };
const PAGE = 50;

async function viewMarkets() {
  await loadStatus();
  if (marketFilters.onlyLinked == null) marketFilters.onlyLinked = (status?.linked_markets ?? 0) > 0;

  const tbody = h("tbody", {});
  const summary = h("p", { class: "muted small", role: "status" });
  const more = h("div", { class: "more" });
  let offset = 0;

  const load = async (reset) => {
    if (reset) offset = 0;
    const data = await api("/markets", {
      params: { q: marketFilters.q, only_linked: marketFilters.onlyLinked, include_closed: marketFilters.includeClosed, limit: PAGE, offset },
    });
    if (reset) clear(tbody);
    tbody.append(...data.markets.map(marketRow));
    offset += data.markets.length;
    summary.textContent = data.total ? `${fmt.int(offset)} di ${fmt.int(data.total)} mercati, ordinati per volume` : "";
    more.replaceChildren(offset < data.total
      ? h("button", { class: "btn btn-ghost", type: "button", on: { click: () => load(false) } }, "Carica altri")
      : "");
    tableWrap.hidden = data.total === 0;
    empty.hidden = data.total !== 0;
  };
  const reloadSoon = debounce(() => load(true).catch((e) => toast(e.message, { error: true })), 300);

  const search = h("input", { id: "m-search", class: "search", type: "search", placeholder: "Cerca un mercato (es. Fed, elezioni, Bitcoin)", value: marketFilters.q, "aria-label": "Cerca un mercato" });
  search.addEventListener("input", () => { marketFilters.q = search.value.trim(); reloadSoon(); });

  const tableWrap = h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, "Mercato"), h("th", { class: "num" }, "Prezzo SÌ"), h("th", { class: "num" }, "Volume"),
        h("th", {}, "Scadenza"), h("th", { class: "num" }, "Notizie"), h("th", {}, "Ultimo segnale"),
      )),
      tbody,
    ),
  );
  const empty = emptyState("Nessun mercato trovato",
    marketFilters.onlyLinked ? "Nessun mercato con notizie collegate corrisponde alla ricerca. Togli il filtro «Solo con notizie» per vederli tutti." : "Aggiorna i mercati da Polymarket o cambia la ricerca.");
  empty.hidden = true;

  await load(true);
  return h("div", {},
    pageHead("Mercati", "I mercati Sì/No più scambiati su Polymarket. Il prezzo in centesimi è la probabilità implicita del SÌ."),
    h("div", { class: "filters", role: "group", "aria-label": "Filtri" },
      h("div", { class: "search-wrap" }, icon("search"), search),
      checkField("m-linked", "Solo con notizie", marketFilters.onlyLinked, (v) => { marketFilters.onlyLinked = v; reloadSoon(); }),
      checkField("m-closed", "Includi chiusi", marketFilters.includeClosed, (v) => { marketFilters.includeClosed = v; reloadSoon(); }),
    ),
    h("div", { class: "stack" }, summary, tableWrap, empty, more),
  );
}

function marketRow(m) {
  const p = m.latest_prediction;
  const row = h("tr", { class: "clickable", on: { click: (e) => { if (!e.target.closest("a")) window.location.hash = marketHref(m.id); } } },
    h("td", { class: "q-cell" }, h("a", { href: marketHref(m.id) }, m.question)),
    h("td", { class: "num" }, fmt.cents(m.yes_price)),
    h("td", { class: "num" }, fmt.usd(m.volume)),
    h("td", {}, fmt.date(m.end_date)),
    h("td", { class: "num" }, fmt.int(m.linked_articles)),
    h("td", {}, p ? h("span", { style: { display: "inline-flex", gap: "8px", alignItems: "center" } }, signalBadge(p.signal), h("span", { class: "mono small" }, fmt.pts(p.edge))) : m.closed ? marketStateBadge(m) : h("span", { class: "muted small" }, "–")),
  );
  return row;
}

// ---------- Dettaglio mercato ----------
async function viewMarketDetail(id) {
  const [market] = await Promise.all([api(`/markets/${encodeURIComponent(id)}`), status ? null : loadStatus()]);
  const latest = market.latest_prediction;

  const predictBtn = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"), latest ? "Nuova previsione" : "Chiedi una previsione a Jev");
  let blocker = null;
  if (!isAdmin()) blocker = "Solo gli amministratori possono chiedere previsioni (sono chiamate a pagamento).";
  else if (!status?.jev_enabled) blocker = "Serve TYPESAFE_API_KEY nel file .env.";
  else if (market.closed) blocker = "Il mercato è chiuso.";
  else if (market.evidence.length === 0) blocker = "Nessuna notizia recente collegata a questo mercato.";
  predictBtn.disabled = Boolean(blocker);
  predictBtn.addEventListener("click", async () => {
    setBusy(predictBtn, true);
    try {
      const p = await api(`/markets/${encodeURIComponent(id)}/predict`, { method: "POST" });
      toast(`Previsione salvata: ${p.signal === "BUY_YES" ? "Compra SÌ" : p.signal === "BUY_NO" ? "Compra NO" : "Attendi"} (edge ${fmt.pts(p.edge)})`);
      await loadStatus();
      route({ quiet: true });
    } catch (e) {
      toast(e.message, { error: true });
      setBusy(predictBtn, false);
    }
  });

  const forecastCard = h("section", { class: "card", "aria-labelledby": "h-forecast" },
    h("div", { class: "card-head" }, h("h2", { id: "h-forecast" }, "Ultima previsione"), latest ? signalBadge(latest.signal) : null),
    latest
      ? h("div", {},
        probTrack({ market: latest.market_probability, blended: latest.blended_probability, jev: latest.model_probability }),
        probLegend({ market: latest.market_probability, blended: latest.blended_probability, jev: latest.model_probability }),
        h("div", { class: "opp-figures", style: { marginTop: "16px", gridTemplateColumns: "repeat(3, minmax(0, 1fr))" } },
          h("div", {}, h("div", { class: "fig-label" }, "Edge"), h("div", { class: `fig-value ${latest.edge > 0 ? "pos" : latest.edge < 0 ? "neg" : ""}` }, fmt.pts(latest.edge))),
          h("div", {}, h("div", { class: "fig-label" }, "Puntata"), h("div", { class: "fig-value" }, latest.kelly_fraction > 0 ? fmt.pct(latest.kelly_fraction) : "–")),
          h("div", {}, h("div", { class: "fig-label" }, "Notizie"), h("div", { class: "fig-value" }, fmt.int(latest.article_count))),
        ),
        h("div", { style: { marginTop: "12px" } }, h("div", { class: "fig-label" }, "Forza delle evidenze"), meter(latest.evidence_strength, "Forza delle evidenze")),
        h("p", { class: "muted small", style: { marginTop: "10px" } }, `Calcolata ${fmt.ago(latest.created_at)} con ${latest.model_name || "Jev"}. Il prezzo usato è quello di quel momento.`),
      )
      : h("p", { class: "secondary" }, "Nessuna previsione per questo mercato. Jev legge le regole del mercato e le notizie collegate e stima la probabilità del SÌ, senza vedere il prezzo."),
    h("div", { class: "predict-bar" }, predictBtn, blocker ? h("span", { class: "muted small" }, blocker) : h("span", { class: "muted small" }, "Una chiamata all'API TypeSafe. Il prezzo viene aggiornato prima.")),
  );

  const historyCard = h("section", { class: "card", "aria-labelledby": "h-history" },
    h("div", { class: "card-head" }, h("h2", { id: "h-history" }, "Storico delle previsioni"), h("span", { class: "muted small" }, `${market.predictions.length} previsioni`)),
    market.predictions.length ? historyChart(market.predictions) : h("p", { class: "secondary" }, "Lo storico compare dopo la prima previsione."),
  );

  const evidenceCard = h("section", { class: "card", "aria-labelledby": "h-evidence" },
    h("div", { class: "card-head" },
      h("h2", { id: "h-evidence" }, "Notizie collegate"),
      h("span", { class: "muted small" }, "Somiglianza tra titolo e domanda; rilevanza e impatto secondo Jev"),
    ),
    market.evidence.length
      ? h("div", {}, market.evidence.map((ev) => h("div", { class: "ev" },
        h("div", {},
          externalLink(ev.url, h("span", { class: "ev-title" }, ev.title)),
          h("div", { class: "article-meta", style: { margin: "4px 0 0" } }, h("span", {}, ev.source_name), h("span", { title: fmt.dateTime(ev.published_at) }, fmt.ago(ev.published_at))),
        ),
        h("div", { class: "ev-stats" },
          h("span", {}, "Somiglianza ", h("b", { class: "mono" }, fmt.pct(ev.similarity))),
          ev.relevance != null ? h("span", {}, "Rilevanza ", h("b", { class: "mono" }, fmt.pct(ev.relevance))) : null,
          impactBadge(ev.impact),
        ),
      )))
      : h("p", { class: "secondary" }, "Nessuna notizia delle ultime ore somiglia a questo mercato."),
  );

  return h("div", {},
    h("a", { class: "back", href: "#/mercati" }, icon("back"), "Tutti i mercati"),
    h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "eyebrow" }, "Mercato Polymarket"),
      h("h1", { style: { marginTop: "4px" } }, market.question),
      h("div", { class: "meta-row" },
        marketStateBadge(market),
        h("span", {}, "Prezzo SÌ ", h("b", { class: "mono", style: { color: "var(--text-primary)" } }, fmt.cents(market.yes_price))),
        h("span", {}, `Scade ${fmt.date(market.end_date)}`),
        h("span", {}, `Volume ${fmt.usd(market.volume)}`),
        h("span", {}, `Liquidità ${fmt.usd(market.liquidity)}`),
        market.url ? externalLink(market.url, "Apri su Polymarket", icon("external")) : null,
      ),
    ),
    h("div", { class: "grid-2", style: { marginBottom: "16px" } }, forecastCard, historyCard),
    h("div", { class: "stack" },
      latest ? economicsCard(ctx, market) : null,
      latest ? explainCard(latest, market, market.evidence, status) : null,
      evidenceCard,
      market.description ? h("details", { class: "card rules" }, h("summary", {}, "Regole di risoluzione"), h("p", { class: "rules-text" }, market.description)) : null,
    ),
  );
}

// ---------- Calibrazione ----------
async function viewCalibration() {
  const cal = await api("/predictions/calibration");
  const head = pageHead("Calibrazione", "Quanto sono state accurate le previsioni sui mercati già risolti, rispetto al prezzo di mercato.");
  if (!cal.resolved_markets) {
    return h("div", {}, head, emptyState("Ancora nessun mercato risolto",
      "Quando un mercato con almeno una previsione si chiude, qui trovi il confronto tra Jev, blended e prezzo. Servono decine di mercati risolti prima di fidarsi dei segnali."));
  }
  const better = cal.brier_blended != null && cal.brier_market != null ? (cal.brier_market - cal.brier_blended) / cal.brier_market : null;
  const small = cal.resolved_markets < 30;
  return h("div", {}, head,
    h("div", { class: "grid-2" },
      h("section", { class: "card", "aria-labelledby": "h-verdict" },
        h("h2", { id: "h-verdict", class: "eyebrow" }, "Blended rispetto al mercato"),
        h("div", { class: `hero ${better > 0 ? "pos" : better < 0 ? "neg" : ""}`, style: { marginTop: "8px" } }, better == null ? "–" : `${better > 0 ? "−" : "+"}${fmt.pct(Math.abs(better))}`),
        h("p", { class: "secondary", style: { marginTop: "6px" } },
          better == null ? "Dati insufficienti."
            : better > 0 ? "di errore rispetto al prezzo di mercato: le previsioni blended sono state più accurate."
              : "di errore rispetto al prezzo di mercato: il prezzo è stato più accurato delle previsioni."),
        h("p", { class: "muted small", style: { marginTop: "10px" } }, `Su ${fmt.int(cal.resolved_markets)} mercati risolti, usando l'ultima previsione fatta per ciascuno.`),
        small ? h("p", { class: "note", style: { marginTop: "12px" } }, h("span", { class: "badge badge-warning" }, icon("alert"), "Campione piccolo"), " Con meno di 30 mercati il confronto dipende molto dal caso.") : null,
      ),
      h("section", { class: "card", "aria-labelledby": "h-brier" },
        h("div", { class: "card-head" }, h("h2", { id: "h-brier" }, "Brier score"), h("span", { class: "muted small" }, "più basso è meglio")),
        brierBars([
          { key: "market", label: "Prezzo mercato", value: cal.brier_market, first: true },
          { key: "jev", label: "Stima Jev", value: cal.brier_model },
          { key: "blended", label: "Blended", value: cal.brier_blended },
        ]),
        h("p", { class: "muted small", style: { marginTop: "16px" } }, "Media di (probabilità − esito)², con esito 1 se il mercato si è risolto SÌ e 0 se NO. Chi dice sempre 50% ottiene 0,25."),
      ),
    ),
  );
}

// ---------- Account ----------
async function viewAccount() {
  const fields = {};
  const field = (id, label, autocomplete, hint) => {
    const input = h("input", { id, class: "input", type: "password", autocomplete, required: true, maxlength: "256", "aria-describedby": `${id}-msg` });
    const msg = h("p", { class: "field-hint", id: `${id}-msg` }, hint || "");
    fields[id] = { input, msg, hint: hint || "" };
    return h("div", { class: "form-field" }, h("label", { for: id }, label), input, msg);
  };
  const setError = (id, text) => {
    const f = fields[id];
    f.msg.textContent = text || f.hint;
    f.msg.classList.toggle("field-error", Boolean(text));
    f.input.setAttribute("aria-invalid", text ? "true" : "false");
  };
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Cambia password");
  const form = h("form", { class: "form", novalidate: true },
    field("pw-current", "Password attuale", "current-password"),
    field("pw-new", "Nuova password", "new-password", "Almeno 12 caratteri, senza lo username. Una frase lunga è più sicura e più facile da ricordare."),
    field("pw-confirm", "Ripeti la nuova password", "new-password"),
    submit,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const cur = fields["pw-current"].input.value, next = fields["pw-new"].input.value, confirm = fields["pw-confirm"].input.value;
    Object.keys(fields).forEach((id) => setError(id, ""));
    let first = null;
    const fail = (id, text) => { setError(id, text); first = first || id; };
    if (!cur) fail("pw-current", "Inserisci la password attuale.");
    if (next.length < 12) fail("pw-new", "La nuova password deve avere almeno 12 caratteri.");
    if (next && confirm !== next) fail("pw-confirm", "Le due password non coincidono.");
    if (first) return fields[first].input.focus();
    setBusy(submit, true);
    try {
      await api("/auth/password", { method: "POST", body: { current_password: cur, new_password: next } });
      form.reset();
      toast("Password aggiornata. Le sessioni aperte su altri dispositivi sono state chiuse.");
    } catch (err) {
      if (err.status === 400) { setError("pw-current", err.message); fields["pw-current"].input.focus(); }
      else if (err.status === 422) { setError("pw-new", err.message); fields["pw-new"].input.focus(); }
      else toast(err.message, { error: true });
    } finally {
      setBusy(submit, false);
    }
  });

  return h("div", {},
    pageHead("Account", "Il tuo profilo e la password di accesso."),
    h("div", { class: "grid-2" },
      h("section", { class: "card", "aria-labelledby": "h-profile", style: { alignSelf: "start" } },
        h("h2", { id: "h-profile" }, "Profilo"),
        h("dl", { class: "dl" },
          h("dt", {}, "Username"), h("dd", { class: "mono" }, me.username),
          h("dt", {}, "Ruolo"), h("dd", {}, isAdmin()
            ? h("span", { class: "badge badge-outline" }, "Amministratore")
            : h("span", { class: "badge badge-outline" }, "Sola lettura")),
          h("dt", {}, "Permessi"), h("dd", { class: "secondary" }, isAdmin()
            ? "Consulta i dati, aggiorna notizie e mercati, chiede previsioni a Jev."
            : "Consulta notizie, mercati, previsioni e calibrazione."),
        ),
        h("button", { class: "btn btn-ghost", type: "button", style: { marginTop: "16px" }, on: { click: logout } }, icon("logout"), "Esci"),
      ),
      h("section", { class: "card", "aria-labelledby": "h-password" },
        h("h2", { id: "h-password", style: { marginBottom: "12px" } }, "Cambia password"),
        form,
      ),
    ),
  );
}

// ---------- Context shared with the view modules ----------
const ctx = {
  get status() { return status; },
  isAdmin,
  pageHead,
  rangeField,
  checkField,
  setBusy,
  rerender: () => route({ quiet: true }),
};

// ---------- Boot ----------
setupShell();
window.addEventListener("hashchange", () => route());
checkSession().then(async (ok) => {
  if (!ok) return showLogin();
  await loadStatus();
  route();
});
