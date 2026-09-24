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
- [Dashboard](#dashboard)
- [Accesso e sicurezza](#accesso-e-sicurezza)
- [Configurazione](#configurazione)
- [Come nasce una previsione](#come-nasce-una-previsione)
- [API](#api)
- [Flusso di lavoro consigliato](#flusso-di-lavoro-consigliato)
- [Valutazione economica e portafoglio simulato](#valutazione-economica-e-portafoglio-simulato)
- [Calibrazione](#calibrazione)
- [Sviluppo e test](#sviluppo-e-test)
- [Struttura del progetto](#struttura-del-progetto)
- [Limiti noti](#limiti-noti)

---

## Cosa fa

| Fase | Descrizione |
|---|---|
| **Raccolta** | Legge le fonti attive ogni `INGEST_INTERVAL_MINUTES` (e subito all'avvio). Le fonti si gestiscono dalla dashboard; `feeds.yaml` è il catalogo delle fonti consigliate. |
| **Deduplicazione** | L1: hash dell'URL normalizzato. L2: similarità degli embedding dei titoli; le notizie quasi identiche finiscono nello stesso cluster. |
| **Riassunto** | Gemini → Groq → Ollama, con fallback a un estratto del testo. |
| **Classificazione** | Jev assegna categoria (8, allineate ai temi di Polymarket), regione, opinione o notizia, rilevanza per i mercati, clickbait, autorevolezza, profondità e urgenza. Senza chiave usa parole chiave pesate. |
| **Ricerca** | Ricerca full-text su titolo, testo e riassunto, con evidenziazione e filtri. |
| **Mercati** | Sincronizza in sola lettura i mercati Sì/No più scambiati di Polymarket. |
| **Collegamento** | Associa ogni mercato alle notizie recenti più simili (pgvector). |
| **Previsione** | Jev stima la probabilità del SÌ a partire da regole del mercato e notizie. |
| **Segnale** | Confronta la stima con il prezzo: `BUY_YES`, `BUY_NO` o `HOLD`. |
| **Valutazione economica** | Decide se conviene davvero e quanto puntare: prezzo reale dal book, commissioni, incertezza della stima, rendimento annualizzato, Kelly sul book e limiti di rischio. |
| **Portafoglio simulato** | Ogni scommessa che conviene diventa una scommessa virtuale, chiusa alla risoluzione, per misurare i risultati prima di usare soldi veri. |

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
docker compose exec api python -m backend.auth.cli create-user tuonome --role admin
```

Il compose avvia anche Postgres con pgvector. Dashboard su http://localhost:8000 (accedi
con l'utente appena creato), documentazione interattiva delle API su http://localhost:8000/docs.

### In locale

Serve un PostgreSQL con l'estensione [pgvector](https://github.com/pgvector/pgvector).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # imposta DATABASE_URL e le chiavi
python -m backend.auth.cli create-user tuonome --role admin
uvicorn backend.main:app --reload
```

`python scripts/check_env.py` controlla quali chiavi sono configurate e se il database risponde.

Le tabelle vengono create all'avvio. Quelle già esistenti **non** vengono modificate: se
cambi lo schema di una tabella esistente, aggiornala a mano.

## Dashboard

L'interfaccia web è servita dalla stessa app su `/`: HTML, CSS e JavaScript senza
dipendenze né build, nella cartella `frontend/`.

| Sezione | Cosa mostra |
|---|---|
| **Opportunità** | Mercati con segnale attivo ordinati per edge: prezzo, stima Jev e probabilità blended sulla stessa scala 0–100 %, puntata suggerita e forza delle evidenze. Filtri per edge ed evidenze minime. |
| **Mercati** | Tabella dei mercati con ricerca, prezzo in centesimi, volume, scadenza, notizie collegate e ultimo segnale. |
| **Dettaglio mercato** | Ultima previsione, pulsante per chiederne una nuova, storico (prezzo contro blended), notizie collegate con rilevanza e impatto, regole di risoluzione. |
| **Notizie** | Ricerca nelle notizie (titolo, testo, riassunto) con parole evidenziate; filtri per fonte, periodo, regione, categoria, rilevanza per i mercati, opinioni; ordinamento per pertinenza, punteggio o data. |
| **Calibrazione** | Brier score di prezzo, Jev e blended sui mercati risolti, con avviso se il campione è piccolo. |
| **Come funziona** | Il metodo passo per passo con i parametri reali del server, un esempio numerico e un glossario. |
| **Impostazioni** | Fonti: aggiungi (con prova del feed prima di salvare), modifica, attiva/disattiva, aggiorna subito, elimina; fonti consigliate dal catalogo; riclassificazione; parametri di previsione in sola lettura. |

Nel dettaglio di un mercato la scheda **Perché questo segnale** mostra il calcolo completo
sui numeri di quella previsione: stima Jev, peso, probabilità blended, edge, controlli
superati e puntata. I termini tecnici hanno una definizione al passaggio del mouse (ⓘ).

La ricerca accetta frasi tra virgolette, `-parola` per escludere e `or` per alternative;
l'ultima parola vale anche come prefisso. Il link `#/notizie?q=...` riapre la stessa ricerca.

I pulsanti in alto avviano l'aggiornamento di notizie e mercati. La pagina si aggiorna da
sola mentre il lavoro procede in background.

Scelte di design:
- **Tema scuro predefinito**, con tema chiaro dal pulsante in alto. La scelta viene ricordata.
- **Colori fissi per ogni serie in tutti i grafici:** prezzo arancio, Jev acqua, blended blu. Verde e rosso sono riservati ai segnali e sono sempre accompagnati da icona e testo.
- **Palette verificata** per il daltonismo su entrambi i temi.
- **Forme distinte:** cerchio e rombo, linea continua e tratteggiata. Ogni grafico ha legenda con valori e tabella alternativa.
- **Numeri in carattere monospazio** (Fira Code) e prezzi in centesimi, come su Polymarket.
- **Accessibilità:** navigabile da tastiera, target touch di 44 px, rispetta la riduzione del movimento, nessuno scroll orizzontale da 375 px in su.

## Accesso e sicurezza

Tutta l'API, tranne il login, richiede un utente autenticato. I file statici della
dashboard sono pubblici ma non contengono dati.

**Ruoli**

| Ruolo | Può fare |
|---|---|
| `admin` | Tutto: consultare i dati, aggiornare notizie e mercati, chiedere previsioni a Jev (a pagamento) |
| `viewer` | Consultare notizie, mercati, previsioni e calibrazione |

**Gestione utenti** (non c'è registrazione pubblica):

```bash
python -m backend.auth.cli create-user alice --role admin   # chiede la password
python -m backend.auth.cli create-user bob                   # viewer
python -m backend.auth.cli list-users
python -m backend.auth.cli set-password alice                # chiude anche tutte le sue sessioni
python -m backend.auth.cli disable bob                       # disattiva e disconnette
python -m backend.auth.cli enable bob
python -m backend.auth.cli revoke-sessions alice
```

In alternativa, al primo avvio senza utenti viene creato un admin da `ADMIN_USERNAME` e
`ADMIN_PASSWORD`. Dopo il primo avvio togli `ADMIN_PASSWORD` dal file `.env`.

**Come funziona**

- **Password:** hash Argon2id, minimo 12 caratteri, non possono contenere lo username.
  Gli hash con parametri vecchi vengono aggiornati al login successivo.
- **Sessioni lato server:** il cookie `nm_session` contiene un token casuale di 256 bit;
  nel database c'è solo il suo hash SHA-256. Il cookie è `HttpOnly`, `SameSite=Lax` e
  `Secure` (tranne su `localhost` in HTTP).
- **Scadenza:** ogni sessione dura al massimo `SESSION_TTL_HOURS` e termina dopo
  `SESSION_IDLE_MINUTES` di inattività. Logout, cambio password e disattivazione dell'utente
  la revocano subito.
- **CSRF:** ogni richiesta che modifica dati deve inviare nell'header `X-CSRF-Token` il
  token della sessione. La dashboard lo fa da sola.
- **Tentativi di login:** dopo `LOGIN_MAX_ATTEMPTS` errori per IP e username, o
  `LOGIN_MAX_ATTEMPTS_PER_IP` per IP, il login viene bloccato per `LOGIN_WINDOW_MINUTES`.
  La risposta è la stessa sia che l'utente esista sia che no, e dura lo stesso tempo.
- **Header di sicurezza:** Content-Security-Policy senza script inline, `X-Frame-Options:
  DENY`, `nosniff`, HSTS quando la richiesta arriva in HTTPS.
- **CORS:** disattivato. La dashboard è sulla stessa origine; eventuali origini esterne
  vanno elencate in `CORS_ORIGINS` (il carattere jolly `*` viene ignorato).

**Messa online**

1. Metti l'app dietro un reverse proxy con HTTPS (Caddy, nginx, Traefik).
2. Avvia uvicorn con `--proxy-headers` (già nel Dockerfile), così il limite dei tentativi
   vede il vero IP del client. Se il proxy non gira sulla stessa macchina, aggiungi
   `--forwarded-allow-ips` con il suo indirizzo.
3. Imposta `API_DOCS_ENABLED=false` per non pubblicare lo schema dell'API.
4. Il limite dei tentativi è in memoria: con più worker ognuno ha i suoi contatori. Con più
   worker aggiungi un limite anche sul proxy.

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
| `AI_BATCH_SIZE` | `100` | Notizie riassunte e classificate a ogni esecuzione |
| `FEED_TIMEOUT_SECONDS` | `20` | Tempo massimo per scaricare un feed |
| `FEED_MAX_BYTES` | `5000000` | Dimensione massima di un feed |
| `ALLOW_PRIVATE_FEEDS` | `false` | Permette feed su indirizzi interni (vedi sotto) |
| `CORS_ORIGINS` | – | Origini esterne ammesse, separate da virgola. La dashboard non ne ha bisogno |
| `API_DOCS_ENABLED` | `true` | Pubblica `/docs` e `/openapi.json`. Disattiva in produzione |

</details>

<details>
<summary><b>Valutazione economica e portafoglio simulato</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `POLYMARKET_CLOB_URL` | `https://clob.polymarket.com` | API pubblica del book (sola lettura) |
| `RISK_FREE_RATE` | `0.04` | Rendimento annuo dell'alternativa senza rischio, base della soglia di rendimento |
| `DEFAULT_FEE_BPS` | `0` | Commissione (in punti base) quando il mercato non ne dichiara una |
| `DEFAULT_SPREAD` | `0.02` | Spread ipotizzato quando il book non è disponibile |
| `MODEL_PSEUDO_COUNT` | `20` | Quante "osservazioni" vale una stima Jev con evidenze piene (regola l'incertezza) |
| `PAPER_BANKROLL` | `1000` | Capitale iniziale simulato (modificabile dalla dashboard) |
| `PAPER_PRESET` | `bilanciato` | Preset iniziale: `prudente`, `bilanciato`, `aggressivo` |

</details>

<details>
<summary><b>Accesso</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `SESSION_TTL_HOURS` | `168` | Durata massima di una sessione (7 giorni) |
| `SESSION_IDLE_MINUTES` | `720` | Chiusura dopo inattività (12 ore) |
| `SESSION_COOKIE_SECURE` | `auto` | `auto` = `Secure` tranne su localhost in HTTP; oppure `true` / `false` |
| `LOGIN_MAX_ATTEMPTS` | `5` | Tentativi falliti per IP e username prima del blocco |
| `LOGIN_MAX_ATTEMPTS_PER_IP` | `20` | Tentativi falliti per IP, qualunque username |
| `LOGIN_WINDOW_MINUTES` | `15` | Finestra e durata del blocco |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | – | Admin creato al primo avvio se non esistono utenti |

</details>

**Fonti.** Si gestiscono da *Impostazioni → Fonti*. Al primo avvio, con la tabella delle
fonti vuota, vengono aggiunte le voci di `feeds.yaml` con `active: true`; le altre compaiono
tra le fonti consigliate. Ogni fonte può avere un argomento prevalente, usato come indizio
dal classificatore. Per sicurezza il server rifiuta i feed che puntano a reti interne
(localhost, 10.x, 192.168.x, metadati cloud), anche dopo un reindirizzamento: altrimenti
chi aggiunge una fonte potrebbe far interrogare al server la rete interna. Se ti serve un
feed interno imposta `ALLOW_PRIVATE_FEEDS=true`.

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
| Puntata (Kelly semplice) | (0.519 − 0.35) / 0.65 × 0.25 ≈ **6.5 % del bankroll** |

La puntata effettiva la decide poi la [valutazione economica](#valutazione-economica-e-portafoglio-simulato), che tiene conto di prezzo reale, costi, incertezza, tempo e limiti.

## API

Documentazione interattiva completa su `/docs` (se `API_DOCS_ENABLED=true`). Tutti gli
endpoint richiedono una sessione; quelli `POST` anche l'header `X-CSRF-Token`, e quelli che
avviano lavori o chiamate a pagamento (`/ingest`, `/markets/sync`, `/markets/{id}/predict`)
il ruolo `admin`.

### Accesso

| Metodo | Path | Descrizione |
|---|---|---|
| POST | `/auth/login` | `{username, password}` → cookie di sessione, utente e token CSRF |
| POST | `/auth/logout` | Chiude la sessione corrente |
| GET | `/auth/me` | Utente corrente e token CSRF |
| POST | `/auth/password` | `{current_password, new_password}`; chiude le altre sessioni |

### Notizie

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/articles` | Notizie con ricerca, filtri e ordinamento |
| GET | `/articles/{id}` | Dettaglio di una notizia |
| GET | `/categories` | Numero di notizie per categoria |
| POST | `/ingest` | Avvia subito la pipeline completa (admin) |
| POST | `/ingest/reclassify` | Riclassifica le notizie salvate (admin, `limit` fino a 1000) |

`/articles` accetta:
- **ricerca:** `q` (testo), `scope` (`all` o `title`), `sort` (`relevance`, `score`, `recent`);
- **filtri:** `category`, `region`, `source_id`, `since_hours`, `min_market_relevance`, `hide_opinion`;
- **paginazione:** `limit`, `offset`;
- **pesi del punteggio:** `w_authority`, `w_tech`, `w_urgency`, `w_clickbait`, con le soglie `max_clickbait`, `min_authority`.

Con `q` ogni notizia ha anche `title_highlight` e `snippet`, con le parole trovate racchiuse
tra i caratteri `\u0002` e `\u0003`.

```bash
curl -b cookie.txt "localhost:8000/articles?q=fed%20rate%20cut&since_hours=72&min_market_relevance=0.5"
```

### Fonti

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/sources` | Fonti con stato dell'ultimo aggiornamento e numero di notizie |
| POST | `/sources` | Aggiunge una fonte `{name, url, category_hint, active}` (admin) |
| PATCH | `/sources/{id}` | Modifica nome, indirizzo, argomento o attivazione (admin) |
| DELETE | `/sources/{id}` | Elimina la fonte; se ha notizie serve `delete_articles=true` (admin) |
| POST | `/sources/{id}/fetch` | Scarica subito le notizie di questa fonte (admin) |
| POST | `/sources/test` | Prova un feed senza salvarlo: titolo, numero di notizie, esempi (admin) |
| GET | `/sources/catalog` | Fonti consigliate da `feeds.yaml`, con quelle già aggiunte |
| POST | `/sources/catalog` | Aggiunge fonti dal catalogo `{urls: [...]}` (admin) |

### Mercati e previsioni

| Metodo | Path | Descrizione |
|---|---|---|
| POST | `/markets/sync` | Sync mercati + collegamento notizie (+ previsioni se `PREDICTION_AUTO`) |
| GET | `/markets` | Mercati con ultima previsione. Filtri: `q`, `only_linked`, `include_closed` |
| GET | `/markets/{id}` | Notizie collegate (rilevanza e impatto) e storico previsioni |
| POST | `/markets/{id}/predict` | Previsione Jev immediata (aggiorna prima il prezzo) |
| GET | `/predictions/opportunities` | Mercati con edge maggiore. Filtri: `min_edge`, `min_evidence`, `include_hold` |
| GET | `/predictions/calibration` | Brier score sui mercati risolti |
| GET | `/status` | Configurazione (Jev attivo, soglie) e contatori per la dashboard |
| GET | `/markets/{id}/economics` | Valutazione economica dal vivo dell'ultima previsione (`preset` opzionale) |
| POST | `/markets/{id}/paper-bet` | Aggiunge subito la scommessa simulata, se conviene (admin) |

### Portafoglio simulato

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/portfolio` | Riepilogo, curva del capitale, preset disponibili |
| PUT | `/portfolio/settings` | `{preset, auto_paper}` (admin) |
| POST | `/portfolio/reset` | `{bankroll, preset}`: cancella le scommesse simulate e ricomincia (admin) |
| GET | `/portfolio/bets` | Scommesse: `status` = `open`, `settled`, `excluded`, `all` |
| POST | `/portfolio/bets/{id}/exclude` · `/include` | Esclude o riammette una scommessa (admin) |
| GET · POST | `/portfolio/exclusions` | Esclusioni `{kind: market/event/category, value, label}` (POST admin) |
| DELETE | `/portfolio/exclusions/{id}` | Rimuove un'esclusione (admin) |

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
7. Lascia lavorare il portafoglio simulato per decine di mercati risolti. Prima di usare
   soldi veri controlla che il risultato reale sia positivo e vicino a quello atteso.

## Valutazione economica e portafoglio simulato

Un edge sulla carta non basta: la valutazione economica (`backend/betting/`) decide se una
previsione conviene davvero, quanto puntare e a che prezzo massimo. Si calcola dopo ogni
previsione e, dal vivo, nella scheda **Conviene?** del dettaglio mercato.

1. **Prezzo reale.** Legge il book del lato da comprare dal CLOB di Polymarket (API
   pubblica, sola lettura) e calcola il prezzo medio che pagheresti per quella cifra.
   Commissione: `tasso × min(prezzo, 1 − prezzo)` per quota. Senza book usa prezzo medio +
   metà spread e la liquidità dichiarata, e lo segnala.
2. **Probabilità prudente.** `p_prudente = p_blended − z × σ`, dove
   `σ = w × √(p_jev (1 − p_jev) / (MODEL_PSEUDO_COUNT × evidenze + 1))`. Dopo 30 mercati
   risolti σ viene corretta con i risultati: allargata se le previsioni blended hanno fatto
   peggio del prezzo, ristretta se hanno fatto meglio.
3. **Margine netto.** `p_prudente − (prezzo + commissione)` deve superare la soglia del preset.
4. **Tempo.** Il rendimento atteso prudente viene annualizzato sui giorni che mancano alla
   scadenza e deve superare `RISK_FREE_RATE` + il premio del preset.
5. **Quanto puntare.** Il capitale di Kelly è calcolato sul book, perché comprare di più
   peggiora il prezzo. Se ne prende una frazione (in base al preset) e poi si applicano i
   limiti per mercato, evento, categoria, totale investito, liquidità disponibile e quota del
   book. Il **prezzo massimo** da pagare è `p_prudente − margine minimo`.
6. **Verdetto.** *Conviene*, *Conviene poco* (la puntata è stata ridotta a meno della metà
   dai limiti) oppure *Non conviene*, sempre con i motivi.

| Preset | Kelly | z | Margine netto | Premio annuo | Max per mercato | Max per evento | Max per categoria | Max investito | Liquidità min. | Scadenza max |
|---|---|---|---|---|---|---|---|---|---|---|
| Prudente | ×0,15 | 1,64 | 4 pt | 15% | 2% | 5% | 15% | 40% | 25.000 $ | 120 gg |
| Bilanciato | ×0,25 | 1,0 | 3 pt | 8% | 4% | 8% | 25% | 60% | 10.000 $ | 365 gg |
| Aggressivo | ×0,5 | 0,5 | 2 pt | 3% | 8% | 15% | 40% | 85% | 5.000 $ | 730 gg |

**Portafoglio simulato** (pagina *Portafoglio*):
- **Scommesse automatiche:** ogni previsione con verdetto *Conviene* o *Conviene poco* diventa una scommessa virtuale al prezzo reale del momento, al massimo una aperta per mercato.
- **Chiusura:** avviene quando il mercato si risolve; una quota vincente vale 1 $.
- **Cosa mostra:** valore attuale, profitti realizzati e latenti, percentuale di vittorie, curva del capitale e confronto tra profitto atteso e reale.
- **Esclusioni:**
  - singole scommesse, anche già chiuse (non contano nei risultati e si possono riammettere);
  - mercati, eventi o categorie, che le scommesse automatiche saltano.
- **Impostazioni:** si possono scegliere il preset, attivare o disattivare le scommesse automatiche, oppure ricominciare con un nuovo capitale.

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
| `tests/test_auth.py` | Password, cookie, CSRF, ruoli, limite tentativi, scadenze, logout, header di sicurezza |
| `tests/test_sources.py` | Fetcher (pulizia HTML, reindirizzamenti, blocco reti interne, limiti), catalogo, migrazioni, API delle fonti |
| `tests/test_search.py` | Ricerca su titolo, testo e riassunto, prefissi, sintassi, evidenziazioni, filtri, uso dell'indice |
| `tests/test_economics.py` | Book, commissioni, Kelly sul book, incertezza, annualizzazione, verdetti e limiti dei preset |
| `tests/test_portfolio.py` | Scommesse automatiche, esclusioni, chiusura, profitti e perdite, curva, API e permessi |

## Struttura del progetto

```
backend/
├── main.py                 # app FastAPI, avvio e chiusura
├── config.py               # impostazioni da .env
├── auth/                   # password, sessioni, ruoli, CLI utenti
├── ai/
│   ├── jev.py              # client TypeSafe condiviso
│   ├── typesafe_evaluator.py  # classificazione e punteggi delle notizie
│   └── summarizer.py       # riassunti Gemini / Groq / Ollama
├── ingestor/
│   ├── fetcher.py          # download sicuro e pulizia dei feed RSS/Atom
│   ├── sources.py          # catalogo (feeds.yaml) e validazione delle fonti
│   ├── deduplicator.py     # hash URL ed embedding
│   └── scheduler.py        # pipeline periodica e riclassificazione
├── markets/
│   ├── polymarket.py       # client Gamma API (sola lettura)
│   ├── service.py          # sync, collegamento notizie, previsioni Jev
│   └── forecast.py         # blending, edge, Kelly, Brier
├── betting/
│   ├── profiles.py         # preset di rischio
│   ├── economics.py        # valutazione economica (funzioni pure)
│   └── portfolio.py        # portafoglio simulato
├── db/                     # modelli SQLAlchemy, query e migrazioni idempotenti
└── api/                    # schemi e route FastAPI
frontend/                   # dashboard: app.js, ui.js, charts.js, explain.js, views/ (notizie, impostazioni, metodo)
scripts/check_env.py        # verifica chiavi e database
feeds.yaml                  # catalogo delle fonti consigliate
```

## Limiti noti

- **Solo mercati binari** Sì/No. I mercati con più esiti vengono ignorati.
- **Nessuna esecuzione di ordini**: per piazzare ordini servirebbero l'API CLOB di
  Polymarket, un wallet e la firma degli ordini.
- **Solo simulazione**: il portafoglio è virtuale. Le scommesse simulate ipotizzano di
  comprare al book del momento senza muovere il mercato oltre la profondità letta.
- **Formati dell'API CLOB e delle commissioni non verificati dal vivo**: il codice segue i
  formati documentati (`/book`, `clobTokenIds`, `takerBaseFee`); se Polymarket li cambia,
  la valutazione usa il prezzo stimato e lo segnala.
- **Collegamento per similarità dei titoli**: notizie rilevanti con titoli diversi dalla
  domanda del mercato possono sfuggire.
- **Nessun recupero password via email**: un admin reimposta la password da riga di
  comando con `set-password`.

[Polymarket]: https://polymarket.com
