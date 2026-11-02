// Real wallet on Polymarket (Portfolio page, admins only): mode, balance, limits, real positions and orders.
import { t } from "../i18n.js";
import { h, api, fmt, toast, icon, infoTip } from "../ui.js";

const money = fmt.money;
const MODES = [
  ["off", () => t("Spento"), () => t("Solo simulazione: nessun ordine reale.")],
  ["manual", () => t("Solo le scommesse fatte a mano"), () => t("Diventano ordini reali solo le scommesse che aggiungi tu dal dettaglio di un mercato.")],
  ["auto", () => t("Tutte le scommesse"), () => t("Ogni acquisto e ordine limite della simulazione, anche automatico, diventa un ordine reale.")],
];
const ORDER_STATUS = {
  filled: ["badge-good", t("Eseguito")], partial: ["badge-accent", t("Eseguito in parte")], live: ["badge-outline", t("In attesa nel book")],
  submitted: ["badge-outline", t("Inviato")], cancelled: ["", t("Annullato")], failed: ["badge-critical", t("Errore")], blocked: ["badge-warning", t("Non inviato")],
};
const sideBadge = (side) => h("span", { class: `badge ${side === "YES" ? "badge-accent" : "badge-outline"}` }, side === "YES" ? t("SÌ") : "NO");
const shortAddr = (a) => (a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a || "–");

export function liveBanner(live) {
  if (!live || live.mode === "off") return null;
  return h("div", { class: "notice", role: "status", style: { marginBottom: "16px" } }, icon("alert"),
    h("div", {}, h("p", {}, h("b", {}, t("Portafoglio reale attivo. ")),
      live.mode === "auto" ? t("Ogni scommessa della simulazione diventa anche un ordine reale su Polymarket.")
        : t("Le scommesse che aggiungi a mano diventano anche ordini reali su Polymarket."))));
}

