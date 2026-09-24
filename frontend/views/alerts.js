// Allerte: fresh news evaluated right away, Telegram notifications and the price afterwards.
import { h, api, fmt, toast, icon, statTile, emptyState, infoTip, externalLink, CATEGORY_LABELS } from "../ui.js";
import { verdictBadge } from "../economics.js";

const CHECKPOINTS = [["15m", "15 min"], ["1h", "1 ora"], ["6h", "6 ore"], ["24h", "24 ore"]];
const filters = { kind: "opportunities" };

const moveCls = (m) => (m == null ? "muted" : m > 0 ? "pos" : m < 0 ? "neg" : "");
const sideLabel = (side) => (side === "YES" ? "SÌ" : side === "NO" ? "NO" : "–");

export async function viewAlerts(ctx) {
  const admin = ctx.isAdmin();
  const [settings, summary, categories] = await Promise.all([
    api("/alerts/settings"), api("/alerts/summary"), api("/sources/categories").catch(() => []),
  ]);

  const list = h("div", { class: "alerts" });
  const loadList = async () => {
    list.replaceChildren(h("p", { class: "muted small" }, "Caricamento…"));
    try {
      const items = await api("/alerts", { params: { kind: filters.kind, limit: 100 } });
      list.replaceChildren(...(items.length ? items.map(alertCard) : [emptyList(settings)]));
    } catch (e) {
      list.replaceChildren(emptyState("Impossibile caricare le allerte", e.message));
    }
  };

  const kindSwitch = h("div", { class: "segmented", role: "group", "aria-label": "Quali allerte" },
    [["opportunities", "Solo opportunità"], ["all", "Tutte le valutazioni"]].map(([value, label]) => {
      const b = h("button", { type: "button", class: "seg", "aria-pressed": String(filters.kind === value) }, label);
      b.addEventListener("click", () => {
        filters.kind = value;
        b.parentElement.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        loadList();
      });
      return b;
    }));

  let runBtn = null;
  if (admin) {
    runBtn = h("button", { class: "btn btn-ghost", type: "button", title: "Controlla subito le notizie collegate dall'ultimo controllo" },
      h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"), "Controlla ora");
    runBtn.addEventListener("click", async () => {
      ctx.setBusy(runBtn, true);
      try {
        const r = await api("/alerts/run", { method: "POST" });
        toast(r.evaluated
          ? `${fmt.count(r.evaluated, "mercato valutato", "mercati valutati")}, ${fmt.count(r.opportunities, "opportunità", "opportunità")}.`
          : r.triggers ? "Notizie trovate, ma i mercati sono in pausa o il limite giornaliero è raggiunto." : "Nessuna notizia nuova da valutare.");
        ctx.rerender();
      } catch (e) {
        toast(e.message, { error: true });
        ctx.setBusy(runBtn, false);
      }
    });
  }

  await loadList();
  return h("div", {},
    ctx.pageHead("Allerte",
      "Quando esce una notizia importante per un mercato, Jev lo valuta subito. Se conviene arriva una notifica su Telegram; poi si misura se il prezzo si è mosso nella direzione prevista.",
      runBtn),
    setupNotes(settings),
    resultsCard(summary),
    h("div", { class: "section-head" }, h("h2", {}, "Ultime allerte"), kindSwitch),
    list,
    settingsCard(ctx, settings, categories),
  );
}

function setupNotes(s) {
  const notes = [];
  if (!s.jev_enabled) notes.push("Serve TYPESAFE_API_KEY: le allerte usano Jev per valutare il mercato.");
  if (!s.enabled) notes.push("Le allerte sono disattivate (vedi le impostazioni in fondo).");
  if (s.enabled && !s.telegram_configured) notes.push("Telegram non è configurato: le allerte compaiono qui, ma non arrivano notifiche. Imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nel file .env.");
  const cadence = s.scan_minutes > 0 && s.scan_minutes < s.ingest_minutes
    ? `Le fonti vengono controllate ogni ${s.scan_minutes} minuti.`
    : `Le fonti vengono controllate ogni ${s.ingest_minutes} minuti, con la raccolta completa.`;
  return h("div", { class: "notice", role: "note" },
    icon(notes.length ? "alert" : "check"),
    h("div", {}, ...notes.map((n) => h("p", {}, n)), h("p", { class: "muted small" }, cadence)),
  );
}

function resultsCard(s) {
  const oneHour = s.moves["1h"];
  const rows = CHECKPOINTS.map(([key, label]) => {
    const m = s.moves[key];
    return h("tr", {},
      h("th", { scope: "row" }, `Dopo ${label}`),
      h("td", { class: "num" }, fmt.int(m.count)),
      h("td", { class: `num ${moveCls(m.avg_move)}` }, m.avg_move == null ? "–" : fmt.pts(m.avg_move)),
      h("td", { class: "num" }, m.share_favorable == null ? "–" : fmt.pct(m.share_favorable)),
    );
  });
  return h("section", { class: "card", "aria-labelledby": "h-alert-results", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" },
      h("h2", { id: "h-alert-results" }, `Risultati degli ultimi ${s.days} giorni`),
      infoTip("Movimento a favore: di quanti punti il prezzo si è spostato nella direzione consigliata dopo l'allerta. Positivo vuol dire che l'allerta è arrivata prima del mercato. Servono decine di allerte perché i numeri dicano qualcosa."),
    ),
    h("div", { class: "kpis" },
      statTile("Opportunità", fmt.int(s.opportunities), `su ${fmt.count(s.evaluated, "valutazione", "valutazioni")}`),
      statTile("Notificate", fmt.int(s.notified), "su Telegram"),
      statTile("A favore dopo 1 ora", oneHour.share_favorable == null ? "–" : fmt.pct(oneHour.share_favorable),
        oneHour.avg_move == null ? "nessun dato ancora" : `media ${fmt.pts(oneHour.avg_move)}`),
      statTile("Chiamate Jev (24 ore)", `${fmt.int(s.calls_last_24h)} / ${fmt.int(s.daily_budget)}`, "limite giornaliero delle allerte"),
    ),
    h("div", { class: "table-wrap" },
      h("table", { class: "compact-table" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Quando"), h("th", { scope: "col", class: "num" }, "Allerte"),
          h("th", { scope: "col", class: "num" }, "Media a favore"), h("th", { scope: "col", class: "num" }, "A favore"))),
        h("tbody", {}, rows),
      )),
    s.resolved ? h("p", { class: "muted small", style: { marginTop: "8px" } },
      `Mercati già risolti: ${fmt.int(s.won)} su ${fmt.int(s.resolved)} nella direzione dell'allerta.`) : null,
  );
}

