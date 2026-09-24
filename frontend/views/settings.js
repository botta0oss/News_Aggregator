// Impostazioni: news sources (list, add, test, edit, enable/disable, delete, catalog) and classification.
import {
  h, clear, api, fmt, toast, icon, externalLink, emptyState, selectField, CATEGORY_LABELS,
} from "../ui.js";

const CATEGORY_OPTIONS = [["", "Nessuna (argomenti misti)"], ...Object.entries(CATEGORY_LABELS)];

export async function viewSettings(ctx) {
  const admin = ctx.isAdmin();
  const [sources, catalog] = await Promise.all([api("/sources"), api("/sources/catalog")]);
  const rerender = () => ctx.rerender();

  const formHolder = h("div", {});
  const openForm = (source = null) => {
    formHolder.replaceChildren(sourceForm(ctx, source, () => { formHolder.replaceChildren(); }, rerender));
    formHolder.querySelector("input")?.focus();
    formHolder.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const active = sources.filter((s) => s.active).length;
  const errors = sources.filter((s) => s.active && s.last_status === "error").length;

  const sourcesCard = h("section", { class: "card", "aria-labelledby": "h-sources" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { id: "h-sources" }, "Fonti"),
        h("p", { class: "muted small" }, `${active} attive su ${sources.length}`, errors ? ` · ${errors} con errori` : ""),
      ),
      admin ? h("div", { class: "actions" },
        h("button", { class: "btn btn-primary", type: "button", on: { click: () => openForm() } }, icon("plus"), "Aggiungi fonte"),
      ) : null,
    ),
    sources.length
      ? h("div", { class: "table-wrap" }, h("table", { class: "sources-table" },
        h("thead", {}, h("tr", {},
          h("th", {}, "Fonte"), h("th", {}, "Argomento"), h("th", {}, "Ultimo aggiornamento"),
          h("th", { class: "num" }, "Notizie"), h("th", {}, "Attiva"), admin ? h("th", {}, h("span", { class: "sr-only" }, "Azioni")) : null,
        )),
        h("tbody", {}, sources.map((s) => sourceRow(ctx, s, { admin, onEdit: openForm, onChange: rerender }))),
      ))
      : emptyState("Nessuna fonte", admin ? "Aggiungi una fonte o scegline alcune tra quelle consigliate qui sotto." : "Un amministratore deve aggiungere le fonti."),
  );

  return h("div", {},
    ctx.pageHead("Impostazioni", admin ? "Fonti delle notizie e classificazione." : "Fonti delle notizie e parametri. Solo gli amministratori possono modificarli."),
    h("div", { class: "stack" },
      formHolder,
      sourcesCard,
      admin ? catalogCard(ctx, catalog, rerender) : null,
      h("div", { class: "grid-2" },
        classificationCard(ctx),
        parametersCard(ctx),
      ),
    ),
  );
}

// ---------- Source list ----------

function statusCell(s) {
  if (!s.last_fetched_at) {
    return h("span", { class: "muted small" }, s.active ? "In attesa del primo aggiornamento" : "Mai scaricata");
  }
  if (s.last_status === "error") {
    return h("div", { class: "status-cell" },
      h("span", { class: "badge badge-critical" }, icon("x"), "Errore"),
      h("span", { class: "small error-text", title: s.last_error || "" }, s.last_error || "Errore sconosciuto"),
      h("span", { class: "muted small" }, fmt.ago(s.last_fetched_at)),
    );
  }
  return h("div", { class: "status-cell" },
    h("span", { class: "badge badge-good" }, icon("check"), s.last_new_items ? `${s.last_new_items} nuove` : "Nessuna novità"),
    h("span", { class: "muted small", title: fmt.dateTime(s.last_fetched_at) }, fmt.ago(s.last_fetched_at)),
  );
}

