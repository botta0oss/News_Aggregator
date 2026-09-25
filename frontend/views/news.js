// Notizie: full-text search, filters and the ranked list.
import { t } from "../i18n.js";
import {
  h, clear, api, fmt, toast, debounce, icon, externalLink, meter, emptyState, infoTip, highlight, selectField,
  CATEGORY_LABELS, REGION_LABELS, GLOSSARY,
} from "../ui.js";

const DEFAULTS = {
  q: "", scope: "all", sort: "", category: "", region: "", source_id: "", since_hours: "",
  relevant: false, hide_opinion: false,
  w_authority: 0.35, w_tech: 0.25, w_urgency: 0.25, w_clickbait: 0.4, max_clickbait: 1, min_authority: 0,
};
const state = { ...DEFAULTS };
const PERIODS = [["", t("Qualsiasi data")], ["24", t("Ultime 24 ore")], ["72", t("Ultimi 3 giorni")], ["168", t("Ultimi 7 giorni")], ["720", t("Ultimi 30 giorni")]];

function syncHash() {
  // Keep the search in the address so it survives a reload and can be shared
  const target = state.q ? `#/notizie?q=${encodeURIComponent(state.q)}` : "#/notizie";
  if (window.location.hash !== target) window.history.replaceState(null, "", target);
}

