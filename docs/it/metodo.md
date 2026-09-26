# Il metodo: dalle notizie alla previsione

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../method.md)</sub>

Come le notizie vengono raccolte, collegate ai mercati e trasformate in una stima di probabilità.

## La pipeline, fase per fase

| Fase | Descrizione |
|---|---|
| **Raccolta** | Legge le fonti attive ogni `INGEST_INTERVAL_MINUTES` (e subito all'avvio). Le fonti si gestiscono dalla dashboard; `feeds.yaml` è il catalogo delle fonti consigliate. |
| **Deduplicazione** | L1: hash dell'URL normalizzato. L2: titolo quasi identico, oppure titolo e testo molto simili nelle ultime 48 ore: la stessa storia riscritta da più testate finisce in un unico gruppo, con il numero di testate diverse che la riportano. |
| **Riassunto** | Gemini → Groq → Ollama, con fallback a un estratto del testo. |
| **Classificazione** | Jev assegna categoria (8, allineate ai temi di Polymarket), regione, opinione o notizia, rilevanza per i mercati, clickbait, autorevolezza, profondità e urgenza. Senza chiave usa parole chiave pesate. |
| **Ricerca** | Ricerca full-text su titolo, testo e riassunto, con evidenziazione e filtri. |
| **Mercati** | Sincronizza in sola lettura i mercati Sì/No e gli eventi a più esiti più scambiati di Polymarket. |
| **Ricerca mirata** | Per i mercati più scambiati cerca su Google News le notizie con i termini chiave della domanda, anche su temi che le fonti abituali non coprono. |
| **Collegamento** | Associa ogni mercato alle notizie recenti sullo stesso soggetto: somiglianza semantica (pgvector) più termini chiave (nomi, sigle, numeri, sinonimi). |
| **Selezione delle evidenze** | Jev legge le notizie più utili: pertinenti, di fonti affidabili, recenti, una per storia con il numero di fonti che la confermano. |
| **Previsione** | Jev stima la probabilità del SÌ a partire da regole del mercato e notizie, senza vedere il prezzo; la stima viene calibrata sui mercati già risolti. |
| **Segnale** | Unisce la stima al prezzo (in log-odds, con un peso che cresce con la forza delle notizie) e li confronta: `BUY_YES`, `BUY_NO` o `HOLD`. |
| **Valutazione economica** | Decide se conviene davvero e quanto puntare: prezzo reale dal book, commissioni, incertezza della stima, rendimento annualizzato, Kelly sul book e limiti di rischio. |
| **Strategia** | Per ogni mercato dice cosa fare (compra SÌ/NO, aspetta, evita, tieni, vendi), con quali ordini limite, a che prezzi la decisione cambierebbe e perché sì o perché no. |
| **Portafoglio simulato** | Ogni scommessa che conviene diventa una scommessa virtuale, venduta quando il piano lo dice o chiusa alla risoluzione, per misurare i risultati prima di usare soldi veri. |
| **Allerte** | Quando esce una notizia fresca e pertinente per un mercato, Jev lo valuta subito. Se conviene arriva una notifica su Telegram; poi si registra il prezzo dopo 15 minuti, 1, 6 e 24 ore per misurare se l'allerta ha anticipato il mercato. |

## Come le notizie vengono collegate ai mercati

1. **Candidati.** pgvector trova le notizie recenti simili alla domanda del mercato. Confronta sia
   il titolo sia il titolo con l'inizio del testo, perché molti titoli sono vaghi.
2. **Termini chiave.** Dalla domanda si estraggono nomi e sigle (peso 2), parole distintive e
   numeri (peso 1), anni, mesi e giorni (peso 0,5). Contano anche i sinonimi più comuni: Fed =
   Federal Reserve = Powell, BTC = Bitcoin, 100k = 100.000 = $100,000. Le sigle corte si
   confrontano rispettando le maiuscole, così «US» non corrisponde a «us».
3. **Pertinenza.** Si calcola `0,7 × somiglianza + 0,3 × termini trovati`. Se la notizia non
   cita nessun nome della domanda la pertinenza scende al 60 %: è lo stesso tema su un altro
   soggetto, per esempio una notizia sulla BCE per un mercato sulla Fed. Si tengono le notizie
   con pertinenza ≥ `MARKET_MATCH_THRESHOLD`.
4. **Utilità per Jev.** L'utilità di ogni notizia è `pertinenza × affidabilità della fonte ×
   freschezza × giudizio di Jev`.
   - L'affidabilità dipende da autorevolezza, clickbait e articoli d'opinione. L'autorevolezza
     di un singolo articolo è una stima rumorosa: per le testate con almeno 5 articoli
     classificati vale per metà la media della testata.
   - La freschezza si dimezza ogni `EVIDENCE_HALF_LIFE_HOURS`.
   - Le notizie che Jev ha già giudicato non rilevanti per quel mercato vengono tolte.

   Una **storia** raggruppa gli articoli con titolo quasi identico (`SIMILARITY_THRESHOLD`) o con
   titolo + testo molto simili nelle ultime `STORY_WINDOW_HOURS` ore
   (`STORY_SIMILARITY_THRESHOLD`: le testate riscrivono i titoli). Le conferme contano le
   testate diverse, non gli articoli: una fonte che ripete la notizia non la rende più certa.
   Jev legge le `MARKET_MAX_ARTICLES` più utili: una per storia, al massimo 3 per fonte. Per ogni
   notizia riceve età, tipo (cronaca o opinione), affidabilità della fonte e numero di testate che
   l'hanno riportata, oltre ai giorni che mancano alla scadenza del mercato.
5. **Ricerca mirata.** A ogni giro, per i `TARGETED_NEWS_MAX_MARKETS` mercati più scambiati non
   cercati nelle ultime `TARGETED_NEWS_REFRESH_HOURS` ore, i termini chiave vengono cercati su
   Google News. I risultati vengono salvati come notizie della fonte automatica «Ricerca mirata»,
   con il nome della testata originale, e seguono lo stesso percorso: deduplicazione,
   classificazione, collegamento. Si può lanciare anche a mano dal dettaglio di un mercato
   («Cerca notizie»). Se il servizio non risponde per 3 mercati di fila, la ricerca si ferma
   fino al giro successivo.

Nel dettaglio di un mercato ogni notizia mostra la pertinenza, i termini chiave trovati, il
numero di fonti che la confermano e se arriva dalla ricerca mirata.

## Come nasce una previsione

### 1. Domande a Jev

Per ogni mercato si fa **una sola** chiamata `system_one`. Lo stato contiene la domanda del
mercato, le regole di risoluzione, la data di scadenza, la data di oggi e le notizie
collegate. **Il prezzo di mercato non viene passato**, così la stima di Jev resta
indipendente e confrontabile con il prezzo.

| Domanda | Primitiva | Uso |
|---|---|---|
| `base_rate` | `Noul` | Visione esterna: quanto spesso eventi di questo tipo accadono in un periodo simile, prima di leggere le notizie |
| `resolves_yes` | `Noul` | Probabilità che il mercato si risolva SÌ, partendo dal tasso di base e spostandosi solo quanto le notizie giustificano |
| `evidence_strength` | `Score` (0–4) | Quanto le notizie informano davvero l'esito |
| `relevant_nX` | `Noul` | La notizia X è rilevante per l'esito? |
| `impact_nX` | `Choice` | La notizia X alza, abbassa o non cambia la probabilità del SÌ |

**Prima la visione esterna.** Chi prevede partendo da quanto spesso accadono eventi simili
(il tasso di base) e poi corregge per il caso specifico è meglio calibrato di chi parte dalla
storia. A Jev si chiede prima il tasso di base; il tasso di base viene salvato con la
previsione e mostrato nella spiegazione.

**Forza delle evidenze: la più bassa di due valutazioni.** La valutazione di Jev (0–4 → 0–1)
si confronta con una calcolata dai fatti: ogni notizia collegata pesa l'affidabilità della
testata × la freschezza × la pertinenza data da Jev, di più se altre testate confermano la
stessa storia (fino a 3) e 1,5× se viene da una fonte primaria (Fed, BCE, BLS, SEC,
tribunali, annunci ufficiali…). Il totale `T` diventa `T / (T + EVIDENCE_OBJECTIVE_HALF)`: col
valore di base 1,5 servono circa tre notizie fresche da testate affidabili, o una fonte
primaria confermata, per arrivare a 0,5. La previsione usa la **più bassa** delle due, così un
solo articolo non può valere come evidenza forte perché lo dice Jev. Si salvano entrambe.

### Seconda opinione

Quando un acquisto è possibile (evidenze ≥ `MIN_EVIDENCE` e Jev calibrato ad almeno `MIN_EDGE`
dal prezzo), un modello gratuito riceve lo stesso stato (domanda, regole, date, notizie, niente
prezzo) e gli si chiede, prima il tasso di base, la probabilità del SÌ: Gemini, poi Groq se
Gemini non risponde (`SECOND_OPINION_PROVIDERS`). Viene salvata con la previsione e mostrata
nella spiegazione. Per comprare deve stare **dalla stessa parte del prezzo** della previsione
(oltre di `SECOND_OPINION_MARGIN`, 0 di base): che due modelli facciano lo stesso errore è meno
probabile che lo faccia uno. Se nessun provider risponde la previsione va avanti con il solo
Jev, a meno che `SECOND_OPINION_REQUIRED=true`. Le chiamate contano nelle quote gratuite di
Gemini e Groq, condivise con i riassunti; gli eventi a più esiti e il backtest non la usano.

### 2. Dalla stima al segnale

I mercati liquidi di solito sono già ben calibrati, quindi la stima di Jev non viene usata
così com'è:

1. **Calibrazione (scala di Platt).** `logit P_cal = JEV_CALIB_A + JEV_CALIB_B × logit P_jev`,
   con `logit p = ln(p / (1 − p))`. I due numeri si stimano col backtest sui mercati risolti:
   `B < 1` ammorbidisce un Jev troppo sicuro di sé, `B > 1` rende più netto uno troppo
   prudente. Di base (0 e 1) la stima resta com'è.
2. **Unione col prezzo in log-odds**, con un peso che cresce con la forza delle evidenze:

```
d              = |logit(P_cal) − logit(prezzo)|
w              = MODEL_WEIGHT_MAX × evidence_strength × min(1, MODEL_DISAGREEMENT_LOGIT / d)
logit(blended) = w × logit(P_cal) + (1 − w) × logit(prezzo)
edge           = blended − prezzo
```

`MODEL_WEIGHT_MAX` vale 0,25 e `MODEL_DISAGREEMENT_LOGIT` 2. Il peso si riduce quando Jev è molto
lontano dal prezzo: un mercato liquido che dissente da Jev di più di 2 in log-odds (per esempio
80 % contro 35 %, o 66 % contro 5,5 %) ha ragione più spesso di Jev. Senza la riduzione le
distanze più grandi, cioè i probabili errori di Jev, diventerebbero gli edge più grandi e le
prime scommesse. Il primo portafoglio reale ha perso proprio lì (vedi
[strategia](strategia.md#cosa-è-andato-storto-nel-primo-portafoglio)).

La media in log-odds è il modo standard di unire previsioni calibrate: la media semplice
(`BLEND_METHOD=linear`, il metodo di prima) le rende sistematicamente troppo timide. Con
`w = 0` il blended è il prezzo, con `w = 1` è Jev.

Con `JEV_SAMPLES > 1` ogni previsione chiede a Jev più volte e fa la media (in log-odds)
delle risposte: meno rumore, ma ogni chiamata si paga.

- `edge ≥ MIN_EDGE` ed evidenze ≥ `MIN_EVIDENCE` → **BUY_YES**
- `edge ≤ −MIN_EDGE` ed evidenze ≥ `MIN_EVIDENCE` → **BUY_NO**
- altrimenti → **HOLD**

La puntata suggerita è Kelly frazionario: per il SÌ `(blended − prezzo) / (1 − prezzo) × KELLY_FRACTION`,
per il NO la formula simmetrica sul prezzo del NO.

### Esempio

| | Valore |
|---|---|
| Prezzo di mercato (SÌ) | 0.35 |
| Stima Jev | 0.80 |
| Forza evidenze | 3/4 → 0.75 (i fatti la valutano almeno altrettanto) |
| Distanza `d` | \|logit 0,80 − logit 0,35\| = 2,005 → riduzione 2 / 2,005 = 0,997 |
| Peso `w` | 0,25 × 0,75 × 0,997 ≈ 0,187 |
| Probabilità blended | logit⁻¹(0,187 × logit 0,80 + 0,813 × logit 0,35) ≈ **0,439** |
| Edge | **+0,089** → `BUY_YES` |
| Puntata (Kelly semplice) | (0,439 − 0,35) / 0,65 × 0,25 ≈ **3,4 % del bankroll** |

La puntata effettiva la decide poi la [valutazione economica](strategia.md#valutazione-economica-e-portafoglio-simulato), che tiene conto di prezzo reale, costi, incertezza, tempo e limiti.

## Quali mercati restano fuori

- **Mercati decisi dal prezzo di un asset** («Bitcoin sopra 84.000 $ il 24 settembre?», «ETH
  arriva a 2.800 $ questa settimana?», «L'oro tocca 3.000 $?», «Bitcoin Up or Down»): Jev legge
  notizie, non il prezzo del momento e la sua volatilità, mentre il prezzo del mercato li
  contiene già. Si riconoscono dalla domanda (un asset, un livello di prezzo e una soglia come
  sopra, sotto, tra, raggiunge, scende a: `backend/markets/kinds.py`). Con
  `EXCLUDE_PRICE_MARKETS=true` (predefinito) non ricevono scommesse, e previsioni automatiche,
  «Valuta tutti» e allerte li saltano, così non si spendono chiamate a Jev. Una previsione
  chiesta a mano parte comunque.
- **Mercati vicini alla scadenza**: sotto le ore minime del preset (72 / 24 / 12) nessuna scommessa.

## Mercati a più esiti

Molti degli eventi più scambiati su Polymarket hanno più risposte possibili, una sola delle
quali vince: «Chi vincerà le elezioni?», «Chi vincerà la Champions?». Su Polymarket ogni
esito è una quota SÌ/NO a sé; qui vengono tenuti insieme e separati dai mercati Sì/No,
nella sezione **Più esiti**, perché si leggono come una distribuzione.

- **Sincronizzazione.** Arrivano gli eventi aperti più scambiati (Gamma `/events`,
  `negRisk`, almeno 3 esiti; `MULTI_SYNC_LIMIT`). Gli esiti di questi eventi non compaiono
  più tra i mercati Sì/No, tra le opportunità e nelle allerte. Il vincitore viene rilevato
  quando l'evento si chiude.
- **Notizie.** Il collegamento funziona come per i mercati Sì/No, sul titolo dell'evento.
  Una notizia che nomina uno degli esiti (un candidato, una squadra) riceve un bonus.
- **Previsione.** Con una sola chiamata Jev dà la probabilità di ciascun esito (domanda a
  scelta multipla), senza vedere i prezzi.
  - Riceve i `MULTI_MAX_OUTCOMES` esiti più probabili (12 di default); gli altri vengono
    sommati in «Altri esiti».
  - I prezzi vengono normalizzati a 100 %, così il margine del mercato sparisce dal
    riferimento.
  - Il blend unisce le due distribuzioni in modo log-lineare (`p ∝ Jev^w × mercato^(1−w)`,
    poi normalizzato), con lo stesso peso dei mercati Sì/No.
  - L'edge si misura sul **prezzo vero** della quota SÌ, quello che si paga: normalizzare
    toglie il margine dal riferimento, non dal costo.
  - Il segnale indica l'esito più lontano dal suo prezzo, se l'edge supera `MIN_EDGE` e le
    evidenze `MIN_EVIDENCE`: **compra SÌ** se è sottovalutato, **compra NO** se è
    sopravvalutato (spesso un favorito su cui il mercato è troppo ottimista).
- **Arbitraggio.** Vince un solo esito, quindi un SÌ di ogni esito paga sempre 1 $ e un NO
  di ogni esito paga sempre N − 1 $. Se al miglior prezzo del book comprare tutto il set
  costa meno, commissioni incluse, l'evento mostra il badge «Arbitraggio» con il guadagno per
  set. Si conosce solo il primo livello del book: la quantità può essere piccola e il prezzo
  cambiare in fretta.
- **Vista.** Nell'elenco, per ogni evento, i primi esiti con la barra del prezzo e i
  marcatori di Jev (rombo) e blended (cerchio). Nel dettaglio: tutti gli esiti, la tabella,
  le notizie (con l'esito che ciascuna favorisce) e lo storico.

**Economia, portafoglio, allerte e backtest.** Per gli esiti vale tutto quello che vale per i
mercati Sì/No:
- **Valutazione economica.** Dopo ogni previsione vengono valutati i 3 esiti più lontani dal
  prezzo, sulla quota SÌ se sottovalutati e sulla quota NO se sopravvalutati: prezzo reale del book, commissioni, incertezza, rendimento annualizzato,
  Kelly e limiti del preset. Nel dettaglio dell'evento la scheda «Conviene?» permette di
  scegliere l'esito.
- **Portafoglio simulato.** L'esito migliore diventa una scommessa simulata, se conviene. Il
  limite per evento vale per tutti gli esiti insieme, così non si punta su tre candidati della
  stessa elezione oltre il rischio del preset. Esclusioni per evento e categoria e chiusura alla
  risoluzione funzionano come per i mercati Sì/No.
- **Opportunità.** Gli eventi con un esito sotto- o sopravvalutato compaiono in un blocco a
  parte, sotto i mercati Sì/No.
- **Allerte.** Una notizia fresca e pertinente collegata a un evento fa ricalcolare subito la
  distribuzione (una chiamata). Se conviene arriva una notifica «Compra SÌ su …» (o «Compra NO su …») e il prezzo
  successivo viene misurato come per i mercati Sì/No.
- **Backtest.** Scegli «Più esiti» tra i tipi di mercato. Per ogni evento risolto vengono
  ricostruiti i prezzi storici e gli esiti mostrati a Jev sono i più probabili secondo il
  prezzo di allora. Il risultato riporta:
  - il Brier a più esiti (0 = perfetto, 2 = certo e sbagliato);
  - la probabilità data al vincitore;
  - quante volte il favorito ha vinto, per Jev e per il mercato;
  - le scommesse simulate.

Tecnicamente ogni esito è anche una riga della tabella dei mercati, segnata con
`multi_event_id` e nascosta dagli elenchi Sì/No: book, valutazione economica, scommesse e
chiusura usano lo stesso codice.
