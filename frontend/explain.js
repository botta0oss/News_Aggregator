// Plain-language explanations of how a forecast becomes a signal, using the real numbers.
import { t } from "./i18n.js";
import { h, fmt, icon, infoTip, GLOSSARY } from "./ui.js";

const pct = (v) => fmt.pct(v);
const pts = (v) => fmt.pts(v);

/** Weight of Jev in the blend: stored with the prediction, or recomputed from the settings. */
export function weightOf(p, status) {
  if (p.model_weight != null) return p.model_weight;
  const max = status?.model_weight_max ?? 0.5;
  return Math.max(0, Math.min(1, max * p.evidence_strength));
}

const logit = (p) => { const c = Math.min(1 - 1e-4, Math.max(1e-4, p)); return Math.log(c / (1 - c)); };
const sigmoid = (x) => 1 / (1 + Math.exp(-x));

/** Jev after the Platt calibration fitted by the backtest (a = 0, b = 1: unchanged). */
export function calibrated(pModel, status) {
  const a = status?.jev_calib_a ?? 0, b = status?.jev_calib_b ?? 1;
  return a === 0 && b === 1 ? pModel : sigmoid(a + b * logit(pModel));
}

/** Same pooling as the server: log-odds (default) or linear average, weight w on Jev. */
export function pool(pModel, price, w, method) {
  if (w <= 0) return price;
  if (method === "linear") return w * pModel + (1 - w) * price;
  return sigmoid(w * logit(pModel) + (1 - w) * logit(price));
}

/** One sentence for cards: what Jev thinks, how much it counts, the resulting gap. */
export function explainSentence(p, status) {
  const w = weightOf(p, status);
  const gap = Math.abs(p.edge) * 100;
  const side = p.edge > 0 ? t("sopra") : t("sotto");
  const verdict = p.signal === "BUY_YES" ? t("Il SÌ sembra sottovalutato.")
    : p.signal === "BUY_NO" ? t("Il NO sembra sottovalutato.")
      : t("Differenza troppo piccola o evidenze troppo deboli per un segnale.");
  return t("Jev stima {0} contro un prezzo di {1}. ", pct(p.model_probability), fmt.cents(p.market_probability))
    + t("Con evidenze al {0} la stima pesa per il {1}: ", pct(p.evidence_strength), pct(w))
    + t("probabilità finale {0}, {1} punti {2} il prezzo. {3}", pct(p.blended_probability), fmt.dec(gap, 1), side, verdict);
}

function check(ok, text) {
  return h("li", { class: `check ${ok ? "ok" : "ko"}` }, icon(ok ? "check" : "x"), h("span", {}, text));
}

function step(n, title, tip, ...body) {
  return h("li", { class: "xstep" },
    h("span", { class: "xstep-n", "aria-hidden": "true" }, String(n)),
    h("div", {}, h("div", { class: "xstep-title" }, title, tip ? infoTip(tip) : null), ...body),
  );
}

const formula = (...parts) => h("p", { class: "formula" }, ...parts);

