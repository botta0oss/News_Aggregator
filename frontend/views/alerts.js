// Allerte: fresh news evaluated right away, Telegram notifications and the price afterwards.
import { t } from "../i18n.js";
import { h, api, fmt, toast, icon, statTile, emptyState, infoTip, externalLink, CATEGORY_LABELS } from "../ui.js";
import { verdictBadge } from "../economics.js";

const CHECKPOINTS = [["15m", t("15 min")], ["1h", t("1 ora")], ["6h", t("6 ore")], ["24h", t("24 ore")]];
const filters = { kind: "opportunities" };

const moveCls = (m) => (m == null ? "muted" : m > 0 ? "pos" : m < 0 ? "neg" : "");
const sideLabel = (side) => (side === "YES" ? t("SÌ") : side === "NO" ? "NO" : "–");

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
      list.replaceChildren(emptyState(t("Impossibile caricare le allerte"), e.message));
    }
  };

  const kindSwitch = h("div", { class: "segmented", role: "group", "aria-label": t("Quali allerte") },
    [["opportunities", t("Solo opportunità")], ["all", t("Tutte le valutazioni")]].map(([value, label]) => {
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
    runBtn = h("button", { class: "btn btn-ghost", type: "button", title: t("Controlla subito le notizie collegate dall'ultimo controllo") },
      h("span", { class: "spinner", "aria-hidden": "true" }), icon("refresh"), t("Controlla ora"));
    runBtn.addEventListener("click", async () => {
      ctx.setBusy(runBtn, true);
      try {
        const r = await api("/alerts/run", { method: "POST" });
        toast(r.evaluated
          ? `${fmt.count(r.evaluated, t("mercato valutato"), t("mercati valutati"))}, ${fmt.count(r.opportunities, t("opportunità"), t("opportunità"))}.`
          : r.triggers ? t("Notizie trovate, ma i mercati sono in pausa o il limite giornaliero è raggiunto.") : t("Nessuna notizia nuova da valutare."));
        ctx.rerender();
      } catch (e) {
        toast(e.message, { error: true });
        ctx.setBusy(runBtn, false);
      }
    });
  }

  await loadList();
  return h("div", {},
    ctx.pageHead(t("Allerte"),
      t("Quando esce una notizia importante per un mercato, Jev lo valuta subito. Se conviene arriva una notifica su Telegram; poi si misura se il prezzo si è mosso nella direzione prevista."),
      runBtn),
    setupNotes(settings),
    resultsCard(summary),
    h("div", { class: "section-head" }, h("h2", {}, t("Ultime allerte")), kindSwitch),
    list,
    settingsCard(ctx, settings, categories),
  );
}

function setupNotes(s) {
  const notes = [];
  if (!s.jev_enabled) notes.push(t("Serve TYPESAFE_API_KEY: le allerte usano Jev per valutare il mercato."));
  if (!s.enabled) notes.push(t("Le allerte sono disattivate (vedi le impostazioni in fondo)."));
  if (s.enabled && !s.telegram_configured) notes.push(t("Telegram non è configurato: le allerte compaiono qui, ma non arrivano notifiche. Imposta TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nel file .env."));
  const cadence = s.scan_minutes > 0 && s.scan_minutes < s.ingest_minutes
    ? t("Le fonti vengono controllate ogni {0} minuti.", s.scan_minutes)
    : t("Le fonti vengono controllate ogni {0} minuti, con la raccolta completa.", s.ingest_minutes);
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
      h("th", { scope: "row" }, t("Dopo {0}", label)),
      h("td", { class: "num" }, fmt.int(m.count)),
      h("td", { class: `num ${moveCls(m.avg_move)}` }, m.avg_move == null ? "–" : fmt.pts(m.avg_move)),
      h("td", { class: "num" }, m.share_favorable == null ? "–" : fmt.pct(m.share_favorable)),
    );
  });
  return h("section", { class: "card", "aria-labelledby": "h-alert-results", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" },
      h("h2", { id: "h-alert-results" }, t("Risultati degli ultimi {0} giorni", s.days)),
      infoTip(t("Movimento a favore: di quanti punti il prezzo si è spostato nella direzione consigliata dopo l'allerta. Positivo vuol dire che l'allerta è arrivata prima del mercato. Servono decine di allerte perché i numeri dicano qualcosa.")),
    ),
    h("div", { class: "kpis" },
      statTile(t("Opportunità"), fmt.int(s.opportunities), `su ${fmt.count(s.evaluated, t("valutazione"), t("valutazioni"))}`),
      statTile(t("Notificate"), fmt.int(s.notified), t("su Telegram")),
      statTile(t("A favore dopo 1 ora"), oneHour.share_favorable == null ? "–" : fmt.pct(oneHour.share_favorable),
        oneHour.avg_move == null ? t("nessun dato ancora") : t("media {0}", fmt.pts(oneHour.avg_move))),
      statTile(t("Chiamate Jev (24 ore)"), `${fmt.int(s.calls_last_24h)} / ${fmt.int(s.daily_budget)}`, t("limite giornaliero delle allerte")),
    ),
    h("div", { class: "table-wrap" },
      h("table", { class: "compact-table" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, t("Quando")), h("th", { scope: "col", class: "num" }, t("Allerte")),
          h("th", { scope: "col", class: "num" }, t("Media a favore")), h("th", { scope: "col", class: "num" }, t("A favore")))),
        h("tbody", {}, rows),
      )),
    s.resolved ? h("p", { class: "muted small", style: { marginTop: "8px" } },
      t("Mercati già risolti: {0} su {1} nella direzione dell'allerta.", fmt.int(s.won), fmt.int(s.resolved))) : null,
    s.clv?.n ? h("p", { class: "muted small", style: { marginTop: "4px" } },
      t("Fino alla chiusura ({0}): in media {1} nella direzione dell'allerta, a favore nel {2} dei casi. ", fmt.count(s.clv.n, t("mercato chiuso"), t("mercati chiusi")), fmt.pts(s.clv.avg), fmt.pct(s.clv.share_positive)),
      t("È il segnale più affidabile che le allerte anticipano davvero il mercato.")) : null,
  );
}