function emptyList(s) {
  if (filters.kind === "opportunities") {
    return emptyState("Nessuna opportunità ancora",
      "Le allerte partono quando una notizia fresca e pertinente viene collegata a un mercato e Jev dice che conviene scommettere. Guarda «Tutte le valutazioni» per vedere anche i casi in cui non conveniva.");
  }
  return emptyState("Nessuna valutazione ancora", s.enabled ? "Appena arriva una notizia importante per un mercato, compare qui." : "Le allerte sono disattivate.");
}

function notifyState(a) {
  if (a.notified_at) return h("span", { class: "muted small", title: fmt.dateTime(a.notified_at) }, icon("check"), " Notificata");
  if (a.notify_error) return h("span", { class: "small neg", title: a.notify_error }, icon("alert"), " Notifica non riuscita");
  return a.opportunity ? h("span", { class: "muted small" }, "Non notificata") : null;
}

function alertCard(a) {
  const moves = CHECKPOINTS.map(([key, label]) => {
    const price = a.followups[key];
    const move = a.moves[key];
    return h("div", { class: "move" },
      h("span", { class: "muted small" }, label),
      h("b", { class: `mono ${moveCls(move)}` }, move == null ? "…" : fmt.pts(move)),
      price == null ? null : h("span", { class: "muted small mono" }, fmt.cents(price)),
    );
  });
  const href = `#/mercati/${encodeURIComponent(a.market.id)}`;
  return h("article", { class: `card alert-card${a.opportunity ? "" : " alert-muted"}` },
    h("div", { class: "alert-top" },
      a.verdict ? verdictBadge(a.verdict) : null,
      a.side ? h("span", { class: "badge badge-outline" }, `Compra ${sideLabel(a.side)}`) : null,
      h("span", { class: "muted small", title: fmt.dateTime(a.created_at) }, fmt.ago(a.created_at)),
      notifyState(a),
    ),
    h("h3", { class: "alert-q" }, h("a", { href }, a.market.question)),
    a.news ? h("p", { class: "small" }, icon("news"), " ",
      externalLink(a.news.url, a.news.title), h("span", { class: "muted" }, ` · ${a.news.source || ""} · ${fmt.ago(a.news.published_at)}`)) : null,
    h("div", { class: "alert-figures" },
      h("div", { class: "move" }, h("span", { class: "muted small" }, "All'allerta"), h("b", { class: "mono" }, fmt.cents(a.price)),
        h("span", { class: "muted small" }, `stima ${fmt.pct(a.blended)}`)),
      h("div", { class: "move" }, h("span", { class: "muted small", title: `Vantaggio stimato sulla quota ${sideLabel(a.side)}` }, "Edge"),
        h("b", { class: `mono ${a.opportunity ? "pos" : ""}` }, a.edge == null ? "–" : fmt.pts(Math.abs(a.edge))),
        a.outlay ? h("span", { class: "muted small" }, fmt.money(a.outlay)) : null),
      ...moves,
      h("div", { class: "move" }, h("span", { class: "muted small" }, "Adesso"),
        h("b", { class: `mono ${moveCls(a.now_move)}` }, a.now_move == null ? "–" : fmt.pts(a.now_move)),
        h("span", { class: "muted small mono" }, fmt.cents(a.market.yes_price))),
    ),
  );
}

