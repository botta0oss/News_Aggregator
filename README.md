# News Aggregator × Polymarket

Aggregatore di notizie RSS che **classifica le notizie** con [TypeSafe Jev](https://typesafe.ai)
e le usa per **stimare la probabilità degli eventi** quotati sui mercati binari (Sì/No) di
[Polymarket], confrontando la stima con il prezzo di mercato per individuare possibili
opportunità.

> ⚠️ **Non è consulenza finanziaria.** L'app non piazza ordini: produce segnali indicativi.
> Prima di usare soldi veri verifica la calibrazione del modello su un numero adeguato di
> mercati risolti (vedi [Calibrazione](#calibrazione)).

---

## Indice

- [Cosa fa](#cosa-fa)
- [Architettura](#architettura)
- [Avvio rapido](#avvio-rapido)
- [Configurazione](#configurazione)
- [Come nasce una previsione](#come-nasce-una-previsione)
- [API](#api)
- [Flusso di lavoro consigliato](#flusso-di-lavoro-consigliato)
- [Calibrazione](#calibrazione)
- [Sviluppo e test](#sviluppo-e-test)
- [Struttura del progetto](#struttura-del-progetto)
- [Limiti noti](#limiti-noti)

---

## Cosa fa

| Fase | Descrizione |
|---|---|
| **Raccolta** | Legge i feed in `feeds.yaml` ogni `INGEST_INTERVAL_MINUTES` (e subito all'avvio). |
| **Deduplicazione** | L1: hash dell'URL normalizzato. L2: similarità degli embedding dei titoli; le notizie quasi identiche finiscono nello stesso cluster. |
| **Riassunto** | Gemini → Groq → Ollama, con fallback a un estratto del testo. |
| **Classificazione** | Jev valuta clickbait, autorevolezza, profondità tecnica, urgenza e categoria. Senza chiave usa un'euristica locale. |
| **Mercati** | Sincronizza in sola lettura i mercati Sì/No più scambiati di Polymarket. |
| **Collegamento** | Associa ogni mercato alle notizie recenti più simili (pgvector). |
| **Previsione** | Jev stima la probabilità del SÌ a partire da regole del mercato e notizie. |
| **Segnale** | Confronta la stima con il prezzo: `BUY_YES`, `BUY_NO` o `HOLD`, con puntata suggerita. |

## Architettura

```mermaid
flowchart LR
    RSS[Feed RSS] --> ING[Raccolta + dedup]
    ING --> DB[(PostgreSQL + pgvector)]
    DB --> AIQ[Riassunto + classificazione Jev]
    AIQ --> DB
    PM[Polymarket Gamma API] --> SYNC[Sync mercati]
    SYNC --> DB
    DB --> LINK[Collegamento notizie ↔ mercati]
    LINK --> PRED[Previsione Jev]
    PRED --> SIG[Edge + Kelly]
    SIG --> DB
    DB --> API[FastAPI]
```

Stack: **FastAPI**, **SQLAlchemy async + asyncpg**, **PostgreSQL + pgvector**,
**sentence-transformers** (`all-MiniLM-L6-v2`), **APScheduler**, **typesafe-sdk**.

## Avvio rapido

### Con Docker (consigliato)

```bash
cp .env.example .env        # inserisci almeno TYPESAFE_API_KEY
docker compose up --build
```

Il compose avvia anche Postgres con pgvector. API su http://localhost:8000, documentazione
interattiva su http://localhost:8000/docs.

### In locale

Serve un PostgreSQL con l'estensione [pgvector](https://github.com/pgvector/pgvector).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # imposta DATABASE_URL e le chiavi
uvicorn backend.main:app --reload
```

`python scripts/check_env.py` controlla quali chiavi sono configurate e se il database risponde.

Le tabelle vengono create all'avvio. Quelle già esistenti **non** vengono modificate: se
cambi lo schema di una tabella esistente, aggiornala a mano.

## Configurazione

Tutte le variabili si impostano in `.env`. I valori segnaposto `your_...` contano come
"non configurato".

<details>
<summary><b>Database e AI</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/postgres` | Connessione al database |
| `SIMILARITY_THRESHOLD` | `0.92` | Similarità oltre cui due titoli sono la stessa notizia |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Modello di embedding (384 dimensioni) |
| `TYPESAFE_API_KEY` | – | Chiave TypeSafe. Senza chiave niente previsioni, classificazione euristica |
| `TYPESAFE_MODEL` | `jev-latest` | Modello Jev |
| `SUMMARIZER_PROVIDER` | `auto` | `gemini`, `groq`, `ollama` o `auto` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-2.5-flash` | Google AI Studio |
| `GROQ_API_KEY` / `GROQ_MODEL` | – / `llama-3.1-8b-instant` | Groq Cloud |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:1.5b` | Ollama locale |

</details>

<details>
<summary><b>Polymarket e previsioni</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `POLYMARKET_ENABLED` | `true` | Attiva sync e collegamento dei mercati |
| `POLYMARKET_SYNC_LIMIT` | `200` | Numero massimo di mercati aperti seguiti |
| `POLYMARKET_MIN_VOLUME` | `10000` | Volume minimo (USD): esclude i mercati poco liquidi |
| `MARKET_MATCH_THRESHOLD` | `0.55` | Similarità minima notizia ↔ domanda del mercato |
| `MARKET_NEWS_WINDOW_HOURS` | `72` | Solo notizie delle ultime N ore |
| `MARKET_MAX_ARTICLES` | `8` | Notizie passate a Jev per ogni previsione |
| `PREDICTION_AUTO` | `false` | Previsioni automatiche dopo ogni raccolta (ogni previsione è una chiamata a pagamento) |
| `PREDICTION_MAX_PER_RUN` | `10` | Tetto di previsioni automatiche per esecuzione |
| `MODEL_WEIGHT_MAX` | `0.5` | Peso massimo di Jev rispetto al prezzo di mercato |
| `MIN_EDGE` | `0.05` | Edge minimo per emettere un segnale |
| `MIN_EVIDENCE` | `0.5` | Forza minima delle evidenze per emettere un segnale |
| `KELLY_FRACTION` | `0.25` | Frazione del criterio di Kelly usata per la puntata |

</details>

<details>
<summary><b>Server</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `INGEST_INTERVAL_MINUTES` | `60` | Intervallo della pipeline |
| `FRONTEND_ORIGIN` | `http://localhost:8000` | Origine consentita per CORS |

</details>

I feed si gestiscono in `feeds.yaml` (`active: false` per disattivarne uno).

## Come nasce una previsione

### 1. Domande a Jev

Per ogni mercato si fa **una sola** chiamata `system_one`. Lo stato contiene la domanda del
mercato, le regole di risoluzione, la data di scadenza, la data di oggi e le notizie
collegate. **Il prezzo di mercato non viene passato**, così la stima di Jev resta
indipendente e confrontabile con il prezzo.

| Domanda | Primitiva | Uso |
|---|---|---|
| `resolves_yes` | `Noul` | Probabilità che il mercato si risolva SÌ |
| `evidence_strength` | `Score` (0–4) | Quanto le notizie informano davvero l'esito |
| `relevant_nX` | `Noul` | La notizia X è rilevante per l'esito? |
| `impact_nX` | `Choice` | La notizia X alza, abbassa o non cambia la probabilità del SÌ |

### 2. Dalla stima al segnale

I mercati liquidi di solito sono già ben calibrati, quindi la stima di Jev non viene usata
così com'è: viene avvicinata al prezzo, tanto più quanto le evidenze sono deboli.

```
w        = MODEL_WEIGHT_MAX × evidence_strength
blended  = w × P_jev + (1 − w) × prezzo
edge     = blended − prezzo
```

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
| Forza evidenze | 3/4 → 0.75 |
| Peso `w` | 0.5 × 0.75 = 0.375 |
| Probabilità blended | 0.375 × 0.80 + 0.625 × 0.35 ≈ **0.519** |
| Edge | **+0.169** → `BUY_YES` |
| Puntata | (0.519 − 0.35) / 0.65 × 0.25 ≈ **6.5 % del bankroll** |

## API

Documentazione interattiva completa su `/docs`.

### Notizie

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/articles` | Notizie ordinate per punteggio composito |
| GET | `/articles/{id}` | Dettaglio di una notizia |
| GET | `/categories` | Numero di notizie per categoria |
| POST | `/ingest` | Avvia subito la pipeline completa |

`/articles` accetta `category`, `limit`, `offset`, i pesi `w_authority`, `w_tech`,
`w_urgency`, `w_clickbait` e le soglie `max_clickbait`, `min_authority`.

```bash
curl "localhost:8000/articles?category=Economy&max_clickbait=0.3&w_urgency=0.6"
```

### Mercati e previsioni

| Metodo | Path | Descrizione |
|---|---|---|
| POST | `/markets/sync` | Sync mercati + collegamento notizie (+ previsioni se `PREDICTION_AUTO`) |
| GET | `/markets` | Mercati con ultima previsione. Filtri: `q`, `only_linked`, `include_closed` |
| GET | `/markets/{id}` | Notizie collegate (rilevanza e impatto) e storico previsioni |
| POST | `/markets/{id}/predict` | Previsione Jev immediata (aggiorna prima il prezzo) |
| GET | `/predictions/opportunities` | Mercati con edge maggiore. Filtri: `min_edge`, `min_evidence`, `include_hold` |
| GET | `/predictions/calibration` | Brier score sui mercati risolti |

Codici di errore di `/markets/{id}/predict`: `503` chiave TypeSafe mancante, `422` nessuna
notizia collegata, `409` mercato chiuso o senza prezzo, `404` mercato sconosciuto.

Esempio di risposta di `/predictions/opportunities` (valori illustrativi):

```json
[
  {
    "market": {
      "id": "123456",
      "question": "Will the Fed cut interest rates in December 2026?",
      "url": "https://polymarket.com/event/fed-decision-in-december",
      "yes_price": 0.35,
      "linked_articles": 4
    },
    "prediction": {
      "model_probability": 0.8,
      "evidence_strength": 0.75,
      "blended_probability": 0.5188,
      "edge": 0.1688,
      "signal": "BUY_YES",
      "kelly_fraction": 0.0649,
      "article_count": 4
    }
  }
]
```

## Flusso di lavoro consigliato

1. Avvia l'app: la prima raccolta e il sync dei mercati partono subito.
2. Guarda quali mercati hanno notizie collegate:
   `GET /markets?only_linked=true`
3. Controlla che le notizie siano pertinenti: `GET /markets/{id}`. Se i collegamenti sono
   rumorosi alza `MARKET_MATCH_THRESHOLD`, se sono troppo pochi abbassala.
4. Chiedi una previsione sui mercati che ti interessano:
   `POST /markets/{id}/predict`
5. Consulta le opportunità: `GET /predictions/opportunities?min_edge=0.08`
6. Quando i risultati convincono, attiva `PREDICTION_AUTO=true`, tenendo d'occhio i costi
   con `PREDICTION_MAX_PER_RUN`.

## Calibrazione

Quando un mercato seguito si risolve, il sync lo rileva e salva l'esito.
`/predictions/calibration` confronta, sull'ultima previsione fatta per ogni mercato, il
**Brier score** (errore quadratico medio, più basso è meglio) di:

- `brier_market`: il prezzo di mercato al momento della previsione;
- `brier_model`: la stima grezza di Jev;
- `brier_blended`: la probabilità blended usata per i segnali.

Il sistema aggiunge valore solo se `brier_blended` è stabilmente **inferiore** a
`brier_market` su molti mercati. Con pochi mercati risolti il confronto non è significativo.

## Sviluppo e test

I test richiedono un PostgreSQL con pgvector. TypeSafe e Polymarket vengono simulati a
livello HTTP, quindi non servono chiavi né rete.

```bash
pip install -r requirements.txt pytest pytest-asyncio
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/newsagg
pytest
```

⚠️ Il test end-to-end **cancella e ricrea tutte le tabelle** del database indicato da
`TEST_DATABASE_URL`: usa sempre un database dedicato.

| File | Contenuto |
|---|---|
| `tests/test_pipeline.py` | Punteggio composito, euristica, valutatore Jev, riassunti |
| `tests/test_forecast.py` | Blending, Kelly, segnali, Brier score |
| `tests/test_polymarket.py` | Parsing e filtri dei mercati Gamma |
| `tests/test_markets_e2e.py` | Flusso completo: raccolta → mercati → previsione → API → risoluzione |

## Struttura del progetto

```
backend/
├── main.py                 # app FastAPI, avvio e chiusura
├── config.py               # impostazioni da .env
├── ai/
│   ├── jev.py              # client TypeSafe condiviso
│   ├── typesafe_evaluator.py  # classificazione e punteggi delle notizie
│   └── summarizer.py       # riassunti Gemini / Groq / Ollama
├── ingestor/
│   ├── fetcher.py          # lettura e pulizia dei feed RSS
│   ├── deduplicator.py     # hash URL ed embedding
│   └── scheduler.py        # pipeline periodica
├── markets/
│   ├── polymarket.py       # client Gamma API (sola lettura)
│   ├── service.py          # sync, collegamento notizie, previsioni Jev
│   └── forecast.py         # blending, edge, Kelly, Brier
├── db/                     # modelli SQLAlchemy e query
└── api/                    # schemi e route FastAPI
scripts/check_env.py        # verifica chiavi e database
feeds.yaml                  # elenco dei feed
```

## Limiti noti

- **Solo mercati binari** Sì/No. I mercati con più esiti vengono ignorati.
- **Nessuna esecuzione di ordini**: per piazzare ordini servirebbero l'API CLOB di
  Polymarket, un wallet e la firma degli ordini.
- **Commissioni e spread** non sono modellati: `MIN_EDGE` deve coprirli.
- **Collegamento per similarità dei titoli**: notizie rilevanti con titoli diversi dalla
  domanda del mercato possono sfuggire.
- **Nessun frontend** incluso: se esiste una cartella `frontend/` viene servita su `/`.

[Polymarket]: https://polymarket.com
