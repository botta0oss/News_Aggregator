import { t, lang, setLang } from "./i18n.js";
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
import { bulkPredict } from "./views/bulk.js";
import { viewAlerts } from "./views/alerts.js";
import { viewBacktest } from "./views/backtest.js";
import { viewMultiList, viewMultiDetail, eventCard } from "./views/multi.js";
import { viewUsage } from "./views/usage.js";
import { renderNav, markCurrent, setBadge, setupCollapse, setupMenu } from "./nav.js";
import { economicsCard, verdictBadge } from "./economics.js";
import { planLine } from "./strategy.js";

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
  for (const id of ["sidebar", "bottom-nav", "actions", "footer"]) document.getElementById(id).hidden = false;
  document.body.classList.add("signed-in");
  renderNav({ user: me, onLogout: logout });
  // Jobs and paid API calls are admin-only: viewers don't get the buttons
  document.querySelectorAll(".admin-only").forEach((el) => { el.hidden = !isAdmin(); });
}

function setSignedOut() {
  me = null;
  status = null;
  setCsrfToken(null);
  for (const id of ["sidebar", "bottom-nav", "actions", "footer", "banner", "more-sheet"]) document.getElementById(id).hidden = true;
  document.body.classList.remove("signed-in", "no-scroll");
  document.getElementById("topbar-status").textContent = "";
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
  const reveal = h("button", { class: "input-addon", type: "button", "aria-label": t("Mostra password"), "aria-pressed": "false" }, icon("eye"));
  reveal.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(show));
    reveal.setAttribute("aria-label", show ? t("Nascondi password") : t("Mostra password"));
  });
  const submit = h("button", { class: "btn btn-primary btn-block", type: "submit" }, h("span", { class: "spinner", "aria-hidden": "true" }), t("Accedi"));
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "form-field" }, h("label", { for: "login-username" }, t("Username")), username),
    h("div", { class: "form-field" }, h("label", { for: "login-password" }, t("Password")), h("div", { class: "input-group" }, password, reveal)),
    error, submit,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!username.value.trim() || !password.value) {
      error.textContent = t("Inserisci username e password.");
      error.hidden = false;
      (username.value.trim() ? password : username).focus();
      return;
    }
    setBusy(submit, true);
    error.hidden = true;
    try {
      const info = await api("/auth/login", { method: "POST", body: { username: username.value, password: password.value }, handle401: false });
      // The password was right: if the session is not there, the browser refused the cookie
      try {
        await api("/auth/me", { handle401: false });
      } catch (err) {
        if (err.status === 401) throw new Error(cookieRefusedMessage());
        throw err;
      }
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
        h("h1", { id: "login-title" }, t("Accedi")),
        h("p", { class: "secondary small" }, t("Dashboard privata di News × Markets.")),
      )),
      form,
      h("p", { class: "muted small" }, t("Non hai un account? Chiedi a chi gestisce il server di crearlo con "),
        h("code", {}, "python -m backend.auth.cli create-user"), "."),
    ),
  );
}

function cookieRefusedMessage() {
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  if (window.location.protocol === "http:" && !local) {
    return t("Password corretta, ma il browser non ha salvato la sessione: il cookie è riservato alle connessioni HTTPS ")
      + t("e stai usando HTTP da un indirizzo di rete. Su una rete di casa fidata imposta SESSION_COOKIE_SECURE=false ")
      + t("nel file .env e riavvia; altrimenti usa HTTPS (per esempio con Cloudflare Tunnel).");
  }
  return t("Password corretta, ma il browser non ha salvato la sessione. Controlla che i cookie non siano bloccati per questo sito.");
}

async function logout() {
  try {
    await api("/auth/logout", { method: "POST", handle401: false });
  } catch { /* the session is gone either way */ }
  afterLogin = null;
  showLogin(t("Sei uscito. A presto."));
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
  renderStatusLine();
  refreshBadges();
  return status;
}