export async function viewNews(ctx, query = {}) {
  if (query.q != null && query.q !== state.q) state.q = query.q;
  const [categories, sources] = await Promise.all([
    api("/categories").catch(() => []),
    api("/sources").catch(() => []),
  ]);

  const list = h("div", { class: "news" });
  const summary = h("p", { class: "muted small", role: "status", "aria-live": "polite" });
  const more = h("div", { class: "more" });
  let offset = 0;
  let loadToken = 0;

  const params = () => ({
    q: state.q || null, scope: state.q ? state.scope : null, sort: state.sort || null,
    category: state.category || null, region: state.region || null, source_id: state.source_id || null,
    since_hours: state.since_hours || null,
    min_market_relevance: state.relevant ? 0.5 : null, hide_opinion: state.hide_opinion || null,
    limit: 30, offset,
    w_authority: state.w_authority, w_tech: state.w_tech, w_urgency: state.w_urgency, w_clickbait: state.w_clickbait,
    max_clickbait: state.max_clickbait < 1 ? state.max_clickbait : null,
    min_authority: state.min_authority > 0 ? state.min_authority : null,
  });

  const load = async (reset) => {
    const token = ++loadToken;
    if (reset) offset = 0;
    const data = await api("/articles", { params: params() });
    if (token !== loadToken) return; // a newer search is on its way
    if (reset) clear(list);
    list.append(...data.articles.map((a) => articleCard(a)));
    offset += data.articles.length;
    summary.textContent = !data.total ? ""
      : state.q ? t("{0} per «{1}»{2}", fmt.count(data.total, t("risultato"), t("risultati")), state.q, offset < data.total ? t(", mostrati {0}", fmt.int(offset)) : "")
        : t("{0} di {1}", fmt.int(offset), fmt.count(data.total, t("notizia"), t("notizie")));
    if (!data.total) {
      list.replaceChildren(state.q || filtersActive()
        ? emptyState(t("Nessuna notizia trovata"), t("Prova con meno parole, un periodo più lungo o meno filtri."),
          h("button", { class: "btn btn-ghost", type: "button", on: { click: resetFilters } }, t("Azzera ricerca e filtri")))
        : emptyState(t("Nessuna notizia"), ctx.isAdmin() ? t("Premi «Aggiorna notizie» per scaricarle dalle fonti attive.") : t("Le notizie compaiono dopo il prossimo aggiornamento automatico.")));
    }
    more.replaceChildren(offset < data.total ? h("button", { class: "btn btn-ghost", type: "button", on: { click: () => load(false) } }, t("Carica altre")) : "");
  };
  const reload = () => load(true).catch((e) => toast(e.message, { error: true }));
  const reloadSoon = debounce(reload, 300);

  const filtersActive = () => ["category", "region", "source_id", "since_hours", "relevant", "hide_opinion"].some((k) => state[k]);
  const resetFilters = () => {
    Object.assign(state, DEFAULTS);
    syncHash();
    ctx.rerender();
  };

  // Search box
  const input = h("input", {
    id: "n-q", class: "search search-lg", type: "search", value: state.q, autocomplete: "off", maxlength: "200",
    placeholder: t("Cerca nelle notizie: parole, \"frase esatta\", -escludi"),
    "aria-label": t("Cerca nelle notizie"), "aria-describedby": "n-q-help",
  });
  input.addEventListener("input", () => {
    state.q = input.value.trim();
    sortSelect.querySelector('option[value="relevance"]').disabled = !state.q;
    syncHash();
    reloadSoon();
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Escape" && input.value) { input.value = ""; input.dispatchEvent(new Event("input")); } });

  const sortOptions = [["", t("Ordinamento automatico")], ["relevance", t("Più pertinenti")], ["score", t("Punteggio più alto")], ["recent", t("Più recenti")]];
  const sortField = selectField("n-sort", t("Ordina"), sortOptions, state.sort, (v) => { state.sort = v; reload(); });
  const sortSelect = sortField.querySelector("select");
  sortSelect.querySelector('option[value="relevance"]').disabled = !state.q;

  const searchBar = h("div", { class: "searchbar" },
    h("div", { class: "search-wrap search-wrap-lg" }, icon("search"), input),
    selectField("n-scope", t("Cerca in"), [["all", t("Titolo, testo e riassunto")], ["title", t("Solo titolo")]], state.scope, (v) => { state.scope = v; if (state.q) reload(); }),
  );

  const categoryChips = h("div", { class: "chips", role: "group", "aria-label": t("Categoria") });
  const total = categories.reduce((a, c) => a + c.count, 0);
  const paintChips = () => categoryChips.replaceChildren(
    ...[{ name: "", count: total }, ...categories].map((c) => h("button", {
      class: "chip", type: "button", "aria-pressed": String(state.category === c.name),
      on: { click: () => { state.category = c.name; paintChips(); reload(); } },
    }, c.name ? CATEGORY_LABELS[c.name] || c.name : t("Tutte"), h("span", { class: "count" }, fmt.int(c.count)))),
  );
  paintChips();

  const filters = h("div", { class: "filter-row" },
    selectField("n-source", t("Fonte"), [["", t("Tutte le fonti")], ...sources.map((s) => [s.id, s.name])], state.source_id, (v) => { state.source_id = v; reload(); }),
    selectField("n-period", t("Periodo"), PERIODS, state.since_hours, (v) => { state.since_hours = v; reload(); }),
    selectField("n-region", t("Regione"), [["", t("Tutte")], ...Object.entries(REGION_LABELS)], state.region, (v) => { state.region = v; reload(); }),
    sortField,
    h("span", { class: "field" }, ctx.checkField("n-relevant", t("Solo rilevanti per i mercati"), state.relevant, (v) => { state.relevant = v; reload(); }), infoTip(GLOSSARY.relevance)),
    ctx.checkField("n-opinion", t("Escludi opinioni"), state.hide_opinion, (v) => { state.hide_opinion = v; reload(); }),
  );

  const weight = (key, label) => ctx.rangeField(`w-${key}`, label, { min: 0, max: 1, step: 0.05, value: state[key], format: (v) => fmt.dec(v, 2) },
    (v) => { state[key] = v; reloadSoon(); });
  const tuning = h("details", { class: "tuning" },
    h("summary", {}, t("Personalizza il punteggio")),
    h("div", { class: "tuning-grid" },
      weight("w_authority", t("Autorevolezza")), weight("w_tech", t("Profondità")), weight("w_urgency", t("Urgenza")), weight("w_clickbait", t("Penalità clickbait")),
      weight("max_clickbait", t("Clickbait massimo")), weight("min_authority", t("Autorevolezza minima")),
    ),
  );

  await load(true);
  requestAnimationFrame(() => { if (state.q) input.focus(); });
  return h("div", {},
    ctx.pageHead(t("Notizie"), t("Riassunte e classificate da Jev. Le notizie quasi identiche di più testate sono raggruppate.")),
    h("div", { class: "filters news-filters" },
      searchBar,
      h("p", { id: "n-q-help", class: "field-hint" }, t("Tutte le parole devono comparire. Usa le virgolette per una frase esatta, «-parola» per escludere, «or» per alternative.")),
      filters, categoryChips, tuning,
    ),
    h("div", { class: "stack" }, summary, list, more),
  );
}

function articleCard(a) {
  const scoreRow = (label, v, hint) => h("div", { class: "score-row", title: hint }, h("span", {}, label), meter(v, label));
  const categoryLabel = CATEGORY_LABELS[a.category] || a.category;
  const relevant = a.market_relevance != null && a.market_relevance >= 0.5;
  return h("article", { class: "card article" },
    h("div", {},
      externalLink(a.url, h("span", { class: "article-title" }, a.title_highlight ? highlight(a.title_highlight) : a.title)),
      h("div", { class: "article-meta" },
        h("span", {}, a.source_name),
        h("span", { title: fmt.dateTime(a.published_at) }, fmt.ago(a.published_at)),
        a.category ? h("span", {
          class: "badge",
          title: a.classifier === "jev" && a.category_confidence != null ? t("Classificata da Jev, confidenza {0}", fmt.pct(a.category_confidence)) : t("Classificata con l'euristica locale"),
        }, categoryLabel) : null,
        a.region && a.region !== "Global" ? h("span", { class: "badge badge-outline" }, REGION_LABELS[a.region] || a.region) : null,
        relevant ? h("span", { class: "badge badge-accent", title: GLOSSARY.relevance }, icon("up"), t("Rilevante per i mercati")) : null,
        a.is_opinion != null && a.is_opinion >= 0.5 ? h("span", { class: "badge badge-outline" }, t("Opinione")) : null,
        a.cluster_source_count > 1 ? h("span", { class: "badge badge-outline" }, icon("news"), t("{0} fonti", a.cluster_source_count)) : null,
      ),
      a.snippet ? h("p", { class: "article-summary" }, highlight(a.snippet))
        : a.summary ? h("p", { class: "article-summary" }, a.summary) : null,
    ),
    h("div", { class: "scores" },
      h("div", { class: "composite" }, h("span", { class: "fig-label" }, t("Punteggio")), h("b", {}, fmt.pct(a.composite_score))),
      scoreRow(t("Autorevolezza"), a.authority_score, t("Quanto è verificata e ben sostenuta da fonti")),
      scoreRow(t("Profondità"), a.technical_depth_score, t("Livello di analisi tecnica o quantitativa")),
      scoreRow(t("Urgenza"), a.urgency_score, t("Quanto è una notizia in evoluzione")),
      scoreRow(t("Clickbait"), a.clickbait_score, t("Sensazionalismo del titolo (più basso è meglio)")),
      a.market_relevance != null ? scoreRow(t("Per i mercati"), a.market_relevance, GLOSSARY.relevance) : null,
    ),
  );
}