function sourceRow(ctx, s, { admin, onEdit, onChange }) {
  const toggle = h("input", { type: "checkbox", class: "switch", checked: s.active, disabled: !admin, "aria-label": `${s.active ? "Disattiva" : "Attiva"} ${s.name}` });
  toggle.addEventListener("change", async () => {
    toggle.disabled = true;
    try {
      await api(`/sources/${s.id}`, { method: "PATCH", body: { active: toggle.checked } });
      toast(toggle.checked ? `${s.name} attivata` : `${s.name} disattivata: le sue notizie restano nell'archivio`);
      onChange();
    } catch (e) {
      toggle.checked = !toggle.checked;
      toggle.disabled = false;
      toast(e.message, { error: true });
    }
  });

  const actions = h("td", { class: "row-actions" });
  const paintActions = () => actions.replaceChildren(
    h("button", { class: "btn btn-ghost btn-sm btn-square", type: "button", title: "Scarica subito le notizie di questa fonte", disabled: !s.active,
      on: { click: (e) => fetchNow(e.currentTarget, s, onChange) } }, icon("refresh"), h("span", { class: "sr-only" }, `Aggiorna ${s.name}`)),
    h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: () => onEdit(s) } }, "Modifica"),
    h("button", { class: "btn btn-ghost btn-sm btn-danger", type: "button", on: { click: confirmDelete } }, "Elimina"),
  );
  function confirmDelete() {
    const confirmBtn = h("button", { class: "btn btn-sm btn-danger-solid", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Elimina");
    confirmBtn.addEventListener("click", async () => {
      confirmBtn.disabled = true;
      confirmBtn.classList.add("loading");
      try {
        await api(`/sources/${s.id}`, { method: "DELETE", params: { delete_articles: s.article_count > 0 || null } });
        toast(`${s.name} eliminata${s.article_count ? ` con ${fmt.count(s.article_count, "notizia", "notizie")}` : ""}`);
        onChange();
      } catch (e) {
        toast(e.message, { error: true });
        paintActions();
      }
    });
    actions.replaceChildren(h("div", { class: "confirm", role: "alert" },
      h("span", { class: "small" }, s.article_count
        ? `Eliminare la fonte e ${s.article_count === 1 ? "la sua notizia" : `le sue ${fmt.int(s.article_count)} notizie`}? Per tenerle, disattivala.`
        : "Eliminare la fonte?"),
      h("div", { class: "confirm-actions" },
        confirmBtn,
        h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: paintActions } }, "Annulla"),
      ),
    ));
    confirmBtn.focus();
  }
  paintActions();

  return h("tr", { class: s.active ? "" : "inactive" },
    h("td", { class: "source-cell" },
      h("div", { class: "source-name" }, s.name),
      externalLink(s.url, h("span", { class: "source-url" }, s.url.replace(/^https?:\/\//, ""))),
    ),
    h("td", {}, s.category_hint ? h("span", { class: "badge" }, CATEGORY_LABELS[s.category_hint] || s.category_hint) : h("span", { class: "muted small" }, "Misti")),
    h("td", {}, statusCell(s)),
    h("td", { class: "num" }, fmt.int(s.article_count)),
    h("td", {}, toggle),
    admin ? actions : null,
  );
}

async function fetchNow(btn, s, onChange) {
  btn.disabled = true;
  try {
    await api(`/sources/${s.id}/fetch`, { method: "POST" });
    toast(`Aggiornamento di ${s.name} avviato`);
    setTimeout(onChange, 5000);
  } catch (e) {
    toast(e.message, { error: true });
    btn.disabled = false;
  }
}

// ---------- Add / edit form ----------

function sourceForm(ctx, source, onClose, onSaved) {
  const editing = Boolean(source);
  const field = (id, label, input, hint) => h("div", { class: "form-field" },
    h("label", { for: id }, label), input, h("p", { class: "field-hint", id: `${id}-msg` }, hint || ""));
  const url = h("input", { id: "s-url", class: "input", type: "url", required: true, maxlength: "2000", value: source?.url || "",
    placeholder: "https://esempio.com/feed.xml", autocomplete: "off", "aria-describedby": "s-url-msg" });
  const name = h("input", { id: "s-name", class: "input", required: true, maxlength: "80", value: source?.name || "", autocomplete: "off", "aria-describedby": "s-name-msg" });
  const category = selectField("s-category", "Argomento prevalente", CATEGORY_OPTIONS, source?.category_hint || "", () => {});
  const active = ctx.checkField("s-active", "Attiva (scarica le notizie a ogni aggiornamento)", source ? source.active : true, () => {});
  const result = h("div", { class: "feed-test", "aria-live": "polite" });
  const setMsg = (id, text) => {
    const msg = document.getElementById(`${id}-msg`);
    const input = document.getElementById(id);
    msg.textContent = text || (id === "s-url" ? "Indirizzo del feed RSS o Atom, non della pagina web." : "");
    msg.classList.toggle("field-error", Boolean(text));
    input.setAttribute("aria-invalid", text ? "true" : "false");
  };

  const testBtn = h("button", { class: "btn btn-ghost", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("eye"), "Prova il feed");
  testBtn.addEventListener("click", async () => {
    if (!url.value.trim()) { setMsg("s-url", "Inserisci l'indirizzo del feed."); url.focus(); return; }
    setMsg("s-url", "");
    testBtn.disabled = true;
    testBtn.classList.add("loading");
    result.replaceChildren(h("p", { class: "muted small" }, "Scarico il feed…"));
    try {
      const r = await api("/sources/test", { method: "POST", body: { url: url.value.trim() } });
      if (r.ok) {
        if (!name.value.trim() && r.title) name.value = r.title.slice(0, 80);
        result.replaceChildren(h("div", { class: "test-ok" },
          h("p", {}, h("span", { class: "badge badge-good" }, icon("check"), "Feed valido"), " ",
            r.title ? h("b", {}, r.title) : null, ` · ${fmt.count(r.item_count, "notizia", "notizie")}`),
          h("ul", { class: "samples" }, r.samples.map((t) => h("li", {}, t))),
        ));
      } else {
        result.replaceChildren(h("p", { class: "test-ko" }, h("span", { class: "badge badge-critical" }, icon("x"), "Non funziona"), " ", r.error));
      }
    } catch (e) {
      result.replaceChildren(h("p", { class: "test-ko" }, e.message));
    } finally {
      testBtn.disabled = false;
      testBtn.classList.remove("loading");
    }
  });

  const save = h("button", { class: "btn btn-primary", type: "submit" }, h("span", { class: "spinner", "aria-hidden": "true" }), editing ? "Salva modifiche" : "Aggiungi fonte");
  const form = h("form", { class: "form", novalidate: true },
    field("s-url", "Indirizzo del feed", h("div", { class: "input-row" }, url, testBtn), "Indirizzo del feed RSS o Atom, non della pagina web."),
    result,
    field("s-name", "Nome", name, ""),
    h("div", { class: "form-inline" }, category, active),
    h("div", { class: "form-actions" }, save, h("button", { class: "btn btn-ghost", type: "button", on: { click: onClose } }, "Annulla")),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    setMsg("s-url", ""); setMsg("s-name", "");
    if (!url.value.trim()) { setMsg("s-url", "Inserisci l'indirizzo del feed."); return url.focus(); }
    if (name.value.trim().length < 2) { setMsg("s-name", "Dai un nome alla fonte (almeno 2 caratteri)."); return name.focus(); }
    save.disabled = true;
    save.classList.add("loading");
    const body = {
      name: name.value.trim(), url: url.value.trim(),
      category_hint: form.querySelector("#s-category").value || null,
      active: form.querySelector("#s-active").checked,
    };
    try {
      if (editing) await api(`/sources/${source.id}`, { method: "PATCH", body });
      else await api("/sources", { method: "POST", body });
      toast(editing ? `${body.name} aggiornata` : `${body.name} aggiunta. Le notizie arrivano al prossimo aggiornamento.`);
      onSaved();
    } catch (err) {
      if (err.status === 409 || /indirizzo|http/i.test(err.message)) { setMsg("s-url", err.message); url.focus(); }
      else if (/nome/i.test(err.message)) { setMsg("s-name", err.message); name.focus(); }
      else toast(err.message, { error: true });
      save.disabled = false;
      save.classList.remove("loading");
    }
  });

  return h("section", { class: "card form-card", "aria-labelledby": "h-source-form" },
    h("div", { class: "card-head" }, h("h2", { id: "h-source-form" }, editing ? `Modifica ${source.name}` : "Nuova fonte")),
    form,
  );
}

// ---------- Catalog ----------

function catalogCard(ctx, catalog, onAdded) {
  const available = catalog.filter((c) => !c.added);
  const selected = new Set();
  const addBtn = h("button", { class: "btn btn-primary", type: "button", disabled: true }, h("span", { class: "spinner", "aria-hidden": "true" }), "Aggiungi selezionate");
  const paintBtn = () => {
    addBtn.disabled = selected.size === 0;
    addBtn.lastChild.textContent = selected.size ? `Aggiungi selezionate (${selected.size})` : "Aggiungi selezionate";
  };
  addBtn.addEventListener("click", async () => {
    addBtn.disabled = true;
    addBtn.classList.add("loading");
    try {
      const added = await api("/sources/catalog", { method: "POST", body: { urls: [...selected] } });
      toast(`${added.length === 1 ? "1 fonte aggiunta" : `${added.length} fonti aggiunte`}. Le notizie arrivano al prossimo aggiornamento.`);
      onAdded();
    } catch (e) {
      toast(e.message, { error: true });
      addBtn.classList.remove("loading");
      paintBtn();
    }
  });

  const boxes = new Map(); // url -> checkbox
  const setChecked = (items, on) => {
    for (const item of items) {
      const box = boxes.get(item.url);
      if (!box || box.disabled) continue;
      box.checked = on;
      on ? selected.add(item.url) : selected.delete(item.url);
    }
    paintBtn();
  };
  const toggleAll = (items) => {
    const free = items.filter((i) => !i.added);
    setChecked(free, !free.every((i) => selected.has(i.url)));
  };

  const groups = new Map();
  for (const item of catalog) {
    const key = item.category_hint || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }

  return h("section", { class: "card", "aria-labelledby": "h-catalog" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { id: "h-catalog" }, "Fonti consigliate"),
        h("p", { class: "muted small" }, available.length
          ? `${available.length} fonti selezionate per i temi più scambiati su Polymarket, non ancora aggiunte.`
          : "Hai già aggiunto tutte le fonti consigliate."),
      ),
      available.length ? h("div", { class: "actions" },
        h("button", { class: "btn btn-ghost", type: "button", on: { click: () => toggleAll(catalog) } }, "Seleziona tutte"),
        addBtn) : null,
    ),
    h("div", { class: "catalog" },
      [...groups.entries()].map(([cat, items]) => h("fieldset", { class: "catalog-group" },
        h("legend", {}, cat ? CATEGORY_LABELS[cat] || cat : "Generaliste",
          items.some((i) => !i.added)
            ? h("button", { class: "btn-link small", type: "button", on: { click: () => toggleAll(items) } }, "tutte")
            : null),
        items.map((item, i) => {
          const id = `cat-${cat || "gen"}-${i}`.replace(/\W/g, "-");
          const box = h("input", { type: "checkbox", id, disabled: item.added, checked: item.added });
          boxes.set(item.url, box);
          box.addEventListener("change", () => { box.checked ? selected.add(item.url) : selected.delete(item.url); paintBtn(); });
          return h("label", { class: `catalog-item${item.added ? " added" : ""}`, for: id },
            box,
            h("span", {},
              h("span", { class: "catalog-name" }, item.name, item.added ? h("span", { class: "muted small" }, " · già aggiunta") : null),
              item.description ? h("span", { class: "muted small catalog-desc" }, item.description) : null,
            ),
          );
        }),
      )),
    ),
  );
}