function renderBanner() {
  const banner = document.getElementById("banner");
  clear(banner);
  if (!status) {
    banner.append(icon("alert"), h("span", {}, t("Il server non risponde. Controlla che l'API sia avviata e che il database sia raggiungibile.")));
    banner.hidden = false;
  } else if (!status.jev_enabled) {
    banner.append(icon("alert"), h("span", {},
      t("Previsioni disattivate: manca la chiave TypeSafe. Aggiungi "), h("code", {}, "TYPESAFE_API_KEY"),
      t(" al file .env e riavvia. Nel frattempo le notizie vengono classificate con un'euristica locale."),
    ));
    banner.hidden = false;
  } else if (status.usage?.blocked) {
    banner.append(icon("alert"), h("span", {},
      t("Limite giornaliero delle API AI raggiunto: previsioni, allerte e backtest sono in pausa fino a mezzanotte. "),
      h("a", { href: "#/uso" }, t("Vedi uso e costi"))));
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

/** One line in the top bar: is the data fresh and is Jev working? */
function renderStatusLine() {
  const el = document.getElementById("topbar-status");
  if (!status) return el.replaceChildren(h("span", { class: "dot warn", "aria-hidden": "true" }), t("Server non raggiungibile"));
  const parts = [
    status.last_article_at ? t("Ultima notizia {0}", fmt.ago(status.last_article_at)) : t("Nessuna notizia ancora"),
    t("{0} mercati aperti", fmt.int(status.open_markets)),
    t("Jev {0}", status.jev_enabled ? t("attivo") : t("non configurato")),
    status.prediction_auto ? t("previsioni automatiche") : t("previsioni manuali"),
  ];
  el.replaceChildren(h("span", { class: `dot${status.jev_enabled ? "" : " warn"}`, "aria-hidden": "true" }), parts.join(" · "));
  el.title = el.textContent;
}

/** Opportunities from the alerts of the last 24 hours, as a badge on "Allerte". */
async function refreshBadges() {
  try {
    const items = await api("/alerts", { params: { kind: "opportunities", limit: 100 } });
    const since = Date.now() - 86_400_000;
    setBadge("allerte", items.filter((a) => new Date(a.created_at).getTime() >= since).length);
  } catch { /* the badge is optional */ }
}

function setBusy(btn, busy) {
  btn.disabled = busy;
  btn.classList.toggle("loading", busy);
  btn.setAttribute("aria-busy", String(busy));
}

/** Starts a background job, then refreshes the page a few times while it runs. */
async function runJob(btn, path, startedMessage) {
  const jobs = document.getElementById("btn-jobs");
  setBusy(btn, true);
  setBusy(jobs, true);
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
    setBusy(jobs, false);
  }
}

function setupShell() {
  window.__appStarted = true; // read by boot-check.js
  document.querySelector(".boot-error")?.remove();
  hydrateIcons();
  setupCollapse();
  setupMenu(document.getElementById("btn-jobs"), document.getElementById("jobs-menu"));
  window.addEventListener("auth:required", () => {
    if (me) showLogin(t("La sessione è scaduta. Accedi di nuovo."));
  });
  const ingest = document.getElementById("btn-ingest");
  const sync = document.getElementById("btn-sync");
  ingest.addEventListener("click", () => runJob(ingest, "/ingest", t("Aggiornamento notizie avviato. La pagina si aggiorna da sola.")));
  sync.addEventListener("click", () => runJob(sync, "/markets/sync", t("Aggiornamento mercati avviato. La pagina si aggiorna da sola.")));

  // Language: IT / EN next to the theme button; switching reloads the page in the other language
  for (const btn of document.querySelectorAll("#lang-switch [data-lang]")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.lang === lang));
    btn.addEventListener("click", () => { if (btn.dataset.lang !== lang) setLang(btn.dataset.lang); });
  }
  localizeStatic();

  const themeBtn = document.getElementById("btn-theme");
  const paintThemeButton = () => {
    const light = document.documentElement.dataset.theme === "light";
    themeBtn.replaceChildren(icon(light ? "moon" : "sun"));
    themeBtn.setAttribute("aria-label", light ? t("Passa al tema scuro") : t("Passa al tema chiaro"));
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

/** The fixed texts of index.html, in the interface language. */
function localizeStatic() {
  const text = (sel, value) => { const el = document.querySelector(sel); if (el) el.textContent = value; };
  const attr = (sel, name, value) => document.querySelectorAll(sel).forEach((el) => el.setAttribute(name, value));
  text(".skip-link", t("Vai al contenuto"));
  attr("#sidebar", "aria-label", t("Menu principale"));
  attr(".brand", "aria-label", t("News × Markets, vai alle opportunità"));
  attr("#side-nav", "aria-label", t("Sezioni"));
  text("#btn-jobs .btn-label", t("Aggiorna"));
  text("#btn-ingest b", t("Aggiorna notizie"));
  text("#btn-ingest .muted", t("Scarica le fonti, classifica e collega ai mercati"));
  text("#btn-sync b", t("Aggiorna mercati"));
  text("#btn-sync .muted", t("Prezzi da Polymarket e collegamento delle notizie"));
  text("#footer p", t("Segnali indicativi, non consulenza finanziaria. L'app non piazza ordini su Polymarket."));
  attr("#bottom-nav", "aria-label", t("Sezioni principali"));
  text("#more-title", t("Tutte le sezioni"));
  attr("#btn-more-close", "aria-label", t("Chiudi"));
  attr("#sheet-nav", "aria-label", t("Tutte le sezioni"));
  attr("#lang-switch", "aria-label", t("Lingua"));
  attr('meta[name="description"]', "content", t("Notizie classificate con TypeSafe Jev e previsioni sui mercati Polymarket"));
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
  [/^#\/allerte$/, "allerte", () => viewAlerts(ctx)],
  [/^#\/multi$/, "multi", () => viewMultiList(ctx)],
  [/^#\/uso$/, "uso", () => viewUsage(ctx)],
  [/^#\/multi\/(.+)$/, "multi", (id) => viewMultiDetail(ctx, id)],
  [/^#\/backtest(?:\/([\w-]+))?$/, "backtest", (id) => viewBacktest(ctx, id)],
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
  markCurrent(tab);
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
    clear(view).append(emptyState(t("Impossibile caricare la pagina"), e.message,
      h("button", { class: "btn btn-ghost", type: "button", on: { click: () => route() } }, icon("refresh"), t("Riprova"))));
  }
}

function pageHead(title, subtitle, ...right) {
  right = right.filter(Boolean);
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
  // Multi-outcome events: a separate block, read as distributions
  const multiHead = h("div", { class: "section-head", hidden: true },
    h("h2", {}, t("Mercati a più esiti")), h("a", { class: "btn btn-ghost btn-sm", href: "#/multi" }, t("Tutti gli eventi")));
  const multiList = h("div", { class: "multi-grid" });
  const reloadMulti = async () => {
    try {
      const events = await api("/multi/opportunities", {
        params: { min_edge: oppFilters.minEdge / 100, min_evidence: oppFilters.minEvidence / 100, include_hold: oppFilters.includeHold },
      });
      multiHead.hidden = !events.length;
      multiList.replaceChildren(...events.map(eventCard));
    } catch { multiHead.hidden = true; multiList.replaceChildren(); }
  };
  const reload = async () => {
    reloadMulti();
    try {
      const opps = await api("/predictions/opportunities", {
        params: { min_edge: oppFilters.minEdge / 100, min_evidence: oppFilters.minEvidence / 100, include_hold: oppFilters.includeHold, limit: 100 },
      });
      const active = opps.filter((o) => o.prediction.signal !== "HOLD").length;
      kpis.replaceChildren(
        statTile(t("Mercati aperti"), fmt.int(status?.open_markets ?? 0), t("binari Sì/No su Polymarket")),
        statTile(t("Con notizie collegate"), fmt.int(status?.linked_markets ?? 0), t("notizie recenti simili alla domanda")),
        statTile(t("Previsioni Jev"), fmt.int(status?.predictions ?? 0), status?.last_prediction_at ? t("ultima {0}", fmt.ago(status.last_prediction_at)) : t("nessuna ancora")),
        statTile(t("Segnali attivi"), fmt.int(active), t("edge ≥ {0} pt", oppFilters.minEdge)),
      );
      list.replaceChildren(...(opps.length ? opps.map(opportunityCard) : [opportunitiesEmpty()]));
    } catch (e) {
      list.replaceChildren(emptyState(t("Impossibile caricare le opportunità"), e.message));
    }
  };
  const reloadSoon = debounce(reload, 250);

  const filters = h("div", { class: "filters", role: "group", "aria-label": t("Filtri") },
    rangeField("f-edge", t("Edge minimo"), { min: 0, max: 30, step: 1, value: oppFilters.minEdge, format: (v) => `${v} ${t("pt")}` }, (v) => { oppFilters.minEdge = v; reloadSoon(); }),
    rangeField("f-evidence", t("Evidenze minime"), { min: 0, max: 100, step: 5, value: oppFilters.minEvidence, format: (v) => `${v}%` }, (v) => { oppFilters.minEvidence = v; reloadSoon(); }),
    checkField("f-hold", t("Mostra anche «Attendi»"), oppFilters.includeHold, (v) => { oppFilters.includeHold = v; reload(); }),
  );

  await reload();
  const bulk = bulkPredict(ctx);
  return h("div", {},
    pageHead(t("Opportunità"), t("Mercati in cui la stima di Jev, pesata per la forza delle notizie, si discosta dal prezzo. Ordinati per edge."),
      h("a", { class: "btn btn-ghost", href: "#/metodo" }, icon("help"), t("Come funziona")), bulk.button),
    bulk.panel, kpis, filters, list,
    h("div", { class: "opps-multi" }, multiHead, multiList),
  );
}

function opportunitiesEmpty() {
  if (!status) return emptyState(t("Nessun dato"), t("Il server non risponde."));
  if (status.open_markets === 0) {
    return emptyState(t("Nessun mercato ancora"), isAdmin() ? t("Scarica i mercati Sì/No più scambiati da Polymarket per iniziare.") : t("Un amministratore deve scaricare i mercati da Polymarket."),
      isAdmin() ? h("button", { class: "btn btn-primary", type: "button", on: { click: () => document.getElementById("btn-sync").click() } }, icon("sync"), t("Aggiorna mercati")) : null);
  }
  if (status.linked_markets === 0) {
    return emptyState(t("Nessuna notizia collegata ai mercati"), t("Le notizie recenti non somigliano ancora a nessuna domanda di mercato. Aggiorna le notizie o abbassa MARKET_MATCH_THRESHOLD nel file .env."),
      isAdmin() ? h("button", { class: "btn btn-primary", type: "button", on: { click: () => document.getElementById("btn-ingest").click() } }, icon("refresh"), t("Aggiorna notizie")) : null);
  }
  if (!status.jev_enabled) {
    return emptyState(t("Previsioni non disponibili"), t("Configura TYPESAFE_API_KEY per ottenere le stime di Jev sui mercati con notizie collegate."));
  }
  if (status.predictions === 0) {
    return emptyState(t("Nessuna previsione ancora"), t("{0} mercati hanno notizie collegate. Apri un mercato e chiedi una previsione a Jev.", fmt.int(status.linked_markets)),
      h("a", { class: "btn btn-primary", href: "#/mercati" }, t("Vedi i mercati con notizie")));
  }
  return emptyState(t("Nessuna opportunità con questi filtri"), t("Abbassa l'edge o le evidenze minime, oppure mostra anche i mercati in attesa."));
}

function opportunityCard({ market, prediction: p }) {
  const edgeCls = p.edge > 0 ? "pos" : p.edge < 0 ? "neg" : "";
  return h("article", { class: "card opp" },
    h("div", {},
      h("a", { class: "opp-title", href: marketHref(market.id) }, market.question),
      h("div", { class: "opp-meta" },
        h("span", {}, t("Scade {0}", fmt.date(market.end_date))),
        h("span", {}, t("Volume {0}", fmt.usd(market.volume))),
        h("span", {}, t("{0} notizie analizzate", p.article_count)),
        h("span", { title: fmt.dateTime(p.created_at) }, t("Previsione {0}", fmt.ago(p.created_at))),
      ),
      probTrack({ market: p.market_probability, blended: p.blended_probability, jev: p.model_probability }),
      probLegend({ market: p.market_probability, blended: p.blended_probability, jev: p.model_probability }),
      h("p", { class: "opp-explain" }, explainSentence(p, status), " ", h("a", { href: marketHref(market.id) }, t("Vedi il calcolo"))),
      planLine(p.economics),
    ),
    h("div", { class: "opp-side" },
      h("div", { class: "opp-side-top" },
        signalBadge(p.signal),
        market.url ? externalLink(market.url, t("Polymarket "), icon("external")) : null,
      ),
      h("div", { class: "opp-figures" },
        h("div", {}, h("div", { class: "fig-label" }, t("Edge"), infoTip(GLOSSARY.edge)), h("div", { class: `fig-value ${edgeCls}` }, fmt.pts(p.edge))),
        p.economics
          ? h("div", {}, h("div", { class: "fig-label" }, t("Conviene?"), infoTip(t("Valutazione economica al momento della previsione: prezzo reale dal book, costi, incertezza, tempo e limiti del preset."))),
            verdictBadge(p.economics.verdict),
            p.economics.stale?.length ? h("div", { class: "fig-label", style: { marginTop: "4px" } }, t("da ricalcolare")) : null,
            p.economics.verdict !== "NO" ? h("div", { class: "fig-label", style: { marginTop: "4px" } }, t("Puntata {0}", fmt.money(p.economics.outlay))) : null)
          : h("div", {}, h("div", { class: "fig-label" }, t("Kelly semplice"), infoTip(GLOSSARY.kelly)), h("div", { class: "fig-value" }, p.kelly_fraction > 0 ? `${fmt.pct(p.kelly_fraction)}` : "–"),
            p.kelly_fraction > 0 ? h("div", { class: "fig-label" }, t("del bankroll")) : null),
      ),
      h("div", {}, h("div", { class: "fig-label" }, t("Forza delle evidenze"), infoTip(GLOSSARY.evidence)), meter(p.evidence_strength, t("Forza delle evidenze"))),
    ),
  );
}

// ---------- Mercati ----------
// key -> label, default direction, direction wording (what "asc" and "desc" mean for this field)
const MARKET_SORTS = {
  volume: { label: t("Volume"), dir: "desc", asc: t("dal più basso"), desc: t("dal più alto") },
  end_date: { label: t("Scadenza"), dir: "asc", asc: t("prima i più vicini"), desc: t("prima i più lontani") },
  price: { label: t("Prezzo SÌ"), dir: "desc", asc: t("dal più basso"), desc: t("dal più alto") },
  signal: { label: t("Ultimo segnale"), dir: "desc", asc: t("prima i meno recenti"), desc: t("prima i più recenti") },
  edge: { label: t("Edge"), dir: "desc", asc: t("dal più piccolo"), desc: t("dal più grande") },
  news: { label: t("Notizie collegate"), dir: "desc", asc: t("prima le meno"), desc: t("prima le più") },
  liquidity: { label: t("Liquidità"), dir: "desc", asc: t("dalla più bassa"), desc: t("dalla più alta") },
  question: { label: t("Nome"), dir: "asc", asc: t("A → Z"), desc: t("Z → A") },
};
const marketFilters = { q: "", onlyLinked: null, includeClosed: false, sort: "volume", order: null };
try {
  const saved = JSON.parse(localStorage.getItem("markets-sort") || "null");
  if (saved && MARKET_SORTS[saved.sort]) Object.assign(marketFilters, { sort: saved.sort, order: saved.order === "asc" || saved.order === "desc" ? saved.order : null });
} catch { /* storage unavailable: keep the default */ }
const PAGE = 50;

const sortDir = () => marketFilters.order || MARKET_SORTS[marketFilters.sort].dir;

function setMarketSort(key, order = null) {
  marketFilters.sort = key;
  marketFilters.order = order;
  try { localStorage.setItem("markets-sort", JSON.stringify({ sort: key, order })); } catch { /* ignore */ }
}

async function viewMarkets() {
  await loadStatus();
  if (marketFilters.onlyLinked == null) marketFilters.onlyLinked = (status?.linked_markets ?? 0) > 0;

  const tbody = h("tbody", {});
  const summary = h("p", { class: "muted small", role: "status", "aria-live": "polite" });
  const more = h("div", { class: "more" });
  const headRow = h("tr", {});
  const sortControls = h("div", { class: "sort-controls" });
  let offset = 0;
  let loadToken = 0;

  const load = async (reset) => {
    const token = ++loadToken;
    if (reset) offset = 0;
    const data = await api("/markets", {
      params: {
        q: marketFilters.q, only_linked: marketFilters.onlyLinked, include_closed: marketFilters.includeClosed,
        sort: marketFilters.sort, order: marketFilters.order, limit: PAGE, offset,
      },
    });
    if (token !== loadToken) return;
    if (reset) clear(tbody);
    tbody.append(...data.markets.map(marketRow));
    offset += data.markets.length;
    const s = MARKET_SORTS[marketFilters.sort];
    summary.textContent = data.total
      ? t("{0} di {1}, ordinati per {2} ({3})", fmt.int(offset), fmt.count(data.total, t("mercato"), t("mercati")), s.label.charAt(0).toLowerCase() + s.label.slice(1), s[sortDir()]) : "";
    more.replaceChildren(offset < data.total
      ? h("button", { class: "btn btn-ghost", type: "button", on: { click: () => load(false) } }, t("Carica altri"))
      : "");
    tableWrap.hidden = data.total === 0;
    empty.hidden = data.total !== 0;
  };
  const reload = () => { paintSort(); return load(true).catch((e) => toast(e.message, { error: true })); };
  const reloadSoon = debounce(reload, 300);

  // Column headers: click sorts, a second click on the same column reverses the direction
  const COLUMNS = [
    ["question", t("Mercato"), ""], ["price", t("Prezzo SÌ"), "num"], ["volume", t("Volume"), "num"], ["liquidity", t("Liquidità"), "num"],
    ["end_date", t("Scadenza"), ""], ["news", t("Notizie"), "num"], ["signal", t("Ultimo segnale"), ""], ["edge", t("Edge"), "num"],
  ];
  function paintSort() {
    const dir = sortDir();
    headRow.replaceChildren(...COLUMNS.map(([key, label, cls]) => {
      const active = marketFilters.sort === key;
      const btn = h("button", { class: `th-sort${active ? " active" : ""}`, type: "button",
        title: active ? t("Ordinato per {0}, {1}. Clic per invertire", label.toLowerCase(), MARKET_SORTS[key][dir]) : t("Ordina per {0}", label.toLowerCase()) },
      label, icon(active ? (dir === "asc" ? "arrowUp" : "arrowDown") : "sort", `icon-svg sort-icon${active ? "" : " sort-idle"}`));
      btn.addEventListener("click", () => {
        if (active) setMarketSort(key, dir === "asc" ? "desc" : "asc");
        else setMarketSort(key, null);
        reload();
      });
      return h("th", { class: cls, "aria-sort": active ? (dir === "asc" ? "ascending" : "descending") : "none" }, btn);
    }));

    const select = h("select", { id: "m-sort", class: "select" },
      Object.entries(MARKET_SORTS).map(([key, s]) => h("option", { value: key, selected: key === marketFilters.sort }, s.label)));
    select.addEventListener("change", () => { setMarketSort(select.value, null); reload(); });
    const s = MARKET_SORTS[marketFilters.sort];
    const flip = h("button", { class: "btn btn-ghost", type: "button", "aria-label": t("Direzione: {0}. Clic per invertire", s[dir]) },
      icon(dir === "asc" ? "arrowUp" : "arrowDown"), s[dir]);
    flip.addEventListener("click", () => { setMarketSort(marketFilters.sort, dir === "asc" ? "desc" : "asc"); reload(); });
    sortControls.replaceChildren(h("label", { class: "field", for: "m-sort" }, t("Ordina per"), select), flip);
  }

  const search = h("input", { id: "m-search", class: "search", type: "search", placeholder: t("Cerca un mercato (es. Fed, elezioni, Bitcoin)"), value: marketFilters.q, "aria-label": t("Cerca un mercato") });
  search.addEventListener("input", () => { marketFilters.q = search.value.trim(); reloadSoon(); });

  const tableWrap = h("div", { class: "table-wrap" },
    h("table", { class: "markets-table" }, h("thead", {}, headRow), tbody),
  );
  const empty = emptyState(t("Nessun mercato trovato"),
    marketFilters.onlyLinked ? t("Nessun mercato con notizie collegate corrisponde alla ricerca. Togli il filtro «Solo con notizie» per vederli tutti.") : t("Aggiorna i mercati da Polymarket o cambia la ricerca."));
  empty.hidden = true;

  paintSort();
  await load(true);
  const bulk = bulkPredict(ctx);
  return h("div", {},
    pageHead(t("Mercati"), t("I mercati Sì/No più scambiati su Polymarket. Il prezzo in centesimi è la probabilità implicita del SÌ."), bulk.button),
    bulk.panel,
    h("div", { class: "filters", role: "group", "aria-label": t("Filtri e ordinamento") },
      h("div", { class: "search-wrap" }, icon("search"), search),
      checkField("m-linked", t("Solo con notizie"), marketFilters.onlyLinked, (v) => { marketFilters.onlyLinked = v; reloadSoon(); }),
      checkField("m-closed", t("Includi chiusi"), marketFilters.includeClosed, (v) => { marketFilters.includeClosed = v; reloadSoon(); }),
      sortControls,
    ),
    h("div", { class: "stack" }, summary, tableWrap, empty, more),
  );
}

function daysLeft(end) {
  if (!end) return null;
  const days = Math.ceil((new Date(end).getTime() - Date.now()) / 86_400_000);
  return days < 0 ? t("scaduto") : days === 0 ? t("oggi") : days === 1 ? t("domani") : t("tra {0} gg", fmt.int(days));
}

function marketRow(m) {
  const p = m.latest_prediction;
  const left = daysLeft(m.end_date);
  const row = h("tr", { class: "clickable", on: { click: (e) => { if (!e.target.closest("a")) window.location.hash = marketHref(m.id); } } },
    h("td", { class: "q-cell" }, h("a", { href: marketHref(m.id) }, m.question)),
    h("td", { class: "num" }, fmt.cents(m.yes_price)),
    h("td", { class: "num" }, fmt.usd(m.volume)),
    h("td", { class: "num" }, fmt.usd(m.liquidity)),
    h("td", { class: "nowrap" }, fmt.date(m.end_date), left ? h("div", { class: "muted small" }, left) : null),
    h("td", { class: "num" }, fmt.int(m.linked_articles)),
    h("td", { class: "nowrap" }, p
      ? [signalBadge(p.signal), h("div", { class: "muted small", title: fmt.dateTime(p.created_at) }, fmt.ago(p.created_at))]
      : m.closed ? marketStateBadge(m) : h("span", { class: "muted small" }, t("Nessuna previsione"))),
    h("td", { class: `num ${p ? (p.edge > 0 ? "pos" : p.edge < 0 ? "neg" : "") : ""}` }, p ? fmt.pts(p.edge) : "–"),
  );
  return row;
}

// ---------- Dettaglio mercato ----------
async function viewMarketDetail(id) {
  const [market] = await Promise.all([api(`/markets/${encodeURIComponent(id)}`), status ? null : loadStatus()]);
  const latest = market.latest_prediction;

  const predictBtn = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"), latest ? t("Nuova previsione") : t("Chiedi una previsione a Jev"));
  let blocker = null;
  if (!isAdmin()) blocker = t("Solo gli amministratori possono chiedere previsioni (sono chiamate a pagamento).");
  else if (!status?.jev_enabled) blocker = t("Serve TYPESAFE_API_KEY nel file .env.");
  else if (market.closed) blocker = t("Il mercato è chiuso.");
  else if (market.evidence.length === 0) blocker = t("Nessuna notizia recente collegata a questo mercato.");
  predictBtn.disabled = Boolean(blocker);
  predictBtn.addEventListener("click", async () => {
    setBusy(predictBtn, true);
    try {
      const p = await api(`/markets/${encodeURIComponent(id)}/predict`, { method: "POST" });
      toast(t("Previsione salvata: {0} (edge {1})", p.signal === "BUY_YES" ? t("Compra SÌ") : p.signal === "BUY_NO" ? t("Compra NO") : t("Attendi"), fmt.pts(p.edge)));
      await loadStatus();
      route({ quiet: true });
    } catch (e) {
      toast(e.message, { error: true });
      setBusy(predictBtn, false);
    }
  });

  const forecastCard = h("section", { class: "card", "aria-labelledby": "h-forecast" },
    h("div", { class: "card-head" }, h("h2", { id: "h-forecast" }, t("Ultima previsione")), latest ? signalBadge(latest.signal) : null),
    latest
      ? h("div", {},
        probTrack({ market: latest.market_probability, blended: latest.blended_probability, jev: latest.model_probability }),
        probLegend({ market: latest.market_probability, blended: latest.blended_probability, jev: latest.model_probability }),
        h("div", { class: "opp-figures", style: { marginTop: "16px", gridTemplateColumns: "repeat(3, minmax(0, 1fr))" } },
          h("div", {}, h("div", { class: "fig-label" }, t("Edge")), h("div", { class: `fig-value ${latest.edge > 0 ? "pos" : latest.edge < 0 ? "neg" : ""}` }, fmt.pts(latest.edge))),
          h("div", {}, h("div", { class: "fig-label" }, t("Kelly semplice"), infoTip(GLOSSARY.kelly)), h("div", { class: "fig-value" }, latest.kelly_fraction > 0 ? fmt.pct(latest.kelly_fraction) : "–")),
          h("div", {}, h("div", { class: "fig-label" }, t("Notizie")), h("div", { class: "fig-value" }, fmt.int(latest.article_count))),
        ),
        h("div", { style: { marginTop: "12px" } }, h("div", { class: "fig-label" }, t("Forza delle evidenze")), meter(latest.evidence_strength, t("Forza delle evidenze"))),
        h("p", { class: "muted small", style: { marginTop: "10px" } }, t("Calcolata {0} con {1}. Il prezzo usato è quello di quel momento.", fmt.ago(latest.created_at), latest.model_name || t("Jev"))),
      )
      : h("p", { class: "secondary" }, t("Nessuna previsione per questo mercato. Jev legge le regole del mercato e le notizie collegate e stima la probabilità del SÌ, senza vedere il prezzo.")),
    h("div", { class: "predict-bar" }, predictBtn, blocker ? h("span", { class: "muted small" }, blocker) : h("span", { class: "muted small" }, t("Una chiamata all'API TypeSafe. Il prezzo viene aggiornato prima."))),
  );

  const historyCard = h("section", { class: "card", "aria-labelledby": "h-history" },
    h("div", { class: "card-head" }, h("h2", { id: "h-history" }, t("Storico delle previsioni")), h("span", { class: "muted small" }, t("{0} previsioni", market.predictions.length))),
    market.predictions.length ? historyChart(market.predictions) : h("p", { class: "secondary" }, t("Lo storico compare dopo la prima previsione.")),
  );

  const sentToJev = status?.market_max_articles ?? 8;
  let searchBtn = null;
  if (isAdmin() && !market.closed && status?.targeted_news_enabled) {
    searchBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button", title: t("Cerca su Google News le notizie delle ultime ore su questo mercato") },
      h("span", { class: "spinner", "aria-hidden": "true" }), icon("search"), t("Cerca notizie"));
    searchBtn.addEventListener("click", async () => {
      setBusy(searchBtn, true);
      try {
        const r = await api(`/markets/${encodeURIComponent(id)}/search-news`, { method: "POST" });
        toast(r.added ? t("{0} trovate con «{1}».", fmt.count(r.added, t("notizia nuova"), t("notizie nuove")), r.query) : t("Nessuna notizia nuova per «{0}».", r.query));
        route({ quiet: true });
      } catch (e) {
        toast(e.message, { error: true });
        setBusy(searchBtn, false);
      }
    });
  }
  const evidenceCard = h("section", { class: "card", "aria-labelledby": "h-evidence" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { id: "h-evidence" }, t("Notizie collegate")),
        h("p", { class: "muted small" }, t("Una per storia, dalla più utile. Le prime {0} vengono lette da Jev.", sentToJev)),
      ),
      h("div", { class: "actions" },
        infoTip(t("Pertinenza: somiglianza di significato tra notizia e domanda, più la presenza dei termini chiave (nomi, sigle, numeri). Utilità: pertinenza × affidabilità della fonte × freschezza × giudizio di Jev. Rilevanza e impatto: il giudizio di Jev dopo l'ultima previsione."), t("Come sono ordinate?")),
        searchBtn),
    ),
    market.evidence.length
      ? h("div", {}, market.evidence.map((ev, i) => h("div", { class: `ev${i >= sentToJev ? " ev-extra" : ""}` },
        h("div", {},
          externalLink(ev.url, h("span", { class: "ev-title" }, ev.title)),
          h("div", { class: "article-meta", style: { margin: "4px 0 0" } },
            h("span", {}, ev.source_name),
            h("span", { title: fmt.dateTime(ev.published_at) }, fmt.ago(ev.published_at)),
            ev.corroboration > 1 ? h("span", { class: "badge badge-outline", title: t("Fonti diverse che hanno riportato la stessa notizia") }, t("{0} fonti", ev.corroboration)) : null,
            ev.targeted ? h("span", { class: "badge badge-outline", title: t("Trovata dalla ricerca mirata per questo mercato") }, icon("search"), t("ricerca mirata")) : null,
          ),
          ev.matched_terms?.length ? h("div", { class: "terms", "aria-label": t("Termini chiave trovati") },
            ev.matched_terms.map((x) => h("span", { class: "term" }, x))) : null,
        ),
        h("div", { class: "ev-stats" },
          h("span", {}, t("Pertinenza "), h("b", { class: "mono" }, fmt.pct(ev.match_score ?? ev.similarity))),
          ev.relevance != null ? h("span", {}, t("Rilevanza "), h("b", { class: "mono" }, fmt.pct(ev.relevance))) : null,
          impactBadge(ev.impact),
        ),
      )))
      : h("p", { class: "secondary" }, t("Nessuna notizia recente riguarda questo mercato.")),
  );

  return h("div", {},
    h("a", { class: "back", href: "#/mercati" }, icon("back"), t("Tutti i mercati")),
    h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "eyebrow" }, t("Mercato Polymarket")),
      h("h1", { style: { marginTop: "4px" } }, market.question),
      h("div", { class: "meta-row" },
        marketStateBadge(market),
        h("span", {}, t("Prezzo SÌ "), h("b", { class: "mono", style: { color: "var(--text-primary)" } }, fmt.cents(market.yes_price))),
        h("span", {}, t("Scade {0}", fmt.date(market.end_date))),
        h("span", {}, t("Volume {0}", fmt.usd(market.volume))),
        h("span", {}, t("Liquidità {0}", fmt.usd(market.liquidity))),
        market.url ? externalLink(market.url, t("Apri su Polymarket"), icon("external")) : null,
      ),
    ),
    h("div", { class: "grid-2", style: { marginBottom: "16px" } }, forecastCard, historyCard),
    h("div", { class: "stack" },
      latest ? economicsCard(ctx, market) : null,
      latest ? explainCard(latest, market, market.evidence, status) : null,
      evidenceCard,
      market.description ? h("details", { class: "card rules" }, h("summary", {}, t("Regole di risoluzione")), h("p", { class: "rules-text" }, market.description)) : null,
    ),
  );
}