function emptyList(s) {
  if (filters.kind === "opportunities") {
    return emptyState(t("Nessuna opportunità ancora"),
      t("Le allerte partono quando una notizia fresca e pertinente viene collegata a un mercato e Jev dice che conviene scommettere. Guarda «Tutte le valutazioni» per vedere anche i casi in cui non conveniva."));
  }
  return emptyState(t("Nessuna valutazione ancora"), s.enabled ? t("Appena arriva una notizia importante per un mercato, compare qui.") : t("Le allerte sono disattivate."));
}

function notifyState(a) {
  if (a.notified_at) return h("span", { class: "muted small", title: fmt.dateTime(a.notified_at) }, icon("check"), t(" Notificata"));
  if (a.notify_error) return h("span", { class: "small neg", title: a.notify_error }, icon("alert"), t(" Notifica non riuscita"));
  return a.opportunity ? h("span", { class: "muted small" }, t("Non notificata")) : null;
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
  const href = a.market.multi_event_id ? `#/multi/${encodeURIComponent(a.market.multi_event_id)}` : `#/mercati/${encodeURIComponent(a.market.id)}`;
  return h("article", { class: `card alert-card${a.opportunity ? "" : " alert-muted"}` },
    h("div", { class: "alert-top" },
      a.verdict ? verdictBadge(a.verdict) : null,
      a.side ? h("span", { class: "badge badge-outline" }, t("Compra {0}", sideLabel(a.side))) : null,
      h("span", { class: "muted small", title: fmt.dateTime(a.created_at) }, fmt.ago(a.created_at)),
      notifyState(a),
    ),
    h("h3", { class: "alert-q" }, h("a", { href }, a.market.question)),
    a.news ? h("p", { class: "small" }, icon("news"), " ",
      externalLink(a.news.url, a.news.title), h("span", { class: "muted" }, ` · ${a.news.source || ""} · ${fmt.ago(a.news.published_at)}`)) : null,
    h("div", { class: "alert-figures" },
      h("div", { class: "move" }, h("span", { class: "muted small" }, "All'allerta"), h("b", { class: "mono" }, fmt.cents(a.price)),
        h("span", { class: "muted small" }, t("stima {0}", fmt.pct(a.blended)))),
      h("div", { class: "move" }, h("span", { class: "muted small", title: t("Vantaggio stimato sulla quota {0}", sideLabel(a.side)) }, t("Edge")),
        h("b", { class: `mono ${a.opportunity ? "pos" : ""}` }, a.edge == null ? "–" : fmt.pts(Math.abs(a.edge))),
        a.outlay ? h("span", { class: "muted small" }, fmt.money(a.outlay)) : null),
      ...moves,
      h("div", { class: "move" }, h("span", { class: "muted small" }, t("Adesso")),
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
    const sel = h("select", { id, class: "input", disabled: dis, style: { width: "auto", minWidth: "96px" }, "aria-label": key === "quiet_start" ? t("Inizio ore silenziose") : t("Fine ore silenziose") },
      h("option", { value: "" }, "–"),
      Array.from({ length: 24 }, (_, i) => h("option", { value: String(i), selected: draft[key] === i }, `${String(i).padStart(2, "0")}:00`)));
    sel.addEventListener("change", () => { draft[key] = sel.value === "" ? null : Number(sel.value); });
    return sel;
  };

  const match = ctx.rangeField("al-match", t("Pertinenza minima"),
    { min: 30, max: 100, step: 5, value: Math.round(draft.min_match * 100), format: (v) => `${v}%` },
    (v) => { draft.min_match = v / 100; });
  if (dis) match.querySelector("input").disabled = true;

  const catBoxes = h("div", { class: "chips", role: "group", "aria-label": t("Categorie") },
    categories.map((c) => {
      const id = `al-cat-${c}`.replace(/\W/g, "-");
      const box = h("input", { type: "checkbox", id, checked: draft.categories.includes(c), disabled: dis });
      box.addEventListener("change", () => {
        draft.categories = box.checked ? [...draft.categories, c] : draft.categories.filter((x) => x !== c);
      });
      return h("label", { class: "chip", for: id }, box, CATEGORY_LABELS[c] || c);
    }));

  const verdictGroup = h("div", { class: "field-col", role: "radiogroup", "aria-label": t("Quando notificare") },
    [["GO", t("Solo quando conviene")], ["SMALL", t("Anche quando conviene con una puntata piccola")]].map(([value, label]) => {
      const input = h("input", { type: "radio", name: "al-verdict", value, checked: draft.min_verdict === value, disabled: dis });
      input.addEventListener("change", () => { draft.min_verdict = value; });
      return h("label", { class: "field" }, input, label);
    }));

  let actions = null;
  if (admin) {
    const save = h("button", { class: "btn btn-primary", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), t("Salva"));
    save.addEventListener("click", async () => {
      ctx.setBusy(save, true);
      try {
        const { enabled, telegram_enabled, categories: cats, min_match, max_news_age_hours, daily_budget, cooldown_hours, min_verdict, quiet_start, quiet_end } = draft;
        await api("/alerts/settings", { method: "PUT", body: { enabled, telegram_enabled, categories: cats, min_match, max_news_age_hours, daily_budget, cooldown_hours, min_verdict, quiet_start, quiet_end } });
        toast(t("Impostazioni delle allerte salvate"));
        ctx.rerender();
      } catch (e) {
        toast(e.message, { error: true });
        ctx.setBusy(save, false);
      }
    });
    const testBtn = h("button", { class: "btn btn-ghost", type: "button", disabled: !s.telegram_configured,
      title: s.telegram_configured ? t("Invia un messaggio di prova") : t("Configura prima TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID") },
    h("span", { class: "spinner", "aria-hidden": "true" }), t("Invia messaggio di prova"));
    testBtn.addEventListener("click", async () => {
      ctx.setBusy(testBtn, true);
      try { await api("/alerts/test-telegram", { method: "POST" }); toast(t("Messaggio inviato: controlla Telegram")); }
      catch (e) { toast(e.message, { error: true }); }
      finally { ctx.setBusy(testBtn, false); }
    });
    actions = h("div", { class: "actions", style: { marginTop: "16px" } }, save, testBtn);
  }

  return h("section", { class: "card", "aria-labelledby": "h-alert-settings", style: { marginTop: "16px" } },
    h("div", { class: "card-head" },
      h("h2", { id: "h-alert-settings" }, t("Impostazioni delle allerte")),
      !admin ? h("span", { class: "muted small" }, t("Solo un amministratore può modificarle")) : null),
    h("div", { class: "settings-grid" },
      h("div", { class: "field-col" },
        sw("al-enabled", t("Allerte attive"), "enabled"),
        sw("al-telegram", t("Notifiche su Telegram"), "telegram_enabled"),
        h("p", { class: "muted small" }, s.telegram_configured ? t("Telegram configurato.") : t("Telegram non configurato (file .env).")),
      ),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, t("Categorie da seguire")), catBoxes,
        h("p", { class: "muted small" }, t("Nessuna selezionata = tutte."))),
      h("div", { class: "field-col" }, match,
        number("al-age", t("Età massima della notizia"), "max_news_age_hours", { min: 0.5, max: 72, step: 0.5, suffix: "ore" })),
      h("div", { class: "field-col" },
        number("al-budget", t("Chiamate Jev al giorno"), "daily_budget", { min: 0, max: 500, step: 1 }),
        number("al-cooldown", t("Pausa per mercato dopo un'allerta"), "cooldown_hours", { min: 0, max: 72, step: 0.5, suffix: "ore" })),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, t("Quando notificare")), verdictGroup),
      h("div", { class: "field-col" }, h("span", { class: "field-label" }, t("Ore silenziose ({0})", s.timezone)),
        h("span", { class: "inline small" }, "dalle", hourSelect("al-quiet-start", "quiet_start"), "alle", hourSelect("al-quiet-end", "quiet_end")),
        h("p", { class: "muted small" }, t("In queste ore le notifiche arrivano senza suono."))),
    ),
    actions,
  );
}
