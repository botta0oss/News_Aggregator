# API

<sub>[← Torna al README](../README.md) · [Tutta la documentazione](README.md)</sub>

Gli endpoint REST dell'app.


Documentazione interattiva completa su `/docs` (se `API_DOCS_ENABLED=true`). Tutti gli
endpoint richiedono una sessione; quelli `POST` anche l'header `X-CSRF-Token`, e quelli che
avviano lavori o chiamate a pagamento (`/ingest`, `/markets/sync`, `/markets/{id}/predict`,
`/markets/predict-all`) il ruolo `admin`.

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
| GET | `/markets` | Mercati con ultima previsione. Filtri: `q`, `only_linked`, `include_closed`. Ordinamento: `sort` = `volume`, `end_date`, `price`, `signal` (ultima previsione), `edge`, `news`, `liquidity`, `question`; `order` = `asc`/`desc` (default sensato per ogni campo, valori mancanti sempre in fondo) |
| GET | `/markets/{id}` | Notizie collegate (rilevanza e impatto) e storico previsioni |
| POST | `/markets/{id}/predict` | Previsione Jev immediata (aggiorna prima il prezzo) |
| POST | `/markets/{id}/search-news` | Ricerca mirata su Google News per questo mercato; restituisce `{query, added, linked}` (admin) |
| POST | `/markets/predict-all` | Avvia in background la previsione Jev su tutti i mercati aperti con notizie recenti. `only_new=true`: solo mai valutati o con notizie nuove; `refresh_first=false`: salta l'aggiornamento dei prezzi. `409` se è già in corso (admin) |
| GET | `/markets/predict-all` | Avanzamento (`total`, `done`, `skipped`, `failed`, `current`, `message`) e mercati valutabili `eligible: {all, new}` (admin) |
| POST | `/markets/predict-all/stop` | Interrompe dopo il mercato in corso (admin) |
| GET | `/predictions/opportunities` | Mercati con edge maggiore. Filtri: `min_edge`, `min_evidence`, `include_hold` |
| GET | `/predictions/calibration` | Brier score sui mercati risolti |
| GET | `/status` | Configurazione (Jev attivo, soglie) e contatori per la dashboard |
| GET | `/markets/{id}/economics` | Valutazione economica dal vivo dell'ultima previsione e piano (`strategy`: azione, ordini, prezzi, motivi; `preset` opzionale) |
| POST | `/markets/{id}/paper-bet` | Aggiunge subito la scommessa simulata, se conviene (admin) |

### Portafoglio simulato

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/portfolio` | Riepilogo, curva del capitale, preset disponibili |
| PUT | `/portfolio/settings` | `{preset, auto_paper, auto_sell}` (admin) |
| POST | `/portfolio/reset` | `{bankroll, preset}`: cancella le scommesse simulate e ricomincia (admin) |
| GET | `/portfolio/bets` | Scommesse: `status` = `open`, `settled`, `excluded`, `all`; quelle aperte hanno il piano d'uscita (`plan`) |
| POST | `/portfolio/bets/{id}/exclude` · `/include` | Esclude o riammette una scommessa (admin) |
| POST | `/portfolio/bets/{id}/sell` | Vende subito una scommessa aperta sul book (admin) |
| GET · POST | `/portfolio/exclusions` | Esclusioni `{kind: market/event/category, value, label}` (POST admin) |
| DELETE | `/portfolio/exclusions/{id}` | Rimuove un'esclusione (admin) |

### Backtest

| Metodo | Path | Descrizione |
|---|---|---|
| GET · POST | `/backtest/runs` | Elenco dei backtest; avvio `{resolved_after, resolved_before, max_markets, min_volume, horizons, max_calls, exclude_decided, kinds}` (`kinds`: `binary` e/o `multi`) (POST admin, `409` se uno è già in corso) |
| GET | `/backtest/runs/{id}` | Avanzamento e riepilogo (metriche, calibrazione, suggerimenti) |
| GET | `/backtest/runs/{id}/cases` | Casi: `status` = `ok`, `skipped`, `all` |
| POST | `/backtest/runs/{id}/stop` · DELETE `/backtest/runs/{id}` | Ferma o elimina (admin) |
| GET · PUT | `/backtest/parameters` | `MODEL_WEIGHT_MAX`, `MIN_EDGE`, `JEV_CALIB_A`, `JEV_CALIB_B` in uso; PUT li sostituisce (admin) |
| POST | `/backtest/parameters/reset` | Torna ai valori del `.env` (admin) |

### Uso e costi

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/usage` | Oggi rispetto ai limiti, storico per giorno, servizio e funzione (`days`, max 90), limiti e prezzi in uso |
| PUT | `/usage/settings` | Limiti giornalieri e prezzi (admin) |
| POST | `/usage/settings/reset` | Torna ai valori del `.env` (admin) |

### Mercati a più esiti

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/multi/opportunities` | Eventi con un esito sottovalutato (`min_edge`, `min_evidence`, `include_hold`), con la valutazione economica |
| GET | `/multi` | Eventi con i primi esiti e l'ultima previsione. `q` (titolo o nome di un esito), `sort` = `volume`, `edge`, `signal`, `end_date`, `news` |
| GET | `/multi/{id}` | Tutti gli esiti, notizie collegate, storico delle previsioni |
| POST | `/multi/{id}/predict` | Previsione Jev della distribuzione (admin; `422` senza notizie) |

### Allerte

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/alerts` | Allerte con notizia, prezzi dopo l'allerta e movimento a favore. `kind` = `opportunities` (default) o `all` |
| GET | `/alerts/summary` | Risultati degli ultimi `days` giorni: movimento medio e quota a favore per ogni intervallo, esito dei mercati risolti |
| GET · PUT | `/alerts/settings` | Impostazioni (PUT admin) |
| POST | `/alerts/test-telegram` | Invia un messaggio di prova (admin) |
| POST | `/alerts/run` | Controlla subito le notizie nuove (admin) |

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