// ---------- Calibrazione ----------
async function viewCalibration() {
  const cal = await api("/predictions/calibration");
  const head = pageHead(t("Calibrazione"), t("Quanto sono state accurate le previsioni sui mercati già risolti, rispetto al prezzo di mercato."));
  if (!cal.resolved_markets) {
    return h("div", {}, head, emptyState(t("Ancora nessun mercato risolto"),
      t("Quando un mercato con almeno una previsione si chiude, qui trovi il confronto tra Jev, blended e prezzo. Servono decine di mercati risolti prima di fidarsi dei segnali.")));
  }
  const better = cal.brier_blended != null && cal.brier_market != null ? (cal.brier_market - cal.brier_blended) / cal.brier_market : null;
  const small = cal.resolved_markets < 30;
  const g = cal.gain_blended;
  const interval = g && g.lo != null
    ? (g.lo > 0 ? t("Anche nel caso peggiore dell'intervallo al 95% il blended batte il prezzo: il vantaggio non sembra dovuto al caso.")
      : g.hi < 0 ? t("Anche nel caso migliore dell'intervallo al 95% il prezzo fa meglio del blended.")
        : t("L'intervallo al 95% comprende lo zero: con questi mercati non si può ancora dire chi sia più accurato."))
      + t(" (vantaggio in Brier {0}, da {1} a {2})", fmt.num3(g.mean), fmt.num3(g.lo), fmt.num3(g.hi))
    : null;
  const sc = cal.signal_clv;
  return h("div", {}, head,
    h("div", { class: "grid-2" },
      h("section", { class: "card", "aria-labelledby": "h-verdict" },
        h("h2", { id: "h-verdict", class: "eyebrow" }, t("Blended rispetto al mercato")),
        h("div", { class: `hero ${better > 0 ? "pos" : better < 0 ? "neg" : ""}`, style: { marginTop: "8px" } }, better == null ? "–" : `${better > 0 ? "−" : "+"}${fmt.pct(Math.abs(better))}`),
        h("p", { class: "secondary", style: { marginTop: "6px" } },
          better == null ? t("Dati insufficienti.")
            : better > 0 ? t("di errore rispetto al prezzo di mercato: le previsioni blended sono state più accurate.")
              : t("di errore rispetto al prezzo di mercato: il prezzo è stato più accurato delle previsioni.")),
        h("p", { class: "muted small", style: { marginTop: "10px" } }, t("Su {0} mercati risolti, usando l'ultima previsione fatta per ciascuno.", fmt.int(cal.resolved_markets))),
        interval ? h("p", { class: "secondary small", style: { marginTop: "6px" } }, interval) : null,
        small ? h("p", { class: "note", style: { marginTop: "12px" } }, h("span", { class: "badge badge-warning" }, icon("alert"), t("Campione piccolo")), t(" Con meno di 30 mercati il confronto dipende molto dal caso.")) : null,
      ),
      h("section", { class: "card", "aria-labelledby": "h-brier" },
        h("div", { class: "card-head" }, h("h2", { id: "h-brier" }, t("Brier score")), h("span", { class: "muted small" }, t("più basso è meglio"))),
        brierBars([
          { key: "market", label: t("Prezzo mercato"), value: cal.brier_market, first: true },
          { key: "jev", label: t("Stima Jev"), value: cal.brier_model },
          { key: "blended", label: t("Blended"), value: cal.brier_blended },
        ]),
        h("p", { class: "muted small", style: { marginTop: "16px" } }, t("Media di (probabilità − esito)², con esito 1 se il mercato si è risolto SÌ e 0 se NO. Chi dice sempre 50% ottiene 0,25.")),
      ),
    ),
    h("section", { class: "card", "aria-labelledby": "h-clv", style: { marginTop: "16px" } },
      h("div", { class: "card-head" }, h("h2", { id: "h-clv" }, t("Il prezzo è andato verso i segnali?")),
        infoTip(t("Closing line value: la differenza tra il prezzo di chiusura (l'ultimo prima che il mercato smettesse di scambiare) e il prezzo al momento del segnale, nella direzione consigliata. Chi compra stabilmente sotto la chiusura ha un vantaggio reale, e lo si vede molto prima che i mercati risolti siano abbastanza per il Brier."))),
      sc?.n ? h("div", { class: "kpis" },
        statTile(t("Movimento medio"), fmt.pts(sc.avg), sc.interval?.lo != null ? t("95%: da {0} a {1}", fmt.pts(sc.interval.lo), fmt.pts(sc.interval.hi)) : t("intervallo non disponibile")),
        statTile(t("Segnali a favore"), fmt.pct(sc.share_positive), t("su {0} in mercati chiusi", fmt.count(sc.n, t("segnale"), t("segnali")))))
        : h("p", { class: "muted small" }, t("Nessun segnale su mercati già chiusi.")),
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
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, h("span", { class: "spinner", "aria-hidden": "true" }), t("Cambia password"));
  const form = h("form", { class: "form", novalidate: true },
    field("pw-current", t("Password attuale"), "current-password"),
    field("pw-new", t("Nuova password"), "new-password", t("Almeno 12 caratteri, senza lo username. Una frase lunga è più sicura e più facile da ricordare.")),
    field("pw-confirm", t("Ripeti la nuova password"), "new-password"),
    submit,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const cur = fields["pw-current"].input.value, next = fields["pw-new"].input.value, confirm = fields["pw-confirm"].input.value;
    Object.keys(fields).forEach((id) => setError(id, ""));
    let first = null;
    const fail = (id, text) => { setError(id, text); first = first || id; };
    if (!cur) fail("pw-current", t("Inserisci la password attuale."));
    if (next.length < 12) fail("pw-new", t("La nuova password deve avere almeno 12 caratteri."));
    if (next && confirm !== next) fail("pw-confirm", t("Le due password non coincidono."));
    if (first) return fields[first].input.focus();
    setBusy(submit, true);
    try {
      await api("/auth/password", { method: "POST", body: { current_password: cur, new_password: next } });
      form.reset();
      toast(t("Password aggiornata. Le sessioni aperte su altri dispositivi sono state chiuse."));
    } catch (err) {
      if (err.status === 400) { setError("pw-current", err.message); fields["pw-current"].input.focus(); }
      else if (err.status === 422) { setError("pw-new", err.message); fields["pw-new"].input.focus(); }
      else toast(err.message, { error: true });
    } finally {
      setBusy(submit, false);
    }
  });

  return h("div", {},
    pageHead(t("Account"), t("Il tuo profilo e la password di accesso.")),
    h("div", { class: "grid-2" },
      h("section", { class: "card", "aria-labelledby": "h-profile", style: { alignSelf: "start" } },
        h("h2", { id: "h-profile" }, t("Profilo")),
        h("dl", { class: "dl" },
          h("dt", {}, t("Username")), h("dd", { class: "mono" }, me.username),
          h("dt", {}, t("Ruolo")), h("dd", {}, isAdmin()
            ? h("span", { class: "badge badge-outline" }, t("Amministratore"))
            : h("span", { class: "badge badge-outline" }, t("Sola lettura"))),
          h("dt", {}, t("Permessi")), h("dd", { class: "secondary" }, isAdmin()
            ? t("Consulta i dati, aggiorna notizie e mercati, chiede previsioni a Jev.")
            : t("Consulta notizie, mercati, previsioni e calibrazione.")),
        ),
        h("button", { class: "btn btn-ghost", type: "button", style: { marginTop: "16px" }, on: { click: logout } }, icon("logout"), t("Esci")),
      ),
      h("section", { class: "card", "aria-labelledby": "h-password" },
        h("h2", { id: "h-password", style: { marginBottom: "12px" } }, t("Cambia password")),
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