/** "Why this signal" card: the whole computation, step by step, on this prediction's numbers. */
export function explainCard(p, market, evidence, status) {
  const w = weightOf(p, status);
  const minEdge = status?.min_edge ?? 0.05;
  const minEvidence = status?.min_evidence ?? 0.5;
  const kellyScale = status?.kelly_fraction ?? 0.25;
  const maxW = status?.model_weight_max ?? 0.5;
  const q = p.market_probability;
  const b = p.blended_probability;
  const cal = p.calibrated_probability ?? null;
  const linear = (p.blend_method || "linear") === "linear";  // older forecasts were linear

  const tally = { raises_yes: 0, lowers_yes: 0, neutral: 0 };
  for (const ev of evidence || []) if (ev.impact && ev.impact in tally) tally[ev.impact] += 1;
  const judged = tally.raises_yes + tally.lowers_yes + tally.neutral;

  const edgeOk = Math.abs(p.edge) >= minEdge;
  const evOk = p.evidence_strength >= minEvidence;
  const buyYes = p.edge > 0;
  const fullKelly = p.signal === "HOLD" ? 0
    : buyYes ? Math.max(0, (b - q) / (1 - q)) : Math.max(0, (q - b) / q);

  return h("section", { class: "card explain", "aria-labelledby": "h-explain" },
    h("div", { class: "card-head" },
      h("h2", { id: "h-explain" }, t("Perché questo segnale")),
      h("a", { href: "#/metodo", class: "small" }, t("Come funziona il metodo")),
    ),
    h("p", { class: "secondary", style: { marginBottom: "14px" } }, explainSentence(p, status)),
    h("ol", { class: "xsteps" },
      step(1, t("Stima indipendente di Jev"), GLOSSARY.jev,
        h("p", {}, t("Jev ha letto le regole del mercato e {0} notizie collegate, senza vedere il prezzo: ", p.article_count),
          h("b", { class: "mono" }, pct(p.model_probability)), t(" di probabilità che si risolva SÌ.")),
        p.base_rate != null ? h("p", { class: "muted small" },
          t("Prima di leggere le notizie, il caso tipico: eventi simili accadono circa nel {0} dei casi. Jev parte da lì e se ne allontana solo con notizie forti.", pct(p.base_rate))) : null,
        p.second_opinion != null ? h("p", { class: "muted small" },
          t("Seconda opinione di {0}, con le stesse regole e notizie e senza prezzo: {1}.", (p.second_opinion_provider || "").replace(/^./, (c) => c.toUpperCase()), pct(p.second_opinion)), " ",
          p.signal === "BUY_YES" || p.signal === "BUY_NO"
            ? ((p.signal === "BUY_YES") === (p.second_opinion > p.market_probability)
              ? t("È dalla stessa parte del prezzo di Jev: si può comprare.")
              : t("È dall'altra parte del prezzo rispetto a Jev: niente acquisto."))
            : "") : null,
        judged ? h("p", { class: "muted small" },
          t("Delle notizie valutate: {0} favoriscono il SÌ, {1} il NO, {2} sono neutre.", tally.raises_yes, tally.lowers_yes, tally.neutral)) : null,
      ),
      step(2, t("Quanto contano le notizie"), GLOSSARY.weight,
        p.objective_evidence != null && p.jev_evidence_strength != null ? h("p", { class: "muted small" },
          t("Jev valuta le notizie al {0}; i fatti (fonti, età, conferme da più testate) dicono {1}. Si usa la più bassa.", pct(p.jev_evidence_strength), pct(p.objective_evidence))) : null,
        h("p", {}, t("Forza delle evidenze "), h("b", { class: "mono" }, pct(p.evidence_strength)),
          t(". Il peso di Jev è il peso massimo per la forza delle evidenze:")),
        (() => {
          // Weight reduced because Jev was far from the price (MODEL_DISAGREEMENT_LOGIT), or computed
          // with the parameters of the time: show the formula that gives the stored weight
          const base = maxW * p.evidence_strength;
          if (Math.abs(w - base) < 0.0005) return formula(t("w = {0} × {1} = ", pct(maxW), pct(p.evidence_strength)), h("b", {}, pct(w)));
          if (w < base) return h("div", {},
            formula(t("w = {0} × {1} × {2} = ", pct(maxW), pct(p.evidence_strength), fmt.dec(w / base, 2)), h("b", {}, pct(w))),
            h("p", { class: "muted small" }, t("Jev era molto lontano dal prezzo: il suo peso viene ridotto, perché su un mercato liquido le distanze più grandi sono più spesso errori di Jev che informazioni.")));
          return h("div", {}, formula(t("w = "), h("b", {}, pct(w))), h("p", { class: "muted small" }, t("Calcolato con i parametri di allora.")));
        })(),
      ),
      step(3, t("Probabilità finale (blended)"), GLOSSARY.blended,
        cal != null && Math.abs(cal - p.model_probability) >= 0.0005
          ? h("p", {}, t("Prima la stima di Jev viene corretta con la calibrazione stimata dal backtest: "),
            h("b", { class: "mono" }, `${pct(p.model_probability)} → ${pct(cal)}`), ".") : null,
        h("p", {}, t("Il resto del peso ({0}) va al prezzo di mercato, che di solito è già ben informato. ", pct(1 - w)),
          linear ? t("Media pesata delle due probabilità:") : t("Le due probabilità si uniscono in log-odds (le quote logaritmiche): una stima netta e ben motivata non viene diluita come in una media semplice.")),
        linear
          ? formula(t("{0} × {1} + {2} × {3} = ", pct(w), pct(cal ?? p.model_probability), pct(1 - w), pct(q)), h("b", {}, pct(b)))
          : formula(t("logit(blended) = {0} × logit({1}) + {2} × logit({3}) → ", pct(w), pct(cal ?? p.model_probability), pct(1 - w), pct(q)), h("b", {}, pct(b))),
      ),
      step(4, t("Edge rispetto al prezzo"), GLOSSARY.edge,
        formula(`${pct(b)} − ${pct(q)} = `, h("b", { class: p.edge > 0 ? "pos" : p.edge < 0 ? "neg" : "" }, pts(p.edge))),
      ),
      step(5, t("Controlli per emettere un segnale"), null,
        h("ul", { class: "checks" },
          check(edgeOk, t("Edge di almeno {0} punti (qui {1})", Math.round(minEdge * 100), pts(Math.abs(p.edge)).replace("+", ""))),
          check(evOk, t("Forza delle evidenze di almeno {0} (qui {1})", pct(minEvidence), pct(p.evidence_strength))),
        ),
        h("p", {}, t("Risultato: "), h("b", {}, p.signal === "BUY_YES" ? t("Compra SÌ") : p.signal === "BUY_NO" ? t("Compra NO") : t("Attendi")),
          p.signal === "HOLD" ? t(" (almeno un controllo non è superato).") : "."),
      ),
      p.signal !== "HOLD" ? step(6, t("Kelly semplice (indicativo)"), GLOSSARY.kelly,
        buyYes
          ? formula(t("Kelly = ({0} − {1}) / (100% − {2}) = {3}", pct(b), pct(q), pct(q), pct(fullKelly)))
          : formula(t("Kelly = ({0} − {1}) / {2} = {3}", pct(q), pct(b), pct(q), pct(fullKelly))),
        formula(t("× {0} (Kelly frazionario) = ", fmt.dec(kellyScale)), h("b", {}, t("{0} del capitale", pct(p.kelly_fraction)))),
        h("p", { class: "muted small" }, buyYes
          ? t("Compri quote SÌ a {0}: se il mercato si risolve SÌ ogni quota vale 1 $.", fmt.cents(q))
          : t("Compri quote NO a {0}: se il mercato si risolve NO ogni quota vale 1 $.", fmt.cents(1 - q))),
        h("p", { class: "muted small" },
          p.economics && p.economics.verdict !== "NO" && p.economics.outlay
            ? t("È un'indicazione sul prezzo di mercato. Con prezzo reale del book, commissioni, incertezza e limiti del preset la puntata era {0} al momento della previsione: è quella che conta (scheda «Conviene?»).", fmt.money(p.economics.outlay))
            : t("È un'indicazione sul prezzo di mercato: la puntata effettiva, con prezzo reale del book, commissioni, incertezza e limiti del preset, è nella scheda «Conviene?».")),
      ) : null,
    ),
    h("p", { class: "note", style: { marginTop: "14px" } }, icon("alert"),
      t("Il prezzo usato è quello al momento della previsione. Prima di agire controlla il prezzo attuale su Polymarket e le commissioni: l'edge le deve coprire.")),
  );
}
