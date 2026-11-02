// Portafoglio reale (admins only): wallet settings, mode, balance, limits, real positions and orders.
// The simulated portfolio keeps deciding; this page shows what was sent to Polymarket with real money.
import { t } from "../i18n.js";
import { h, api, fmt, toast, icon, infoTip, statTile, emptyState } from "../ui.js";

const money = fmt.money;
const MODES = [
  ["off", () => t("Spento"), () => t("Solo simulazione: nessun ordine reale.")],
  ["manual", () => t("Solo le scommesse fatte a mano"), () => t("Diventano ordini reali solo le scommesse che aggiungi tu dal dettaglio di un mercato.")],
  ["auto", () => t("Tutte le scommesse"), () => t("Ogni acquisto e ordine limite della simulazione, anche automatico, diventa un ordine reale.")],
];
const SIGNATURES = [
  [0, () => t("0 · Portafoglio proprio (EOA)")],
  [1, () => t("1 · Account Polymarket con email")],
  [2, () => t("2 · Portafoglio del browser su Polymarket")],
];
const ORDER_STATUS = {
  filled: ["badge-good", t("Eseguito")], partial: ["badge-accent", t("Eseguito in parte")], live: ["badge-outline", t("In attesa nel book")],
  submitted: ["badge-outline", t("Inviato")], cancelled: ["", t("Annullato")], failed: ["badge-critical", t("Errore")], blocked: ["badge-warning", t("Non inviato")],
};
const sideBadge = (side) => h("span", { class: `badge ${side === "YES" ? "badge-accent" : "badge-outline"}` }, side === "YES" ? t("SÌ") : "NO");
const shortAddr = (a) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a || "–");
const modeLabel = (mode) => MODES.find(([k]) => k === mode)?.[1]() || mode;

/** Banner on the simulated portfolio page while real orders follow it. */
export function liveBanner(mode) {
  if (!mode || mode === "off") return null;
  return h("div", { class: "notice", role: "status", style: { marginBottom: "16px" } }, icon("alert"),
    h("div", {}, h("p", {}, h("b", {}, t("Portafoglio reale attivo. ")),
      mode === "auto" ? t("Ogni scommessa della simulazione diventa anche un ordine reale su Polymarket.")
        : t("Le scommesse che aggiungi a mano diventano anche ordini reali su Polymarket."),
      " ", h("a", { href: "#/portafoglio-reale" }, t("Vai al portafoglio reale")))));
}

export async function viewLivePortfolio(ctx) {
  const head = ctx.pageHead(t("Portafoglio reale"),
    t("Soldi veri su Polymarket. Il portafoglio simulato continua a decidere: quando questo è attivo, i suoi acquisti, ordini limite e vendite vengono inviati anche qui, con la stessa quota di capitale applicata al saldo reale ed entro i limiti di sicurezza."),
    h("a", { class: "btn btn-ghost", href: "#/portafoglio" }, icon("wallet"), t("Portafoglio simulato")));
  if (!ctx.isAdmin()) {
    return h("div", {}, head, emptyState(t("Solo per amministratori"), t("Il portafoglio reale si vede e si gestisce solo con un account amministratore.")));
  }
  const live = await api("/portfolio/live");
  const refresh = () => ctx.rerender();
  const exp = live.exposure || { today: 0, open: 0 };
  const orders = live.orders || [];
  const positions = live.positions || [];
  const filled = orders.filter((o) => o.status === "filled" || o.status === "partial").length;

  return h("div", {}, head,
    live.mode !== "off" ? h("div", { class: "notice", role: "status", style: { marginBottom: "16px" } }, icon("alert"),
      h("div", {}, h("p", {}, h("b", {}, t("Attivo: {0}. ", modeLabel(live.mode))),
        t("Gli ordini partono dal portafoglio {0}.", shortAddr(live.address))))) : null,
    h("div", { class: "kpis" },
      statTile(t("Saldo USDC"), live.balance != null ? money(live.balance) : "–", live.address ? shortAddr(live.address) : t("portafoglio non collegato")),
      statTile(t("In posizioni e ordini"), money(exp.open), t("limite {0}", money(live.limits.open))),
      statTile(t("Acquisti di oggi"), money(exp.today), t("limite {0} al giorno", money(live.limits.daily))),
      statTile(t("Ordini eseguiti"), fmt.int(filled), t("{0} posizioni aperte", fmt.int(positions.filter((p) => p.shares > 1e-6 && p.paper_status !== "lost").length))),
    ),
    h("div", { class: "grid-2", style: { marginBottom: "16px" } }, modeCard(live, refresh), settingsCard(live, refresh)),
    h("div", { class: "stack" },
      h("section", { class: "card", "aria-labelledby": "h-live-pos" },
        h("div", { class: "card-head" }, h("h2", { id: "h-live-pos" }, t("Posizioni reali ({0})", positions.length))), positionsTable(positions)),
      h("section", { class: "card", "aria-labelledby": "h-live-orders" },
        h("div", { class: "card-head" }, h("h2", { id: "h-live-orders" }, t("Ordini reali")), h("span", { class: "muted small" }, t("ultimi {0}", orders.length))),
        ordersTable(orders),
        h("p", { class: "muted small", style: { marginTop: "10px" } },
          t("Le quote vincenti dei mercati risolti si riscattano su polymarket.com. Gli ordini non inviati per i limiti di sicurezza restano in elenco con il motivo."))),
    ));
}

