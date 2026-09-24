// "Come funziona": how news becomes a Polymarket signal, with the live configuration values.
import { h, fmt, GLOSSARY } from "../ui.js";
import { calibrated, explainSentence, pool } from "../explain.js";

const EXAMPLE = { model_probability: 0.8, market_probability: 0.35, evidence_strength: 0.75, model_weight: null, blended_probability: null, edge: null, signal: "BUY_YES" };

export async function viewMethod(ctx) {
  const st = ctx.status || {};
  const maxW = st.model_weight_max ?? 0.5;
  const minEdge = st.min_edge ?? 0.05;
  const minEv = st.min_evidence ?? 0.5;
  const kelly = st.kelly_fraction ?? 0.25;
  const threshold = st.market_match_threshold ?? 0.5;
  const hours = st.market_news_window_hours ?? 72;
  const maxArticles = st.market_max_articles ?? 8;

  const ex = { ...EXAMPLE };
  ex.model_weight = maxW * ex.evidence_strength;
  ex.blended_probability = pool(calibrated(ex.model_probability, st), ex.market_probability, ex.model_weight, st.blend_method);
  ex.edge = ex.blended_probability - ex.market_probability;
  const exKelly = ((ex.blended_probability - ex.market_probability) / (1 - ex.market_probability)) * kelly;

  const stage = (n, title, ...body) => h("li", { class: "stage" },
    h("span", { class: "stage-n", "aria-hidden": "true" }, String(n)),
    h("div", { class: "stage-body" }, h("h3", {}, title), ...body),
  );
  const p = (...c) => h("p", {}, ...c);
  const kv = (label, value) => h("span", { class: "kv" }, label, " ", h("b", { class: "mono" }, value));

  return h("div", { class: "method" },
    ctx.pageHead("Come funziona la valutazione",
      "Dalle notizie a un segnale su Polymarket, passo per passo. I valori evidenziati sono quelli configurati su questo server."),

    h("section", { class: "card", style: { marginBottom: "16px" } },
      h("h2", {}, "Cosa dice il prezzo di un mercato"),
      p("Su Polymarket ogni mercato è una domanda con risposta SÌ o NO, per esempio «La Fed taglierà i tassi a dicembre?». ",
        "Una quota SÌ paga 1 $ se l'evento accade e 0 se non accade. Se costa ", h("b", { class: "mono" }, "35¢"),
        ", chi compra e chi vende stanno dicendo che l'evento ha circa il ", h("b", {}, "35%"), " di probabilità."),
      p("Scommettere ha senso solo se hai motivo di pensare che la probabilità vera sia diversa dal prezzo, e di abbastanza da coprire le commissioni. ",
        "Questa app cerca questi casi nelle notizie recenti."),
    ),

    h("ol", { class: "stages" },
      stage(1, "Raccolta e classificazione delle notizie",
        p(`Le fonti attive (${fmt.int(st.sources_active ?? 0)}) vengono lette a intervalli regolari. Ogni notizia viene riassunta e classificata da Jev: `,
          "categoria, regione, autorevolezza, clickbait, se è un'opinione e quanto è ", h("b", {}, "rilevante per i mercati"), "."),
        p("Le notizie quasi identiche di testate diverse vengono raggruppate, così lo stesso fatto non conta due volte."),
      ),
      stage(2, "Collegamento notizie ↔ mercati",
        p("Per ogni mercato aperto l'app cerca le notizie recenti che parlano dello stesso argomento, in due modi combinati:"),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, "Significato:"), " somiglianza semantica tra la domanda e il titolo, o il titolo con l'inizio del testo (embedding)."),
          h("li", {}, h("b", {}, "Termini chiave:"), " nomi, sigle, numeri e parole distintive della domanda, con i sinonimi più comuni (Fed = Federal Reserve = Powell, BTC = Bitcoin, 100k = 100.000)."),
        ),
        p("Una notizia sullo stesso tema ma su un soggetto diverso (la BCE per un mercato sulla Fed) non cita nessun nome della domanda e viene scartata."),
        st.targeted_news_enabled
          ? p("Per i mercati più scambiati l'app cerca anche su Google News le notizie che contengono i termini chiave: così arrivano notizie anche su argomenti che le fonti abituali non coprono.")
          : null,
        h("p", { class: "kvs" }, kv("Pertinenza minima", fmt.pct(threshold)), kv("Finestra", `${hours} ore`), kv("Notizie per previsione", String(maxArticles))),
      ),
      stage(3, "Quali notizie legge Jev",
        p(`Delle notizie collegate Jev legge le ${maxArticles} più utili. L'utilità tiene conto di:`),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, "Pertinenza"), " con la domanda."),
          h("li", {}, h("b", {}, "Affidabilità della fonte:"), " autorevolezza, clickbait e articoli d'opinione."),
          h("li", {}, h("b", {}, "Freschezza:"), " il peso si dimezza ogni due giorni."),
          h("li", {}, h("b", {}, "Giudizio di Jev:"), " le notizie che Jev ha già giudicato non rilevanti per quel mercato vengono tolte."),
        ),
        p("Per ogni storia passa un solo articolo, con il numero di fonti che l'hanno riportata: una notizia confermata da più testate è più credibile. ",
          "Al massimo 3 articoli per fonte, così le evidenze sono varie."),
      ),
      stage(4, "Le domande a Jev",
        p("Per ogni mercato Jev riceve le regole di risoluzione, la scadenza, la data di oggi e le notizie collegate, e risponde in una sola chiamata a:"),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, "Il mercato si risolverà SÌ?"), " → una probabilità (la stima di Jev)."),
          h("li", {}, h("b", {}, "Quanto le notizie informano l'esito?"), " → la forza delle evidenze, da 0% a 100%."),
          h("li", {}, h("b", {}, "Per ogni notizia:"), " è rilevante? favorisce il SÌ, il NO o è neutra?"),
        ),
        p(h("b", {}, "Jev non vede il prezzo di mercato."), " Se lo vedesse tenderebbe a copiarlo, e la sua stima non direbbe nulla di nuovo."),
      ),
      stage(5, "Probabilità finale (blended)",
        p("Un mercato liquido aggrega le opinioni di molte persone e di solito è ben calibrato. Per questo la stima di Jev non sostituisce il prezzo: ",
          "si fa una media pesata, e il peso di Jev cresce solo quando le notizie sono forti."),
        p(h("b", {}, "Calibrazione."), " Prima, la stima di Jev viene corretta con quanto si è visto sui mercati già risolti: se Jev è stato troppo sicuro di sé la sua stima viene ammorbidita, se è stato troppo prudente viene resa più netta (scala di Platt, stimata dal backtest). ",
          (st.jev_calib_a ?? 0) === 0 && (st.jev_calib_b ?? 1) === 1 ? "Per ora nessuna correzione è attiva." : `Correzione attiva: a ${fmt.num3(st.jev_calib_a)}, b ${fmt.num3(st.jev_calib_b)}.`),
        h("p", { class: "formula" }, "logit(Jev corretto) = a + b × logit(stima Jev)"),
        h("p", { class: "formula" }, `w = ${fmt.pct(maxW)} × forza delle evidenze`),
        st.blend_method === "linear"
          ? h("p", { class: "formula" }, "blended = w × Jev corretto + (1 − w) × prezzo")
          : h("p", { class: "formula" }, "logit(blended) = w × logit(Jev corretto) + (1 − w) × logit(prezzo)"),
        st.blend_method === "linear" ? null : p("La media si fa sulle quote logaritmiche (logit p = ln(p / (1 − p))): è il modo standard di unire previsioni ben calibrate. Una media semplice le rende troppo timide."),
        (st.jev_samples ?? 1) > 1 ? p(`Ogni previsione chiede a Jev ${st.jev_samples} volte e fa la media delle risposte: meno rumore, ma ogni chiamata si paga.`) : null,
        p("Con evidenze deboli la probabilità finale resta vicina al prezzo; con evidenze decisive Jev può pesare al massimo il ", h("b", {}, fmt.pct(maxW)), "."),
      ),
      stage(6, "Edge e soglie",
        h("p", { class: "formula" }, "edge = blended − prezzo"),
        p("Un segnale compare solo se ", h("b", {}, `|edge| ≥ ${Math.round(minEdge * 100)} punti`), " e ",
          h("b", {}, `forza delle evidenze ≥ ${fmt.pct(minEv)}`), ". Edge positivo: ", h("b", {}, "Compra SÌ"), ". Edge negativo: ", h("b", {}, "Compra NO"),
          ". Altrimenti: ", h("b", {}, "Attendi"), "."),
      ),
      stage(7, "Quanto puntare",
        p("La puntata segue il criterio di Kelly, che massimizza la crescita del capitale nel lungo periodo se le probabilità sono giuste. ",
          "Poiché le probabilità sono stime, si usa solo una frazione: ", h("b", {}, `${String(kelly).replace(".", ",")} × Kelly`), "."),
        h("p", { class: "formula" }, "Kelly (SÌ) = (blended − prezzo) / (1 − prezzo)"),
      ),
      stage(8, "Conviene davvero? La valutazione economica",
        p("Un edge sulla carta non basta. Prima di suggerire una puntata l'app controlla:"),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, "Prezzo reale:"), " legge il book di Polymarket e calcola il prezzo medio che pagheresti per quella cifra, commissioni incluse. Lo spread spesso si mangia l'edge."),
          h("li", {}, h("b", {}, "Incertezza:"), " la probabilità viene abbassata di z volte il suo errore tipico (più notizie forti, meno errore). Le decisioni usano questa probabilità prudente."),
          h("li", {}, h("b", {}, "Tempo:"), " il rendimento atteso viene annualizzato e confrontato con una soglia (tasso senza rischio + premio). Pochi punti su un mercato che si chiude tra un anno non valgono i soldi bloccati."),
          h("li", {}, h("b", {}, "Quanto puntare:"), " Kelly calcolato sul book (puntare di più peggiora il prezzo), una frazione di Kelly, poi i limiti per mercato, evento, categoria, totale investito e profondità del book."),
        ),
        p("I tre preset (prudente, bilanciato, aggressivo) cambiano queste soglie. Il verdetto è ",
          h("b", {}, "Conviene"), ", ", h("b", {}, "Conviene poco"), " (puntata ridotta dai limiti) o ", h("b", {}, "Non conviene"), ", sempre con i motivi."),
        p("Ogni scommessa che conviene entra automaticamente nel ", h("a", { href: "#/portafoglio" }, "portafoglio simulato"),
          ", al prezzo reale del momento, e si chiude quando il mercato si risolve. Puoi escludere singole scommesse, mercati, eventi o categorie."),
      ),
      stage(9, "Verifica sui risultati",
        p("Quando un mercato si risolve, la pagina ", h("a", { href: "#/calibrazione" }, "Calibrazione"),
          " confronta l'errore (Brier score) delle previsioni con quello del prezzo; il ", h("a", { href: "#/portafoglio" }, "portafoglio simulato"),
          " mostra se, dopo costi e limiti, i segnali avrebbero fatto guadagnare. Se su molti mercati non battono il prezzo, i segnali non vanno seguiti con soldi veri."),
      ),
    ),

    h("section", { class: "card", style: { margin: "16px 0" }, "aria-labelledby": "h-example" },
      h("h2", { id: "h-example" }, "Esempio con numeri"),
      h("p", { class: "muted small", style: { marginBottom: "10px" } }, "Numeri inventati per illustrare il calcolo, con i parametri di questo server."),
      h("div", { class: "example-grid" },
        kv("Prezzo SÌ", fmt.cents(ex.market_probability)),
        kv("Stima Jev", fmt.pct(ex.model_probability)),
        kv("Evidenze", fmt.pct(ex.evidence_strength)),
        kv("Peso w", fmt.pct(ex.model_weight)),
        kv("Blended", fmt.pct(ex.blended_probability)),
        kv("Edge", fmt.pts(ex.edge)),
        kv("Puntata", ex.edge >= minEdge && ex.evidence_strength >= minEv ? fmt.pct(exKelly) : "–"),
      ),
      h("p", { class: "secondary", style: { marginTop: "12px" } }, explainSentence({ ...ex, signal: ex.edge >= minEdge && ex.evidence_strength >= minEv ? "BUY_YES" : "HOLD" }, st)),
    ),

    h("div", { class: "grid-2" },
      h("section", { class: "card", "aria-labelledby": "h-limits" },
        h("h2", { id: "h-limits" }, "Cosa tenere presente"),
        h("ul", { class: "bullets" },
          h("li", {}, "L'app non piazza ordini: i segnali sono indicazioni da verificare."),
          h("li", {}, "Il segnale non considera commissioni e spread: li considera la valutazione economica («Conviene?»), che decide se e quanto puntare."),
          h("li", {}, "Il prezzo cambia: una previsione vecchia di ore può non valere più. Controlla sempre il prezzo attuale."),
          h("li", {}, "Il collegamento usa i titoli: una notizia importante con un titolo diverso dalla domanda può sfuggire."),
          h("li", {}, "Jev può sbagliare, soprattutto con poche notizie o regole di risoluzione complicate. Leggi le regole del mercato."),
        ),
      ),
      h("section", { class: "card", "aria-labelledby": "h-glossary" },
        h("h2", { id: "h-glossary" }, "Glossario"),
        h("dl", { class: "glossary" },
          [["Prezzo", "price"], ["Stima Jev", "jev"], ["Forza delle evidenze", "evidence"], ["Peso w", "weight"],
            ["Blended", "blended"], ["Edge", "edge"], ["Puntata (Kelly)", "kelly"], ["Rilevanza per i mercati", "relevance"], ["Brier score", "brier"]]
            .map(([term, key]) => [h("dt", {}, term), h("dd", {}, GLOSSARY[key])]),
        ),
      ),
    ),
  );
}
