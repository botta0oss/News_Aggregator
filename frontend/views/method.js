// "Come funziona": how news becomes a Polymarket signal, with the live configuration values.
import { t } from "../i18n.js";
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
    ctx.pageHead(t("Come funziona la valutazione"),
      t("Dalle notizie a un segnale su Polymarket, passo per passo. I valori evidenziati sono quelli configurati su questo server.")),

    h("section", { class: "card", style: { marginBottom: "16px" } },
      h("h2", {}, t("Cosa dice il prezzo di un mercato")),
      p(t("Su Polymarket ogni mercato è una domanda con risposta SÌ o NO, per esempio «La Fed taglierà i tassi a dicembre?». "),
        t("Una quota SÌ paga 1 $ se l'evento accade e 0 se non accade. Se costa "), h("b", { class: "mono" }, "35¢"),
        t(", chi compra e chi vende stanno dicendo che l'evento ha circa il "), h("b", {}, "35%"), t(" di probabilità.")),
      p(t("Scommettere ha senso solo se hai motivo di pensare che la probabilità vera sia diversa dal prezzo, e di abbastanza da coprire le commissioni. "),
        t("Questa app cerca questi casi nelle notizie recenti.")),
    ),

    h("ol", { class: "stages" },
      stage(1, t("Raccolta e classificazione delle notizie"),
        p(t("Le fonti attive ({0}) vengono lette a intervalli regolari. Ogni notizia viene riassunta e classificata da Jev: ", fmt.int(st.sources_active ?? 0)),
          t("categoria, regione, autorevolezza, clickbait, se è un'opinione e quanto è "), h("b", {}, t("rilevante per i mercati")), "."),
        p(t("Le notizie quasi identiche di testate diverse vengono raggruppate, così lo stesso fatto non conta due volte.")),
      ),
      stage(2, t("Collegamento notizie ↔ mercati"),
        p(t("Per ogni mercato aperto l'app cerca le notizie recenti che parlano dello stesso argomento, in due modi combinati:")),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, t("Significato:")), t(" somiglianza semantica tra la domanda e il titolo, o il titolo con l'inizio del testo (embedding).")),
          h("li", {}, h("b", {}, t("Termini chiave:")), t(" nomi, sigle, numeri e parole distintive della domanda, con i sinonimi più comuni (Fed = Federal Reserve = Powell, BTC = Bitcoin, 100k = 100.000).")),
        ),
        p(t("Una notizia sullo stesso tema ma su un soggetto diverso (la BCE per un mercato sulla Fed) non cita nessun nome della domanda e viene scartata.")),
        st.targeted_news_enabled
          ? p(t("Per i mercati più scambiati l'app cerca anche su Google News le notizie che contengono i termini chiave: così arrivano notizie anche su argomenti che le fonti abituali non coprono."))
          : null,
        h("p", { class: "kvs" }, kv(t("Pertinenza minima"), fmt.pct(threshold)), kv(t("Finestra"), t("{0} ore", hours)), kv(t("Notizie per previsione"), String(maxArticles))),
      ),
      stage(3, t("Quali notizie legge Jev"),
        p(t("Delle notizie collegate Jev legge le {0} più utili. L'utilità tiene conto di:", maxArticles)),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, t("Pertinenza")), t(" con la domanda.")),
          h("li", {}, h("b", {}, t("Affidabilità della fonte:")), t(" autorevolezza, clickbait e articoli d'opinione.")),
          h("li", {}, h("b", {}, t("Freschezza:")), t(" il peso si dimezza ogni due giorni.")),
          h("li", {}, h("b", {}, t("Giudizio di Jev:")), t(" le notizie che Jev ha già giudicato non rilevanti per quel mercato vengono tolte.")),
        ),
        p(t("Per ogni storia passa un solo articolo, con il numero di fonti che l'hanno riportata: una notizia confermata da più testate è più credibile. "),
          t("Al massimo 3 articoli per fonte, così le evidenze sono varie.")),
      ),
      stage(4, t("Le domande a Jev"),
        p(t("Per ogni mercato Jev riceve le regole di risoluzione, la scadenza, la data di oggi e le notizie collegate, e risponde in una sola chiamata a:")),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, t("Il mercato si risolverà SÌ?")), t(" → una probabilità (la stima di Jev).")),
          h("li", {}, h("b", {}, t("Quanto le notizie informano l'esito?")), t(" → la forza delle evidenze, da 0% a 100%.")),
          h("li", {}, h("b", {}, t("Per ogni notizia:")), t(" è rilevante? favorisce il SÌ, il NO o è neutra?")),
        ),
        p(h("b", {}, t("Jev non vede il prezzo di mercato.")), t(" Se lo vedesse tenderebbe a copiarlo, e la sua stima non direbbe nulla di nuovo.")),
      ),
      stage(5, t("Probabilità finale (blended)"),
        p(t("Un mercato liquido aggrega le opinioni di molte persone e di solito è ben calibrato. Per questo la stima di Jev non sostituisce il prezzo: "),
          t("si fa una media pesata, e il peso di Jev cresce solo quando le notizie sono forti.")),
        p(h("b", {}, t("Calibrazione.")), t(" Prima, la stima di Jev viene corretta con quanto si è visto sui mercati già risolti: se Jev è stato troppo sicuro di sé la sua stima viene ammorbidita, se è stato troppo prudente viene resa più netta (scala di Platt, stimata dal backtest). "),
          (st.jev_calib_a ?? 0) === 0 && (st.jev_calib_b ?? 1) === 1 ? t("Per ora nessuna correzione è attiva.") : t("Correzione attiva: a {0}, b {1}.", fmt.num3(st.jev_calib_a), fmt.num3(st.jev_calib_b))),
        h("p", { class: "formula" }, t("logit(Jev corretto) = a + b × logit(stima Jev)")),
        h("p", { class: "formula" }, t("w = {0} × forza delle evidenze", fmt.pct(maxW))),
        st.blend_method === "linear"
          ? h("p", { class: "formula" }, t("blended = w × Jev corretto + (1 − w) × prezzo"))
          : h("p", { class: "formula" }, t("logit(blended) = w × logit(Jev corretto) + (1 − w) × logit(prezzo)")),
        st.blend_method === "linear" ? null : p(t("La media si fa sulle quote logaritmiche (logit p = ln(p / (1 − p))): è il modo standard di unire previsioni ben calibrate. Una media semplice le rende troppo timide.")),
        (st.jev_samples ?? 1) > 1 ? p(t("Ogni previsione chiede a Jev {0} volte e fa la media delle risposte: meno rumore, ma ogni chiamata si paga.", st.jev_samples)) : null,
        p(t("Con evidenze deboli la probabilità finale resta vicina al prezzo; con evidenze decisive Jev può pesare al massimo il "), h("b", {}, fmt.pct(maxW)), "."),
      ),
      stage(6, t("Edge e soglie"),
        h("p", { class: "formula" }, t("edge = blended − prezzo")),
        p(t("Un segnale compare solo se "), h("b", {}, t("|edge| ≥ {0} punti", Math.round(minEdge * 100))), " e ",
          h("b", {}, t("forza delle evidenze ≥ {0}", fmt.pct(minEv))), t(". Edge positivo: "), h("b", {}, t("Compra SÌ")), t(". Edge negativo: "), h("b", {}, t("Compra NO")),
          t(". Altrimenti: "), h("b", {}, t("Attendi")), "."),
      ),
      stage(7, t("Quanto puntare"),
        p(t("La puntata segue il criterio di Kelly, che massimizza la crescita del capitale nel lungo periodo se le probabilità sono giuste. "),
          t("Poiché le probabilità sono stime, si usa solo una frazione: "), h("b", {}, t("{0} × Kelly", fmt.dec(kelly))), "."),
        h("p", { class: "formula" }, t("Kelly (SÌ) = (blended − prezzo) / (1 − prezzo)")),
      ),
      stage(8, t("Conviene davvero? La valutazione economica"),
        p(t("Un edge sulla carta non basta. Prima di suggerire una puntata l'app controlla:")),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, t("Prezzo reale:")), t(" legge il book di Polymarket e calcola il prezzo medio che pagheresti per quella cifra, commissioni incluse. Lo spread spesso si mangia l'edge.")),
          h("li", {}, h("b", {}, t("Incertezza:")), t(" la probabilità viene abbassata di z volte il suo errore tipico (più notizie forti, meno errore). Le decisioni usano questa probabilità prudente.")),
          h("li", {}, h("b", {}, t("Tempo:")), t(" il rendimento atteso viene annualizzato e confrontato con una soglia (tasso senza rischio + premio). Pochi punti su un mercato che si chiude tra un anno non valgono i soldi bloccati.")),
          h("li", {}, h("b", {}, t("Quanto puntare:")), t(" Kelly calcolato sul book (puntare di più peggiora il prezzo), una frazione di Kelly, poi i limiti per mercato, evento, categoria, totale investito e profondità del book.")),
        ),
        p(t("I tre preset (prudente, bilanciato, aggressivo) cambiano queste soglie. Il verdetto è "),
          h("b", {}, t("Conviene")), ", ", h("b", {}, t("Conviene poco")), t(" (puntata ridotta dai limiti) o "), h("b", {}, t("Non conviene")), t(", sempre con i motivi.")),
        p(t("Ogni scommessa che conviene entra automaticamente nel "), h("a", { href: "#/portafoglio" }, t("portafoglio simulato")),
          t(", al prezzo reale del momento, e si chiude quando il mercato si risolve. Puoi escludere singole scommesse, mercati, eventi o categorie.")),
      ),
      stage(9, t("Cosa fare: comprare, aspettare, vendere"),
        p(t("La scheda «Cosa fare» del mercato trasforma tutto questo in istruzioni:")),
        h("ul", { class: "bullets" },
          h("li", {}, h("b", {}, t("Compra SÌ / NO")), t(" con un ordine limite: il prezzo massimo, le quote e la cifra. Appena comprate, un ordine limite di vendita.")),
          h("li", {}, h("b", {}, t("Aspetta")), t(" quando il vantaggio c'è ma al prezzo attuale costi e incertezza se lo mangiano: l'app indica il prezzo a cui conviene (ordine in attesa).")),
          h("li", {}, h("b", {}, t("Evita")), t(" quando il problema non è il prezzo: mercato poco liquido, scadenza troppo lontana, limiti di rischio già pieni.")),
          h("li", {}, h("b", {}, t("Nessuna azione")), t(" quando la stima è vicina al prezzo; l'app dice comunque a che prezzo comprare SÌ o NO diventerebbe conveniente.")),
          h("li", {}, h("b", {}, t("Tieni / Vendi")), t(" se hai già una posizione: si vende quando il prezzo raggiunge la stima (incassare subito rende più che aspettare la risoluzione, tenuto conto delle commissioni e del rendimento chiesto al capitale) o quando una nuova previsione gira la stima.")),
        ),
        h("p", { class: "formula" }, t("vendi se prezzo − commissione ≥ stima al prezzo attuale ÷ (1 + rendimento richiesto)^(giorni/365)")),
        p(t("Niente stop-loss fisso: se il prezzo scende ma le notizie non cambiano, la quota è ancora più conveniente. Ogni piano elenca i motivi a favore e contro (notizie, margini, tempo, risultati passati) e un livello di fiducia.")),
      ),
      stage(10, t("Verifica sui risultati"),
        p(t("Quando un mercato si risolve, la pagina "), h("a", { href: "#/calibrazione" }, t("Calibrazione")),
          t(" confronta l'errore (Brier score) delle previsioni con quello del prezzo; il "), h("a", { href: "#/portafoglio" }, t("portafoglio simulato")),
          t(" mostra se, dopo costi e limiti, i segnali avrebbero fatto guadagnare. Se su molti mercati non battono il prezzo, i segnali non vanno seguiti con soldi veri.")),
      ),
    ),

    h("section", { class: "card", style: { margin: "16px 0" }, "aria-labelledby": "h-example" },
      h("h2", { id: "h-example" }, t("Esempio con numeri")),
      h("p", { class: "muted small", style: { marginBottom: "10px" } }, t("Numeri inventati per illustrare il calcolo, con i parametri di questo server.")),
      h("div", { class: "example-grid" },
        kv(t("Prezzo SÌ"), fmt.cents(ex.market_probability)),
        kv(t("Stima Jev"), fmt.pct(ex.model_probability)),
        kv(t("Evidenze"), fmt.pct(ex.evidence_strength)),
        kv(t("Peso w"), fmt.pct(ex.model_weight)),
        kv(t("Blended"), fmt.pct(ex.blended_probability)),
        kv(t("Edge"), fmt.pts(ex.edge)),
        kv(t("Puntata"), ex.edge >= minEdge && ex.evidence_strength >= minEv ? fmt.pct(exKelly) : "–"),
      ),
      h("p", { class: "secondary", style: { marginTop: "12px" } }, explainSentence({ ...ex, signal: ex.edge >= minEdge && ex.evidence_strength >= minEv ? "BUY_YES" : "HOLD" }, st)),
    ),

    h("div", { class: "grid-2" },
      h("section", { class: "card", "aria-labelledby": "h-limits" },
        h("h2", { id: "h-limits" }, t("Cosa tenere presente")),
        h("ul", { class: "bullets" },
          h("li", {}, t("L'app non piazza ordini: i segnali sono indicazioni da verificare.")),
          h("li", {}, t("Il segnale non considera commissioni e spread: li considera la valutazione economica («Conviene?»), che decide se e quanto puntare.")),
          h("li", {}, t("Il prezzo cambia: una previsione vecchia di ore può non valere più. Controlla sempre il prezzo attuale.")),
          h("li", {}, t("Il collegamento usa i titoli: una notizia importante con un titolo diverso dalla domanda può sfuggire.")),
          h("li", {}, t("Jev può sbagliare, soprattutto con poche notizie o regole di risoluzione complicate. Leggi le regole del mercato.")),
        ),
      ),
      h("section", { class: "card", "aria-labelledby": "h-glossary" },
        h("h2", { id: "h-glossary" }, t("Glossario")),
        h("dl", { class: "glossary" },
          [[t("Prezzo"), "price"], [t("Stima Jev"), "jev"], [t("Forza delle evidenze"), "evidence"], [t("Peso w"), "weight"],
            [t("Blended"), "blended"], [t("Edge"), "edge"], [t("Puntata (Kelly)"), "kelly"], [t("Rilevanza per i mercati"), "relevance"], [t("Brier score"), "brier"]]
            .map(([term, key]) => [h("dt", {}, term), h("dd", {}, GLOSSARY[key])]),
        ),
      ),
    ),
  );
}
