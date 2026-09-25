# Configurazione

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../configuration.md)</sub>

Tutte le variabili del file `.env`.


Tutte le variabili si impostano in `.env`. I valori segnaposto `your_...` contano come
"non configurato".

<details>
<summary><b>Docker compose</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `POSTGRES_PASSWORD` | `password` | Password del database; va scelta prima del primo avvio (solo lettere e cifre) |
| `COMPOSE_PROFILES` | – | Servizi opzionali: `tunnel` (Cloudflare Tunnel), `backup` (dump giornaliero), anche insieme: `tunnel,backup` |
| `CLOUDFLARE_TUNNEL_TOKEN` | – | Token del tunnel creato su Cloudflare |
| `BACKUP_KEEP_DAYS` | `14` | Giorni di backup tenuti in `./backups` |
| `API_BIND` | `127.0.0.1` | Indirizzo su cui risponde la porta 8000: solo questa macchina, oppure `0.0.0.0` per la rete di casa (vedi [Deploy](deploy.md#uso-in-locale-e-nella-rete-di-casa)) |

</details>

<details>
<summary><b>Database e AI</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5432/postgres` | Connessione al database |
| `SIMILARITY_THRESHOLD` | `0.92` | Similarità oltre cui due titoli sono la stessa notizia |
| `STORY_SIMILARITY_THRESHOLD` | `0.82` | Similarità di titolo + testo oltre cui due articoli raccontano la stessa storia (titoli riscritti da testate diverse) |
| `STORY_WINDOW_HOURS` | `48` | Quanto indietro si cerca la stessa storia |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Modello di embedding (384 dimensioni, multilingue: una notizia italiana trova il mercato scritto in inglese). Se lo cambi, all'avvio i vettori salvati vengono ricalcolati in background, prima le notizie più recenti |
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
| `MARKET_MATCH_THRESHOLD` | `0.5` | Pertinenza minima notizia ↔ mercato (significato + termini chiave), 0–1 |
| `MARKET_CANDIDATE_MARGIN` | `0.15` | Candidati: similarità semantica ≥ soglia − margine |
| `MARKET_MATCH_TERM_WEIGHT` | `0.3` | Peso dei termini chiave nella pertinenza |
| `MARKET_MATCH_NO_ENTITY_PENALTY` | `0.6` | Moltiplicatore se la notizia non cita nessun nome della domanda |
| `MARKET_NEWS_WINDOW_HOURS` | `168` | Solo notizie degli ultimi N ore (7 giorni: per molti mercati una settimana di contesto conta) |
| `MARKET_MAX_ARTICLES` | `8` | Notizie passate a Jev per ogni previsione |
| `EVIDENCE_HALF_LIFE_HOURS` | `48` | Il peso di una notizia si dimezza ogni N ore (minimo 25 %) |
| `EVIDENCE_MIN_JEV_RELEVANCE` | `0.25` | Notizie che Jev ha giudicato meno rilevanti di così per un mercato non gli vengono più passate |
| `TARGETED_NEWS_ENABLED` | `true` | Ricerca mirata su Google News per ogni mercato |
| `TARGETED_NEWS_MAX_MARKETS` | `25` | Mercati cercati a ogni giro (i più scambiati) |
| `TARGETED_NEWS_REFRESH_HOURS` | `6` | Ogni quanto ripetere la ricerca per lo stesso mercato |
| `TARGETED_NEWS_MAX_RESULTS` | `10` | Notizie tenute per ricerca |
| `TARGETED_NEWS_DAYS` | `7` | Solo risultati degli ultimi N giorni |
| `TARGETED_NEWS_LOCALE` | `hl=en-US&gl=US&ceid=US:en` | Lingua e paese dei risultati |
| `PREDICTION_AUTO` | `false` | Previsioni automatiche dopo ogni raccolta (ogni previsione è una chiamata a pagamento) |
| `PREDICTION_MAX_PER_RUN` | `10` | Tetto di previsioni automatiche per esecuzione |
| `MODEL_WEIGHT_MAX` | `0.5` | Peso massimo di Jev rispetto al prezzo di mercato |
| `BLEND_METHOD` | `logodds` | Come si uniscono Jev e prezzo: `logodds` o `linear` |
| `JEV_CALIB_A` / `JEV_CALIB_B` | `0` / `1` | Calibrazione di Platt della stima di Jev (si stimano col backtest) |
| `JEV_SAMPLES` | `1` | Chiamate a Jev per previsione, mediate (ognuna si paga) |
| `MIN_EDGE` | `0.05` | Edge minimo per emettere un segnale |
| `MIN_EVIDENCE` | `0.5` | Forza minima delle evidenze per emettere un segnale |
| `KELLY_FRACTION` | `0.25` | Frazione del criterio di Kelly usata per la puntata |

</details>

<details>
<summary><b>Server</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `APP_LANGUAGE` | `en` | Lingua dei testi scritti senza una richiesta della dashboard: allerte Telegram, riassunti delle notizie, note salvate. `en` o `it`. La dashboard ha il suo pulsante IT/EN |
| `INGEST_INTERVAL_MINUTES` | `60` | Intervallo della pipeline |
| `ALERT_SCAN_MINUTES` | `10` | Controllo rapido per le allerte: fonti → collegamenti → allerte, senza riassunti (`0` lo disattiva) |
| `ALERT_FOLLOWUP_MINUTES` | `5` | Ogni quanto registrare il prezzo dopo le allerte |
| `ALERT_TIMEZONE` | `Europe/Rome` | Fuso orario delle ore silenziose |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | – | Bot e chat che ricevono le notifiche |
| `PUBLIC_URL` | – | Indirizzo della dashboard, per il link nelle notifiche (es. `https://news.example.com`) |
| `AI_BATCH_SIZE` | `25` | Notizie riassunte e classificate a ogni esecuzione |
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
| `DEFAULT_FEE_BPS` | `500` | Tasso di commissione (punti base, applicato a `p × (1 − p)`) per le categorie sconosciute |
| `DEFAULT_SPREAD` | `0.02` | Spread ipotizzato quando il book non è disponibile |
| `MODEL_PSEUDO_COUNT` | `20` | Quante "osservazioni" vale una stima Jev con evidenze piene (regola l'incertezza) |
| `PAPER_BANKROLL` | `1000` | Capitale iniziale simulato (modificabile dalla dashboard) |
| `PAPER_PRESET` | `bilanciato` | Preset iniziale: `prudente`, `bilanciato`, `aggressivo` |

</details>

<details>
<summary><b>Limiti delle API AI</b></summary>

Ogni fornitore ha un limitatore lato client: distanzia le richieste, limita quelle
contemporanee e, quando l'API risponde 429, mette in pausa quel fornitore per il tempo
indicato da `Retry-After`. Imposta i valori del tuo piano (li trovi nelle console di Groq,
Google AI Studio e TypeSafe).

| Variabile | Default | Descrizione |
|---|---|---|
| `GROQ_RPM` | `20` | Richieste al minuto verso Groq |
| `GEMINI_RPM` | `10` | Richieste al minuto verso Gemini |
| `JEV_RPM` | `30` | Richieste al minuto verso TypeSafe Jev |
| `JEV_CONCURRENCY` | `2` | Chiamate Jev contemporanee |
| `OLLAMA_CONCURRENCY` | `1` | Richieste contemporanee a Ollama locale |
| `AI_MAX_WAIT_SECONDS` | `90` | Attesa massima di un lavoro in background per il suo turno |

- **Pausa di Jev:** se Jev è in pausa, la classificazione si ferma e le notizie restanti
  vengono riprese al giro successivo (non vengono declassate all'euristica). La previsione
  manuale dalla dashboard aspetta al massimo 10 secondi, poi risponde "riprova tra N secondi".
- **Pausa di Groq o Gemini:** si passa al fornitore successivo; senza nessun fornitore
  disponibile il riassunto è un estratto del testo.
- **Modelli di ragionamento:** con i modelli Groq di ragionamento (`openai/gpt-oss-20b`,
  `openai/gpt-oss-120b`) l'app chiede un ragionamento breve (`reasoning_effort=low`) e limita
  i token della risposta, per non esaurire il limite di token al minuto.

</details>

<details>
<summary><b>Accesso</b></summary>

| Variabile | Default | Descrizione |
|---|---|---|
| `SESSION_TTL_HOURS` | `168` | Durata massima di una sessione (7 giorni) |
| `SESSION_IDLE_MINUTES` | `720` | Chiusura dopo inattività (12 ore) |
| `SESSION_COOKIE_SECURE` | `auto` | `auto` = `Secure` tranne su localhost in HTTP; oppure `true` / `false`. Per aprire la dashboard in HTTP da un altro dispositivo di casa serve `false` |
| `CLIENT_IP_HEADER` | – | Header con l'IP reale del visitatore messo da un proxy fidato davanti all'app (`CF-Connecting-IP` dietro Cloudflare Tunnel). Usalo solo se l'app è raggiungibile unicamente attraverso quel proxy |
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

Il catalogo contiene 85 fonti, raggruppate per i temi scambiati su Polymarket:
- esteri e conflitti;
- politica USA ed europea, compresi sondaggi, Corte suprema ed elezioni;
- economia, con fonti primarie come Fed, BCE, Bank of England, BLS, BEA e SEC;
- crypto;
- tecnologia e AI, compresi gli annunci ufficiali di OpenAI e Google;
- scienza, spazio, salute e meteo (NASA, OMS, NOAA per gli uragani);
- sport;
- cultura e spettacolo (premi, box office, musica);
- testate italiane.

In *Impostazioni* puoi aggiungerle tutte, oppure per categoria, con «Seleziona tutte» e «tutte».
Prima di attivare molte fonti nuove prova i feed con «Prova»: gli indirizzi RSS a volte
cambiano.