function modeCard(live, refresh) {
  const area = h("div", {});
  const busy = (on) => area.querySelectorAll("button, input").forEach((b) => { b.disabled = on; });
  const setMode = async (mode, confirm) => {
    busy(true);
    try {
      await api("/portfolio/live", { method: "PUT", body: { mode, confirm } });
      toast(mode === "off" ? t("Portafoglio reale spento") : t("Portafoglio reale attivo"));
      refresh();
    } catch (e) { toast(e.message, { error: true }); paint(); }
  };
  const paint = () => {
    area.replaceChildren(h("div", { class: "presets", role: "radiogroup", "aria-label": t("Modalità del portafoglio reale") },
      MODES.map(([key, label, help]) => {
        const input = h("input", { type: "radio", name: "live-mode", value: key, id: `live-${key}`, checked: key === live.mode,
          disabled: key !== "off" && !!live.unavailable });
        input.addEventListener("change", () => (key === "off" ? setMode("off", false) : askConfirm(key)));
        return h("label", { class: "preset", for: `live-${key}` }, input,
          h("span", { class: "preset-body" }, h("span", { class: "preset-name" }, label()), h("span", { class: "muted small" }, help())));
      })));
  };
  const askConfirm = (key) => {
    const go = h("button", { class: "btn btn-danger-solid btn-sm", type: "button", on: { click: () => setMode(key, true) } }, t("Sì, usa soldi veri"));
    area.replaceChildren(h("div", { class: "confirm", role: "group", "aria-label": t("Conferma l'uso di soldi veri"), style: { maxWidth: "none" } },
      h("p", { class: "small" }, h("b", {}, t("Soldi veri. ")),
        t("Gli ordini partono dal portafoglio {0} (saldo {1}), fino a {2} per ordine, {3} al giorno e {4} in posizioni aperte. Le perdite sono reali e i segnali non sono garantiti.",
          shortAddr(live.address), money(live.balance), money(live.limits.order), money(live.limits.daily), money(live.limits.open))),
      h("div", { class: "confirm-actions" }, go, h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: paint } }, t("Annulla")))));
    go.focus();
  };
  paint();

  const stopBtn = h("button", { class: "btn btn-danger-solid btn-sm", type: "button" }, icon("x"), t("Arresto di emergenza"));
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      const r = await api("/portfolio/live/stop", { method: "POST" });
      toast(t("Portafoglio reale spento; ordini in attesa annullati ({0})", r.cancelled));
      refresh();
    } catch (e) { toast(e.message, { error: true }); stopBtn.disabled = false; }
  });

  return h("section", { class: "card", "aria-labelledby": "h-live-mode" },
    h("div", { class: "card-head" },
      h("h2", { id: "h-live-mode" }, t("Attivazione"),
        infoTip(t("Con il portafoglio reale attivo, ogni acquisto, ordine limite e vendita della simulazione viene inviato anche su Polymarket: la stessa quota di capitale applicata al saldo del portafoglio, entro i limiti di sicurezza."))),
      h("span", { class: `badge ${live.mode === "off" ? "badge-outline" : "badge-warning"}` }, modeLabel(live.mode))),
    live.unavailable ? h("p", { class: "note", role: "status", style: { marginBottom: "12px" } }, icon("alert"), live.unavailable) : null,
    live.error ? h("p", { class: "note", role: "alert", style: { marginBottom: "12px" } }, icon("alert"), t("Portafoglio non raggiungibile: {0}", live.error)) : null,
    area,
    h("div", { style: { marginTop: "12px" } }, stopBtn),
    h("p", { class: "muted small", style: { marginTop: "8px" } },
      t("Spegne il portafoglio reale e annulla su Polymarket tutti gli ordini in attesa. Le quote già comprate restano nel portafoglio.")));
}