export function liveCard(ctx, live, refresh) {
  if (!live) return null;
  const busy = (el, on) => { el.querySelectorAll("button, input").forEach((b) => { b.disabled = on; }); };

  const head = h("div", { class: "card-head" },
    h("h2", { id: "h-live" }, t("Portafoglio reale"),
      infoTip(t("La simulazione continua a decidere. Con il portafoglio reale attivo, ogni suo acquisto, ordine limite e vendita viene inviato anche su Polymarket con soldi veri: la stessa quota di capitale applicata al saldo del portafoglio, entro i limiti di sicurezza."))),
    h("span", { class: `badge ${live.mode === "off" ? "badge-outline" : "badge-warning"}` }, MODES.find(([k]) => k === live.mode)?.[1]() || live.mode));

  if (live.unavailable) {
    return h("section", { class: "card", "aria-labelledby": "h-live" }, head,
      h("p", { class: "secondary" }, live.unavailable),
      h("p", { class: "muted small" }, t("Per usarlo: LIVE_TRADING_ENABLED=true e la chiave del portafoglio in .env, poi riavvia. Istruzioni nella documentazione di configurazione.")));
  }

  // Mode: turning real money on asks for a confirmation first
  const modeArea = h("div", {});
  const setMode = async (mode, confirm) => {
    busy(modeArea, true);
    try {
      await api("/portfolio/live", { method: "PUT", body: { mode, confirm } });
      toast(mode === "off" ? t("Portafoglio reale spento") : t("Portafoglio reale attivo"));
      refresh();
    } catch (e) { toast(e.message, { error: true }); busy(modeArea, false); }
  };
  const paintModes = () => {
    modeArea.replaceChildren(h("div", { class: "presets", role: "radiogroup", "aria-label": t("Modalità del portafoglio reale") },
      MODES.map(([key, label, help]) => {
        const input = h("input", { type: "radio", name: "live-mode", value: key, id: `live-${key}`, checked: key === live.mode });
        input.addEventListener("change", () => (key === "off" ? setMode("off", false) : askConfirm(key)));
        return h("label", { class: "preset", for: `live-${key}` }, input,
          h("span", { class: "preset-body" }, h("span", { class: "preset-name" }, label()), h("span", { class: "muted small" }, help())));
      })));
  };
  const askConfirm = (key) => {
    const go = h("button", { class: "btn btn-danger-solid btn-sm", type: "button", on: { click: () => setMode(key, true) } }, t("Sì, usa soldi veri"));
    modeArea.replaceChildren(h("div", { class: "confirm", role: "group", "aria-label": t("Conferma l'uso di soldi veri"), style: { maxWidth: "none" } },
      h("p", { class: "small" }, h("b", {}, t("Soldi veri. ")),
        t("Gli ordini partono dal portafoglio {0} (saldo {1}), fino a {2} per ordine, {3} al giorno e {4} in posizioni aperte. Le perdite sono reali e i segnali non sono garantiti.",
          shortAddr(live.address), money(live.balance), money(live.limits.order), money(live.limits.daily), money(live.limits.open))),
      h("div", { class: "confirm-actions" }, go, h("button", { class: "btn btn-ghost btn-sm", type: "button", on: { click: paintModes } }, t("Annulla")))));
    go.focus();
  };
  paintModes();

  const stopBtn = h("button", { class: "btn btn-danger-solid btn-sm", type: "button" }, icon("x"), t("Arresto di emergenza"));
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      const r = await api("/portfolio/live/stop", { method: "POST" });
      toast(t("Portafoglio reale spento; ordini in attesa annullati ({0})", r.cancelled));
      refresh();
    } catch (e) { toast(e.message, { error: true }); stopBtn.disabled = false; }
  });

  const exp = live.exposure || { today: 0, open: 0 };
  const facts = h("dl", { class: "facts" },
    h("dt", {}, t("Indirizzo")), h("dd", { title: live.address || "" }, shortAddr(live.address)),
    h("dt", {}, t("Saldo USDC")), h("dd", {}, live.balance != null ? money(live.balance) : "–"),
    h("dt", {}, t("Acquisti di oggi")), h("dd", {}, t("{0} di {1}", money(exp.today), money(live.limits.daily))),
    h("dt", {}, t("In posizioni e ordini")), h("dd", {}, t("{0} di {1}", money(exp.open), money(live.limits.open))),
    h("dt", {}, t("Massimo per ordine")), h("dd", {}, money(live.limits.order)));

  const positions = live.positions || [];
  const posTable = positions.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, t("Mercato")), h("th", {}, t("Lato")), h("th", { class: "num" }, t("Quote")),
      h("th", { class: "num" }, t("Prezzo medio")), h("th", { class: "num" }, t("Costo")), h("th", { class: "num" }, t("In attesa")), h("th", {}, t("Stato")))),
    h("tbody", {}, positions.map((p) => h("tr", {},
      h("td", { class: "q-cell" }, h("a", { href: `#/mercati/${encodeURIComponent(p.market_id)}` }, p.question)), h("td", {}, sideBadge(p.side)),
      h("td", { class: "num" }, fmt.shares(p.shares)), h("td", { class: "num" }, p.avg_price != null ? fmt.cents(p.avg_price) : "–"),
      h("td", { class: "num" }, money(p.cost)), h("td", { class: "num" }, p.resting ? money(p.resting) : "–"),
      h("td", {}, p.to_redeem ? h("span", { class: "badge badge-good", title: t("Mercato risolto: riscatta le quote vincenti su polymarket.com") }, t("Da riscattare"))
        : p.paper_status === "lost" ? h("span", { class: "badge badge-critical" }, t("Persa"))
          : h("span", { class: "badge badge-outline" }, t("Aperta"))),
    ))))) : null;

  const orders = (live.orders || []).slice(0, 30);
  const ordersTable = orders.length ? h("div", { class: "table-wrap" }, h("table", {},
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
    })))) : h("p", { class: "secondary" }, t("Nessun ordine reale finora."));

  return h("section", { class: "card", "aria-labelledby": "h-live" }, head,
    live.error ? h("p", { class: "note", role: "alert" }, icon("alert"), t("Portafoglio non raggiungibile: {0}", live.error)) : null,
    h("div", { class: "grid-2" }, modeArea, h("div", {}, facts, h("div", { style: { marginTop: "10px" } }, stopBtn))),
    posTable ? h("h3", { style: { margin: "16px 0 8px" } }, t("Posizioni reali")) : null, posTable,
    h("h3", { style: { margin: "16px 0 8px" } }, t("Ordini reali")), ordersTable,
    h("p", { class: "muted small", style: { marginTop: "10px" } },
      t("Le quote vincenti dei mercati risolti si riscattano su polymarket.com. Gli ordini non inviati per i limiti di sicurezza restano in elenco con il motivo.")));
}