// ---------- Classification & parameters ----------

function classificationCard(ctx) {
  const st = ctx.status || {};
  const btn = h("button", { class: "btn btn-ghost", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"), "Riclassifica le notizie");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.classList.add("loading");
    try {
      await api("/ingest/reclassify", { method: "POST", params: { limit: 500 } });
      toast("Riclassificazione avviata: fino a 500 notizie, dalle più recenti.");
    } catch (e) {
      toast(e.message, { error: true });
    } finally {
      btn.disabled = false;
      btn.classList.remove("loading");
    }
  });
  return h("section", { class: "card", "aria-labelledby": "h-classif" },
    h("h2", { id: "h-classif" }, "Classificazione"),
    h("p", { class: "secondary", style: { margin: "8px 0" } }, st.jev_enabled
      ? "Le notizie sono classificate da Jev: categoria, regione, opinione o notizia, rilevanza per i mercati, autorevolezza e clickbait."
      : "Senza chiave TypeSafe le notizie sono classificate con parole chiave. Aggiungi TYPESAFE_API_KEY per usare Jev."),
    h("p", { class: "muted small" }, st.jev_enabled
      ? "La riclassificazione passa a Jev le notizie classificate prima con le parole chiave. Ogni notizia è una chiamata a pagamento."
      : "La riclassificazione applica le regole attuali alle notizie salvate prima dell'ultimo aggiornamento."),
    ctx.isAdmin() ? h("div", { style: { marginTop: "12px" } }, btn) : null,
  );
}

