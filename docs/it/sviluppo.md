# Sviluppo e test

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../development.md)</sub>

Test, CI e struttura del codice.


I test richiedono un PostgreSQL con pgvector. TypeSafe e Polymarket vengono simulati a
livello HTTP, quindi non servono chiavi né rete.

```bash
pip install -r requirements.txt -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/newsagg
pytest
```

I test non usano il modello di embedding, quindi `sentence-transformers` (e PyTorch) si può
saltare se serve solo far girare i test.

**CI.** Su GitHub ogni push su `main` e ogni pull request avviano
`.github/workflows/tests.yml`:
- **backend:** tutti i test, su un Postgres con pgvector (`pgvector/pgvector:pg16`) e senza
  PyTorch;
- **frontend:** controllo di sintassi dei moduli JavaScript con `node --check`.

In CI un database non raggiungibile fa fallire i test invece di saltarli.

⚠️ Il test end-to-end **cancella e ricrea tutte le tabelle** del database indicato da
`TEST_DATABASE_URL`: usa sempre un database dedicato.

| File | Contenuto |
|---|---|
| `tests/test_pipeline.py` | Punteggio composito, euristica, valutatore Jev, riassunti |
| `tests/test_forecast.py` | Calibrazione di Platt, unione in log-odds (anche a più esiti), Kelly, segnali, Brier score |
| `tests/test_polymarket.py` | Parsing e filtri dei mercati Gamma, esito definitivo (oracolo UMA) e 50-50 |
| `tests/test_dedup.py` | Stessa storia da testate diverse raggruppata, conferme contate per testata |
| `tests/test_reembed.py` | Cambio del modello di embedding (vettori ricalcolati), affidabilità storica della testata |
| `tests/test_markets_e2e.py` | Flusso completo: raccolta → mercati → previsione → API → risoluzione |
| `tests/test_auth.py` | Password, cookie, CSRF, ruoli, limite tentativi, scadenze, logout, header di sicurezza |
| `tests/test_sources.py` | Fetcher (pulizia HTML, reindirizzamenti, blocco reti interne, limiti), catalogo, migrazioni, API delle fonti |
| `tests/test_search.py` | Ricerca su titolo, testo e riassunto, prefissi, sintassi, evidenziazioni, filtri, uso dell'indice |
| `tests/test_economics.py` | Book, commissioni `p × (1 − p)`, prezzo massimo al netto della commissione, book stimato a livelli, Kelly sul book, incertezza, annualizzazione, verdetti e limiti dei preset |
| `tests/test_markets_sort.py` | Ordinamento dei mercati per ogni campo e direzione, valori mancanti in fondo, paginazione stabile |
| `tests/test_ratelimit.py` | Limitatore (distanziamento, pausa, concorrenza), Groq sotto rate limit e con modelli di ragionamento, coda che riprende, 429 sulla previsione manuale, file senza cache |
| `tests/test_portfolio.py` | Scommesse automatiche, esclusioni, chiusura (anche 50-50), vendita automatica e a mano, piano nell'API, commissioni per categoria, prezzo di chiusura (CLV), profitti e perdite, curva, API e permessi |
| `tests/test_strategy.py` | Prezzi a cui comprare e vendere, azioni (compra, aspetta, evita, tieni, vendi), motivi, fiducia |
| `tests/test_matching.py` | Termini chiave, sinonimi, pertinenza, classifica delle evidenze, ricerca mirata su Google News |
| `tests/test_multi.py` | Eventi a più esiti: sync, distribuzione, segnale SÌ/NO, economia, allerte, arbitraggio |
| `tests/test_alerts.py` | Rilevamento, valutazione immediata, Telegram, ore silenziose, prezzi dopo l'allerta, impostazioni e permessi |
| `tests/test_backtest.py` | Pianificazione, notizie senza senno di poi (anche d'archivio), metriche, bootstrap, parametri verificati sui mercati recenti |
| `tests/test_bulk_predict.py` | «Valuta tutti con Jev»: flusso completo, attesa sui limiti di frequenza, stop dopo errori ripetuti |
| `tests/test_usage.py` | Registro delle chiamate, costi stimati, limiti giornalieri e avvisi |
| `tests/test_evidence.py` | Niente quote improbabili, tasso di base chiesto a Jev, evidenze dai fatti (la più bassa delle due), guardia sul prezzo di chiusura (categoria, pausa, ripresa) |
| `tests/test_orders.py` | Seconda opinione (lettura della risposta, provider di riserva, accordo, blocco), ordini limite maker (prezzo, esecuzione, scadenza, sostituzione, liquidità riservata, API) |
| `tests/test_shadow.py` | Scommesse ombra per filtro (filtri economici, evidenze dai fatti, guardia), chiusura e resoconto, pagella della seconda opinione, esecuzione maker dallo storico dei prezzi |
| `tests/test_i18n.py` | Testi in inglese e italiano: lingua della richiesta (`X-Lang`), `APP_LANGUAGE`, piani in inglese, ogni testo della dashboard tradotto |

## Struttura del progetto

```
backend/
├── main.py                 # app FastAPI, avvio e chiusura
├── config.py               # impostazioni da .env
├── i18n.py                 # lingua dei testi (X-Lang, APP_LANGUAGE), tr(it, en)
├── overrides.py            # parametri cambiati dalla dashboard (es. dopo un backtest)
├── auth/                   # password, sessioni, ruoli, limite tentativi, CLI utenti
├── ai/
│   ├── jev.py              # client TypeSafe condiviso
│   ├── typesafe_evaluator.py  # classificazione e punteggi delle notizie
│   ├── summarizer.py       # riassunti Gemini / Groq / Ollama
│   ├── ratelimit.py        # limiti di frequenza per servizio
│   └── usage.py            # uso e costi delle chiamate, limiti giornalieri
├── ingestor/
│   ├── fetcher.py          # download sicuro e pulizia dei feed RSS/Atom
│   ├── sources.py          # catalogo (feeds.yaml) e validazione delle fonti
│   ├── deduplicator.py     # hash URL ed embedding
│   ├── reembed.py          # ricalcolo dei vettori quando cambia il modello
│   └── scheduler.py        # pipeline periodica e riclassificazione
├── markets/
│   ├── polymarket.py       # client Gamma e CLOB (sola lettura)
│   ├── service.py          # sync, collegamento notizie, previsioni Jev
│   ├── matching.py         # termini chiave, pertinenza e classifica delle evidenze
│   ├── targeted.py         # ricerca mirata su Google News per mercato
│   ├── forecast.py         # calibrazione di Platt, unione in log-odds, edge, Kelly, Brier
│   ├── calibration.py      # risultati sui mercati risolti e prezzo di chiusura dei segnali
│   └── bulk.py             # «Valuta tutti con Jev» in background
├── betting/
│   ├── profiles.py         # preset di rischio
│   ├── fees.py             # commissioni di Polymarket per categoria
│   ├── economics.py        # valutazione economica (funzioni pure)
│   ├── strategy.py         # cosa fare: azione, ordini, prezzi, motivi (funzioni pure)
│   ├── plans.py            # dati per la strategia (posizione, notizie, book)
│   ├── clv.py              # prezzo di chiusura (closing line value)
│   └── portfolio.py        # portafoglio simulato: acquisti, vendite, chiusura
├── multi/
│   ├── service.py          # eventi a più esiti: sync, notizie, previsione della distribuzione
│   └── arbitrage.py        # guadagno certo comprando tutti i SÌ o tutti i NO
├── backtest/
│   ├── engine.py           # ricostruzione del passato: prezzo storico, notizie di allora, Jev
│   └── analysis.py         # metriche, intervalli, parametri suggeriti e loro verifica
├── alerts/
│   ├── service.py          # rilevamento, valutazione immediata, prezzi dopo l'allerta
│   └── telegram.py         # notifiche Telegram
├── db/                     # modelli SQLAlchemy, query e migrazioni idempotenti
└── api/                    # schemi e route FastAPI
frontend/                   # dashboard senza build: app.js, ui.js, charts.js, explain.js,
                            # economics.js, strategy.js, i18n.js + i18n-en.js, views/ (una per pagina)
docs/                       # documentazione (inglese; italiano in docs/it) e screenshot
scripts/
├── check_env.py            # verifica chiavi e database
└── update.sh               # aggiornamento sul server
Dockerfile · Dockerfile.cuda · docker-compose.yml · docker-compose.gpu.yml
feeds.yaml                  # catalogo delle fonti consigliate
```