// ---------- Settings ----------

function settingsCard(ctx, s, categories) {
  const admin = ctx.isAdmin();
  const draft = { ...s, categories: [...s.categories] };
  const dis = !admin;

  const sw = (id, label, key) => {
    const input = h("input", { type: "checkbox", class: "switch", id, checked: draft[key], disabled: dis });
    input.addEventListener("change", () => { draft[key] = input.checked; });
    return h("label", { class: "field", for: id }, input, label);
  };
  const number = (id, label, key, { min, max, step, suffix }) => {
    const input = h("input", { id, class: "input", type: "number", min, max, step, value: String(draft[key]), disabled: dis, style: { maxWidth: "110px" } });
    input.addEventListener("input", () => { draft[key] = Number(input.value); });
    return h("label", { class: "field field-col", for: id }, label, h("span", { class: "inline" }, input, suffix ? h("span", { class: "muted small" }, suffix) : null));
  };
  const hourSelect = (id, key) => {
    const sel = h("select", { id, class: "input", disabled: dis, style: { width: "auto", minWidth: "96px" }, "aria-label": key === "quiet_start" ? "Inizio ore silenziose" : "Fine ore silenziose" },
      h("option", { value: "" }, "–"),
      Array.from({ length: 24 }, (_, i) => h("option", { value: String(i), selected: draft[key] === i }, `${String(i).padStart(2, "0")}:00`)));
    sel.addEventListener("change", () => { draft[key] = sel.value === "" ? null : Number(sel.value); });
    return sel;
  };

  const match = ctx.rangeField("al-match", "Pertinenza minima",
    { min: 30, max: 100, step: 5, value: Math.round(draft.min_match * 100), format: (v) => `${v}%` },
    (v) => { draft.min_match = v / 100; });
  if (dis) match.querySelector("input").disabled = true;

  const catBoxes = h("div", { class: "chips", role: "group", "aria-label": "Categorie" },
    categories.map((c) => {
      const id = `al-cat-${c}`.replace(/\W/g, "-");
      const box = h("input", { type: "checkbox", id, checked: draft.categories.includes(c), disabled: dis });
      box.addEventListener("change", () => {
        draft.categories = box.checked ? [...draft.categories, c] : draft.categories.filter((x) => x !== c);
      });
      return h("label", { class: "chip", for: id }, box, CATEGORY_LABELS[c] || c);
    }));

  const verdictGroup = h("div", { class: "field-col", role: "radiogroup", "aria-label": "Quando notificare" },
    [["GO", "Solo quando conviene"], ["SMALL", "Anche quando conviene con una puntata piccola"]].map(([value, label]) => {
      const input = h("input", { type: "radio", name: "al-verdict", value, checked: draft.min_verdict === value, disabled: dis });
      input.addEventListener("change", () => { draft.min_verdict = value; });
      return h("label", { class: "field" }, input, label);
    }));

  let actions = null;
  if (admin) {
    const save = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Salva");
    save.addEventListener("click", async () => {
      ctx.setBusy(save, true);
      try {
        const { enabled, telegram_enabled, categories: cats, min_match, max_news_age_hours, daily_budget, cooldown_hours, min_verdict, quiet_start, quiet_end } = draft;
        await api("/alerts/settings", { method: "PUT", body: { enabled, telegram_enabled, categories: cats, min_match, max_news_age_hours, daily_budget, cooldown_hours, min_verdict, quiet_start, quiet_end } });
        toast("Impostazioni delle allerte salvate");
        ctx.rerender();
      } catch (e) {
        toast(e.message, { error: true });
        ctx.setBusy(save, false);
      }
    });
    const testBtn = h("button", { class: "btn btn-ghost", type: "button", disabled: !s.telegram_configured,
      title: s.telegram_configured ? "Invia un messaggio di prova" : "Configura prima TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID" },
    h("span", { class: "spinner", "aria-hidden": "true" }), "Invia messaggio di prova");
    testBtn.addEventListener("click", async () => {
      ctx.setBusy(testBtn, true);
      try { await api("/alerts/test-telegram", { method: "POST" }); toast("Messaggio inviato: controlla Telegram"); }
      catch (e) { toast(e.message, { error: true }); }
      finally { ctx.setBusy(testBtn, false); }
    });
    actions = h("div", { class: "actions", style: { marginTop: "16px" } }, save, testBtn);
  }

  return h("section", { class: "card", "aria-labelledby": "h-alert-settings", style: { marginTop: "16px" } },
    h("div", { class: "card-head" },
      h("h2", { id: "h-alert-settings" }, "Impostazioni delle allerte"),
      !admin ? h("span", { class: "muted small" }, "Solo un amministratore può modificarle") : null),
    h("div", { class: "settings-grid" },
      h("div", { class: "field-col" },
        sw("al-enabled", "Allerte attive", "enabled"),
        sw("al-telegram", "Notifiche su Telegram", "telegram_enabled"),
        h("p", { class: "muted small" }, s.telegram_configured ? "Telegram configurato." : "Telegram non configurato (file .env)."),
      ),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, "Categorie da seguire"), catBoxes,
        h("p", { class: "muted small" }, "Nessuna selezionata = tutte.")),
      h("div", { class: "field-col" }, match,
        number("al-age", "Età massima della notizia", "max_news_age_hours", { min: 0.5, max: 72, step: 0.5, suffix: "ore" })),
      h("div", { class: "field-col" },
        number("al-budget", "Chiamate Jev al giorno", "daily_budget", { min: 0, max: 500, step: 1 }),
        number("al-cooldown", "Pausa per mercato dopo un'allerta", "cooldown_hours", { min: 0, max: 72, step: 0.5, suffix: "ore" })),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, "Quando notificare"), verdictGroup),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, `Ore silenziose (${s.timezone})`),
        h("span", { class: "inline small" }, "dalle", hourSelect("al-quiet-start", "quiet_start"), "alle", hourSelect("al-quiet-end", "quiet_end")),
        h("p", { class: "muted small" }, "In queste ore le notifiche arrivano senza suono.")),
    ),
    actions,
  );
}