function settingsCard(live, refresh) {
  const c = live.config || {};
  const L = live.limits;
  const locked = live.mode !== "off";   // the wallet cannot be swapped while real orders are on
  const field = (id, label, input, hint) => h("div", { class: "form-field" }, h("label", { for: id }, label), input,
    hint ? h("span", { class: "field-hint" }, hint) : null);

  const key = h("input", { id: "lw-key", class: "input", type: "password", autocomplete: "off", spellcheck: "false", disabled: locked,
    placeholder: c.has_key ? t("Salvata · lascia vuoto per non cambiarla") : t("64 caratteri esadecimali, con o senza 0x") });
  const reveal = h("button", { class: "input-addon", type: "button", "aria-label": t("Mostra la chiave"), "aria-pressed": "false" }, icon("eye"));
  reveal.addEventListener("click", () => {
    const show = key.type === "password";
    key.type = show ? "text" : "password";
    reveal.setAttribute("aria-pressed", String(show));
  });
  const funder = h("input", { id: "lw-funder", class: "input", type: "text", autocomplete: "off", spellcheck: "false", disabled: locked,
    value: c.funder || "", placeholder: "0x…" });
  const sig = h("select", { id: "lw-sig", class: "input", disabled: locked },
    SIGNATURES.map(([v, label]) => h("option", { value: String(v), selected: v === c.signature_type }, label())));
  const num = (id, value, { min, max, step }) => h("input", { id, class: "input", type: "number", min, max, step, value: String(value), style: { maxWidth: "140px" } });
  const maxOrder = num("lw-order", L.order, { min: 1, step: 1 });
  const maxDaily = num("lw-daily", L.daily, { min: 1, step: 1 });
  const maxOpen = num("lw-open", L.open, { min: 1, step: 1 });
  const slip = num("lw-slip", Math.round(L.slippage * 100), { min: 0, max: 50, step: 1 });

  const save = h("button", { class: "btn btn-primary", type: "submit" }, t("Salva"));
  const form = h("form", { class: "form", novalidate: true },
    field("lw-key", t("Chiave privata del portafoglio"), h("div", { class: "input-group" }, key, reveal),
      c.key_source === "env" ? t("Ora viene usata quella del file .env; una chiave salvata qui la sostituisce.")
        : t("Non viene mai rimostrata: resta solo sul server.")),
    field("lw-funder", t("Indirizzo con i fondi (facoltativo)"), funder,
      t("L'indirizzo del tuo profilo Polymarket, se diverso da quello della chiave (account email o portafoglio collegato).")),
    field("lw-sig", t("Tipo di account"), sig, t("Hai fatto l'accesso a Polymarket con l'email: 1. Con MetaMask o un altro portafoglio del browser: 2. Usi la chiave direttamente, senza il sito: 0.")),
    locked ? h("p", { class: "muted small" }, t("Per cambiare chiave, indirizzo o tipo di account spegni prima il portafoglio reale.")) : null,
    h("div", { class: "grid-2", style: { alignItems: "start" } },
      field("lw-order", t("Massimo per ordine ($)"), maxOrder),
      field("lw-daily", t("Massimo al giorno ($)"), maxDaily),
      field("lw-open", t("Massimo in posizioni aperte ($)"), maxOpen),
      field("lw-slip", t("Margine sulle vendite (¢)"), slip, t("Una vendita reale scende fino a tanto sotto il prezzo di vendita simulato, non oltre."))),
    h("div", { class: "confirm-actions" }, save,
      c.key_source === "app" && !locked ? h("button", { class: "btn btn-ghost", type: "button", on: { click: async (e) => {
        e.currentTarget.disabled = true;
        try { await api("/portfolio/live/config", { method: "PUT", body: { clear_key: true } }); toast(t("Chiave rimossa")); refresh(); }
        catch (err) { toast(err.message, { error: true }); e.currentTarget.disabled = false; }
      } } }, t("Rimuovi la chiave")) : null),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      max_order_usd: Number(maxOrder.value), max_daily_usd: Number(maxDaily.value), max_open_usd: Number(maxOpen.value),
      sell_slippage: Number(slip.value) / 100,
    };
    if (!locked) {
      if (key.value.trim()) body.private_key = key.value.trim();
      body.funder = funder.value.trim();
      body.signature_type = Number(sig.value);
    }
    save.disabled = true;
    try {
      const r = await api("/portfolio/live/config", { method: "PUT", body });
      key.value = "";
      toast(r.error ? t("Impostazioni salvate, ma il portafoglio non risponde: {0}", r.error)
        : r.balance != null ? t("Impostazioni salvate · saldo {0}", money(r.balance)) : t("Impostazioni salvate"), { error: !!r.error });
      refresh();
    } catch (err) { toast(err.message, { error: true }); save.disabled = false; }
  });

  return h("section", { class: "card", "aria-labelledby": "h-live-cfg" },
    h("div", { class: "card-head" }, h("h2", { id: "h-live-cfg" }, t("Portafoglio e limiti"),
      infoTip(t("Con un account email di Polymarket la chiave privata si esporta dalle impostazioni del sito; l'indirizzo con i fondi è quello mostrato nel tuo profilo."))),
      c.has_key ? h("span", { class: "badge badge-good" }, icon("check"), t("Chiave salvata")) : h("span", { class: "badge badge-outline" }, t("Nessuna chiave"))),
    h("div", { class: "notice small", role: "note" }, icon("alert"), h("p", {},
      t("La chiave viene salvata nel database del server: chi ha accesso al server o ai suoi backup può spendere i fondi. Usa un portafoglio con dentro solo i soldi che accetti di perdere."))),
    form);
}