function parametersCard(ctx) {
  const st = ctx.status || {};
  const row = (label, value, env) => [h("dt", {}, label), h("dd", {}, h("b", { class: "mono" }, value), h("code", { class: "env" }, env))];
  return h("section", { class: "card", "aria-labelledby": "h-params" },
    h("h2", { id: "h-params" }, "Parametri di previsione"),
    h("p", { class: "muted small", style: { margin: "6px 0 10px" } }, "Si cambiano nel file .env del server e valgono al riavvio. ", h("a", { href: "#/metodo" }, "Cosa significano")),
    h("dl", { class: "dl params" },
      row("Pertinenza minima notizia–mercato", fmt.pct(st.market_match_threshold), "MARKET_MATCH_THRESHOLD"),
      row("Finestra delle notizie", `${st.market_news_window_hours ?? "–"} ore`, "MARKET_NEWS_WINDOW_HOURS"),
      row("Peso massimo di Jev", fmt.pct(st.model_weight_max), "MODEL_WEIGHT_MAX"),
      row("Edge minimo", st.min_edge != null ? `${Math.round(st.min_edge * 100)} pt` : "–", "MIN_EDGE"),
      row("Evidenze minime", fmt.pct(st.min_evidence), "MIN_EVIDENCE"),
      row("Frazione di Kelly", st.kelly_fraction != null ? String(st.kelly_fraction).replace(".", ",") : "–", "KELLY_FRACTION"),
      row("Previsioni automatiche", st.prediction_auto ? "Sì" : "No", "PREDICTION_AUTO"),
    ),
  );
}
