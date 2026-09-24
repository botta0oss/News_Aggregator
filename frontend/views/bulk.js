// "Valuta tutti con Jev": starts a Jev forecast on every market with recent news and shows its progress.
import { h, api, fmt, toast, icon } from "../ui.js";

const POLL_MS = 3000;

/** Returns { button, panel }: the button goes in the page head, the panel right below it. */
export function bulkPredict(ctx) {
  const panel = h("div", { class: "bulk-panel", hidden: true, "aria-live": "polite" });
  const button = h("button", { class: "btn btn-ghost", type: "button", disabled: true },
    h("span", { class: "spinner", "aria-hidden": "true" }), icon("play"), "Valuta tutti con Jev");
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
    const blocker = !state.jev_enabled ? "Serve TYPESAFE_API_KEY nel file .env."
      : state.eligible.all === 0 ? "Nessun mercato aperto ha notizie recenti collegate. Aggiorna notizie e mercati."
      : null;
    button.disabled = running || Boolean(blocker);
    button.classList.toggle("loading", running);
    button.title = running ? "Valutazione in corso" : blocker || `Chiede una previsione a Jev per ${fmt.count(state.eligible.all, "mercato", "mercati")} con notizie recenti`;
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
    return m <= 1 ? "circa un minuto" : `circa ${fmt.int(m)} minuti`;
  }

  function askConfirm() {
    const { all, new: fresh } = state.eligible;
    let onlyNew = false;
    const cost = h("p", { class: "small" });
    const paintCost = () => {
      const n = onlyNew ? fresh : all;
      cost.textContent = `${fmt.count(n, "chiamata", "chiamate")} a pagamento a TypeSafe, una per mercato. `
        + `Con il limite di ${fmt.int(state.jev_rpm)} richieste al minuto servono ${minutes(n)}. `
        + "Prima vengono aggiornati prezzi e collegamenti; le scommesse simulate seguono le regole del portafoglio.";
      go.disabled = n === 0;
    };
    const radio = (value, label, checked) => {
      const input = h("input", { type: "radio", name: "bulk-scope", value, checked });
      input.addEventListener("change", () => { onlyNew = value === "new"; paintCost(); });
      return h("label", { class: "field" }, input, label);
    };
    const go = h("button", { class: "btn btn-primary btn-sm", type: "button" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Avvia valutazione");
    go.addEventListener("click", async () => {
      ctx.setBusy(go, true);
      try {
        state = await api("/markets/predict-all", { method: "POST", params: { only_new: onlyNew } });
        toast("Valutazione avviata: puoi continuare a usare la dashboard.");
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
      h("h2", { id: "h-bulk" }, "Valutare tutti i mercati con Jev?"),
      h("div", { class: "bulk-scope", role: "radiogroup", "aria-label": "Quali mercati" },
        radio("all", `Tutti quelli con notizie recenti (${fmt.int(all)})`, true),
        radio("new", `Solo mai valutati o con notizie nuove (${fmt.int(fresh)})`, false),
      ),
      cost,
      h("div", { class: "confirm-actions" }, go,
        h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: close } }, "Annulla")),
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
    const parts = [`${fmt.int(state.done)} di ${fmt.int(state.total)} valutati`];
    if (state.skipped) parts.push(`${fmt.int(state.skipped)} saltati`);
    if (state.failed) parts.push(`${fmt.int(state.failed)} con errore`);
    return parts.join(" · ");
  }

  function paintProgress() {
    const processed = state.done + state.skipped + state.failed;
    const stopBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, icon("pause"), "Interrompi");
    stopBtn.addEventListener("click", async () => {
      stopBtn.disabled = true;
      try { state = await api("/markets/predict-all/stop", { method: "POST" }); paint(); }
      catch (e) { toast(e.message, { error: true }); stopBtn.disabled = false; }
    });
    panel.hidden = false;
    panel.replaceChildren(h("div", { class: "card bulk-card" },
      h("div", { class: "card-head" }, h("h2", {}, "Valutazione Jev in corso"), stopBtn),
      h("progress", { max: String(Math.max(1, state.total)), value: String(processed), "aria-label": "Avanzamento" }),
      state.total ? h("p", { class: "small mono" }, counters()) : null,
      state.current ? h("p", { class: "small muted bulk-current" }, "Ora: ", state.current) : null,
      state.message ? h("p", { class: "small" }, state.message) : null,
    ));
  }

  function paintDone() {
    panel.hidden = false;
    panel.replaceChildren(h("div", { class: "card bulk-card" },
      h("div", { class: "card-head" },
        h("h2", {}, "Valutazione di tutti i mercati"),
        h("button", { class: "btn btn-ghost btn-sm btn-square", type: "button", "aria-label": "Chiudi", on: { click: dismiss } }, icon("x"))),
      h("p", { class: "small" }, state.message || "Completata"),
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