function positionsTable(positions) {
  if (!positions.length) return h("p", { class: "secondary" }, t("Nessuna posizione reale."));
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, t("Mercato")), h("th", {}, t("Lato")), h("th", { class: "num" }, t("Quote")),
      h("th", { class: "num" }, t("Prezzo medio")), h("th", { class: "num" }, t("Costo")), h("th", { class: "num" }, t("In attesa")), h("th", {}, t("Stato")))),
    h("tbody", {}, positions.map((p) => h("tr", {},
      h("td", { class: "q-cell" }, h("a", { href: `#/mercati/${encodeURIComponent(p.market_id)}` }, p.question)), h("td", {}, sideBadge(p.side)),
      h("td", { class: "num" }, fmt.shares(p.shares)), h("td", { class: "num" }, p.avg_price != null ? fmt.cents(p.avg_price) : "–"),
      h("td", { class: "num" }, money(p.cost)), h("td", { class: "num" }, p.resting ? money(p.resting) : "–"),
      h("td", {}, p.to_redeem ? h("span", { class: "badge badge-good", title: t("Mercato risolto: riscatta le quote vincenti su polymarket.com") }, t("Da riscattare"))
        : p.paper_status === "lost" ? h("span", { class: "badge badge-critical" }, t("Persa"))
          : h("span", { class: "badge badge-outline" }, t("Aperta"))),
    )))));
}

function ordersTable(orders) {
  if (!orders.length) return h("p", { class: "secondary" }, t("Nessun ordine reale finora."));
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, t("Data")), h("th", {}, t("Mercato")), h("th", {}, t("Operazione")), h("th", { class: "num" }, t("Quote")),
      h("th", { class: "num" }, t("Prezzo")), h("th", { class: "num" }, t("Importo")), h("th", {}, t("Stato")))),
    h("tbody", {}, orders.map((o) => {
      const [cls, label] = ORDER_STATUS[o.status] || ["", o.status];
      return h("tr", {},
        h("td", { class: "nowrap" }, fmt.dateTime(o.created_at)),
        h("td", { class: "q-cell" }, h("a", { href: `#/mercati/${encodeURIComponent(o.market_id)}` }, o.question)),
        h("td", { class: "nowrap" }, o.action === "BUY" ? t("Acquisto") : t("Vendita"), " ", sideBadge(o.side),
          h("div", { class: "muted small" }, o.order_type === "GTD" ? t("ordine limite") : t("subito"), o.placed_by === "manual" ? t(" · a mano") : "")),
        h("td", { class: "num" }, !o.shares ? "–" : o.filled_shares && o.filled_shares < o.shares ? `${fmt.shares(o.filled_shares)} / ${fmt.shares(o.shares)}` : fmt.shares(o.shares)),
        h("td", { class: "num" }, fmt.cents(o.price)), h("td", { class: "num" }, o.shares ? money(o.usd) : "–"),
        h("td", {}, h("span", { class: `badge ${cls}` }, label), o.error ? h("div", { class: "muted small", style: { whiteSpace: "normal", maxWidth: "320px" } }, o.error) : null));
    }))));
}
