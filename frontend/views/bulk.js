// "Valuta tutti con Jev": starts a Jev forecast on every market with recent news and shows its progress.
import { t } from "../i18n.js";
import { h, api, fmt, toast, icon } from "../ui.js";

const POLL_MS = 3000;

/** Returns { button, panel }: the button goes in the page head, the panel right below it. */
export function bulkPredict(ctx) {
  const panel = h("div", { class: "bulk-panel", hidden: true, "aria-live": "polite" });
  const button = h("button", { class: "btn btn-ghost", type: "button", disabled: true },
    h("span", { class: "spinner", "aria-hidden": "true" }), icon("play"), t("Valuta tutti con Jev"));
  if (!ctx.isAdmin()) return { button: null, panel: null };

  let state = null;
  let timer = null;
  let wasRunning = false;

  const stopPolling = () => { clearTimeout(timer); timer = null; };
  const poll = async () => {
    stopPolling();
    if (!button.isConnected && state) return; // page left: stop polling
    try {
      state = await api("/markets/predict-all");
    } catch (e) {
      button.title = e.message;
      return;
    }
    paint();
    if (state.running) timer = setTimeout(poll, POLL_MS);
  };

  function paint() {
    const running = state.running;
    const blocker = !state.jev_enabled ? t("Serve TYPESAFE_API_KEY nel file .env.")
      : state.eligible.all === 0 ? t("Nessun mercato aperto ha notizie recenti collegate. Aggiorna notizie e mercati.")
      : null;
    button.disabled = running || Boolean(blocker);
    button.classList.toggle("loading", running);
    button.title = running ? t("Valutazione in corso") : blocker || t("Chiede una previsione a Jev per {0} con notizie recenti", fmt.count(state.eligible.all, t("mercato"), t("mercati")));
    if (running) {
      wasRunning = true;
      paintProgress();
    } else if (wasRunning) {
      wasRunning = false;
      paintDone();
      ctx.rerender(); // new forecasts: refresh the list (this panel is rebuilt and shows the summary)
    } else if (state.finished_at && panel.dataset.open !== "1" && !dismissed()) {
      paintDone();
    }
  }

  function minutes(n) {
    const m = Math.ceil(n / Math.max(1, state.jev_rpm));
    return m <= 1 ? t("circa un minuto") : t("circa {0} minuti", fmt.int(m));
  }

  function askConfirm() {
    const { all, new: fresh } = state.eligible;
    let onlyNew = false;
    const cost = h("p", { class: "small" });
    const paintCost = () => {
      const n = onlyNew ? fresh : all;
      cost.textContent = t("{0} a pagamento a TypeSafe, una per mercato. ", fmt.count(n, t("chiamata"), t("chiamate")))
        + t("Con il limite di {0} richieste al minuto servono {1}. ", fmt.int(state.jev_rpm), minutes(n))
        + t("Prima vengono aggiornati prezzi e collegamenti; le scommesse simulate seguono le regole del portafoglio.");
      go.disabled = n === 0;
    };
    const radio = (value, label, checked) => {
      const input = h("input", { type: "radio", name: "bulk-scope", value, checked });
      input.addEventListener("change", () => { onlyNew = value === "new"; paintCost(); });
      return h("label", { class: "field" }, input, label);
    };
    const go = h("button", { class: "btn btn-primary btn-sm", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), t("Avvia valutazione"));
    go.addEventListener("click", async () => {
      ctx.setBusy(go, true);
      try {
        state = await api("/markets/predict-all", { method: "POST", params: { only_new: onlyNew } });
        toast(t("Valutazione avviata: puoi continuare a usare la dashboard."));
        delete panel.dataset.open;
        wasRunning = true;
        paintProgress();
        poll();
      } catch (e) {
        toast(e.message, { error: true });
        ctx.setBusy(go, false);
      }
    });
    panel.dataset.open = "1";
    panel.hidden = false;
    panel.replaceChildren(h("div", { class: "card bulk-card", role: "group", "aria-labelledby": "h-bulk" },
      h("h2", { id: "h-bulk" }, t("Valutare tutti i mercati con Jev?")),
      h("div", { class: "bulk-scope", role: "radiogroup", "aria-label": t("Quali mercati") },
        radio("all", t("Tutti quelli con notizie recenti ({0})", fmt.int(all)), true),
        radio("new", t("Solo mai valutati o con notizie nuove ({0})", fmt.int(fresh)), false),
      ),
      cost,
      h("div", { class: "confirm-actions" }, go,
        h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: close } }, t("Annulla"))),
    ));
    paintCost();
    go.focus();
  }

  function close() {
    delete panel.dataset.open;
    panel.hidden = true;
    panel.replaceChildren();
    button.focus();
  }

  function counters() {
    const parts = [t("{0} di {1} valutati", fmt.int(state.done), fmt.int(state.total))];
    if (state.skipped) parts.push(t("{0} saltati", fmt.int(state.skipped)));
    if (state.failed) parts.push(t("{0} con errore", fmt.int(state.failed)));
    return parts.join(" · ");
  }

  function paintProgress() {
    const processed = state.done + state.skipped + state.failed;
    const stopBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, icon("pause"), t("Interrompi"));
    stopBtn.addEventListener("click", async () => {
      stopBtn.disabled = true;
      try { state = await api("/markets/predict-all/stop", { method: "POST" }); paint(); }
      catch (e) { toast(e.message, { error: true }); stopBtn.disabled = false; }
    });
    panel.hidden = false;
    panel.replaceChildren(h("div", { class: "card bulk-card" },
      h("div", { class: "card-head" }, h("h2", {}, t("Valutazione Jev in corso")), stopBtn),
      h("progress", { max: String(Math.max(1, state.total)), value: String(processed), "aria-label": t("Avanzamento") }),
      state.total ? h("p", { class: "small mono" }, counters()) : null,
      state.current ? h("p", { class: "small muted bulk-current" }, t("Ora: "), state.current) : null,
      state.message ? h("p", { class: "small" }, state.message) : null,
    ));
  }

  function paintDone() {
    panel.hidden = false;
    panel.replaceChildren(h("div", { class: "card bulk-card" },
      h("div", { class: "card-head" },
        h("h2", {}, t("Valutazione di tutti i mercati")),
        h("button", { class: "btn btn-ghost btn-sm btn-square", type: "button", "aria-label": t("Chiudi"), on: { click: dismiss } }, icon("x"))),
      h("p", { class: "small" }, state.message || t("Completata")),
      h("p", { class: "small mono" }, `${counters()} · ${fmt.ago(state.finished_at)}`),
      ...state.errors.map((e) => h("p", { class: "small muted" }, e)),
    ));
  }

  function dismissed() {
    try { return sessionStorage.getItem("bulk-dismissed") === state.finished_at; } catch { return false; }
  }

  function dismiss() {
    try { sessionStorage.setItem("bulk-dismissed", state.finished_at); } catch {}
    close();
  }

  button.addEventListener("click", () => {
    if (!state || state.running) return;
    if (panel.dataset.open === "1") return close();
    askConfirm();
  });

  // Initial state: progress if a run is going on, else the last run's summary (until dismissed)
  api("/markets/predict-all").then((s) => {
    state = s;
    if (s.running) wasRunning = true;
    paint();
    if (s.running) poll();
  }).catch((e) => { button.title = e.message; });

  return { button, panel };
}
