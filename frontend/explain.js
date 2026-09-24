// Plain-language explanations of how a forecast becomes a signal, using the real numbers.
import { h, fmt, icon, infoTip, GLOSSARY } from "./ui.js";

const pct = (v) => fmt.pct(v);
const pts = (v) => fmt.pts(v);

/** Weight of Jev in the blend: stored with the prediction, or recomputed from the settings. */
export function weightOf(p, status) {
  if (p.model_weight != null) return p.model_weight;
  const max = status?.model_weight_max ?? 0.5;
  return Math.max(0, Math.min(1, max * p.evidence_strength));
}

/** One sentence for cards: what Jev thinks, how much it counts, the resulting gap. */
export function explainSentence(p, status) {
  const w = weightOf(p, status);
  const gap = Math.abs(p.edge) * 100;
  const side = p.edge > 0 ? "sopra" : "sotto";
  const verdict = p.signal === "BUY_YES" ? "Il SÌ sembra sottovalutato."
    : p.signal === "BUY_NO" ? "Il NO sembra sottovalutato."
      : "Differenza troppo piccola o evidenze troppo deboli per un segnale.";
  return `Jev stima ${pct(p.model_probability)} contro un prezzo di ${fmt.cents(p.market_probability)}. `
    + `Con evidenze al ${pct(p.evidence_strength)} la stima pesa per il ${pct(w)}: `
    + `probabilità finale ${pct(p.blended_probability)}, ${gap.toFixed(1).replace(".", ",")} punti ${side} il prezzo. ${verdict}`;
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
      h("h2", { id: "h-explain" }, "Perché questo segnale"),
      h("a", { href: "#/metodo", class: "small" }, "Come funziona il metodo"),
    ),
    h("p", { class: "secondary", style: { marginBottom: "14px" } }, explainSentence(p, status)),
    h("ol", { class: "xsteps" },
      step(1, "Stima indipendente di Jev", GLOSSARY.jev,
        h("p", {}, `Jev ha letto le regole del mercato e ${p.article_count} notizie collegate, senza vedere il prezzo: `,
          h("b", { class: "mono" }, pct(p.model_probability)), " di probabilità che si risolva SÌ."),
        judged ? h("p", { class: "muted small" },
          `Delle notizie valutate: ${tally.raises_yes} favoriscono il SÌ, ${tally.lowers_yes} il NO, ${tally.neutral} sono neutre.`) : null,
      ),
      step(2, "Quanto contano le notizie", GLOSSARY.weight,
        h("p", {}, "Forza delle evidenze ", h("b", { class: "mono" }, pct(p.evidence_strength)),
          ". Il peso di Jev è il peso massimo per la forza delle evidenze:"),
        formula(`w = ${pct(maxW)} × ${pct(p.evidence_strength)} = `, h("b", {}, pct(w))),
      ),
      step(3, "Probabilità finale (blended)", GLOSSARY.blended,
        h("p", {}, `Il resto del peso (${pct(1 - w)}) va al prezzo di mercato, che di solito è già ben informato:`),
        formula(`${pct(w)} × ${pct(p.model_probability)} + ${pct(1 - w)} × ${pct(q)} = `, h("b", {}, pct(b))),
      ),
      step(4, "Edge rispetto al prezzo", GLOSSARY.edge,
        formula(`${pct(b)} − ${pct(q)} = `, h("b", { class: p.edge > 0 ? "pos" : p.edge < 0 ? "neg" : "" }, pts(p.edge))),
      ),
      step(5, "Controlli per emettere un segnale", null,
        h("ul", { class: "checks" },
          check(edgeOk, `Edge di almeno ${Math.round(minEdge * 100)} punti (qui ${pts(Math.abs(p.edge)).replace("+", "")})`),
          check(evOk, `Forza delle evidenze di almeno ${pct(minEvidence)} (qui ${pct(p.evidence_strength)})`),
        ),
        h("p", {}, "Risultato: ", h("b", {}, p.signal === "BUY_YES" ? "Compra SÌ" : p.signal === "BUY_NO" ? "Compra NO" : "Attendi"),
          p.signal === "HOLD" ? " (almeno un controllo non è superato)." : "."),
      ),
      p.signal !== "HOLD" ? step(6, "Puntata suggerita", GLOSSARY.kelly,
        buyYes
          ? formula(`Kelly = (${pct(b)} − ${pct(q)}) / (100% − ${pct(q)}) = ${pct(fullKelly)}`)
          : formula(`Kelly = (${pct(q)} − ${pct(b)}) / ${pct(q)} = ${pct(fullKelly)}`),
        formula(`× ${String(kellyScale).replace(".", ",")} (Kelly frazionario) = `, h("b", {}, `${pct(p.kelly_fraction)} del capitale`)),
        h("p", { class: "muted small" }, buyYes
          ? `Compri quote SÌ a ${fmt.cents(q)}: se il mercato si risolve SÌ ogni quota vale 1 $.`
          : `Compri quote NO a ${fmt.cents(1 - q)}: se il mercato si risolve NO ogni quota vale 1 $.`),
      ) : null,
    ),
    h("p", { class: "note", style: { marginTop: "14px" } }, icon("alert"),
      "Il prezzo usato è quello al momento della previsione. Prima di agire controlla il prezzo attuale su Polymarket e le commissioni: l'edge le deve coprire."),
  );
}
