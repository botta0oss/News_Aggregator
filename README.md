# News_Aggregator

Aggregatore di notizie RSS con deduplicazione semantica (pgvector), riassunti LLM,
classificazione/scoring con **TypeSafe Jev (System One)** e previsioni sui mercati
binari di **Polymarket** a partire dalle notizie.

## Pipeline

1. **Ingest** (`feeds.yaml`) → dedup L1 (hash URL) e L2 (embedding titolo, cluster).
2. **AI queue** → riassunto (Gemini / Groq / Ollama / estratto) + valutazione Jev:
   clickbait, autorevolezza, profondità tecnica, urgenza (`Score`) e categoria (`Choice`).
   Senza `TYPESAFE_API_KEY` si usa un'euristica locale.
3. **Mercati** → sync dei mercati Yes/No più scambiati dalla Gamma API (sola lettura),
   collegamento notizie↔mercato per similarità coseno degli embedding
   (`MARKET_MATCH_THRESHOLD`, finestra `MARKET_NEWS_WINDOW_HOURS`).
4. **Previsione Jev** (una chiamata `system_one` per mercato):
   - `resolves_yes` (`Noul`) → P(YES) stimata dal modello. **Il prezzo di mercato non è
     passato al modello**, così la stima è indipendente e confrontabile col prezzo;
   - `evidence_strength` (`Score` 0–4) → quanto le notizie informano l'esito;
   - per ogni notizia `relevant_nX` (`Noul`) e `impact_nX` (`Choice`: raises_yes / lowers_yes / neutral).
5. **Segnale**: probabilità “blended” = `w·P_jev + (1−w)·prezzo`, con
   `w = MODEL_WEIGHT_MAX · evidence_strength` (i mercati liquidi sono già ben calibrati).
   `edge = blended − prezzo`; `BUY_YES`/`BUY_NO` solo se `|edge| ≥ MIN_EDGE` e
   `evidence ≥ MIN_EVIDENCE`, con stake a Kelly frazionario (`KELLY_FRACTION`).
6. **Calibrazione**: quando un mercato si risolve, `/predictions/calibration` confronta il
   Brier score di Jev, blended e prezzo di mercato. Usalo prima di puntare soldi veri.

Le previsioni automatiche sono disattivate di default (`PREDICTION_AUTO=false`): ogni
previsione è una chiamata API a pagamento. Nessun ordine viene piazzato su Polymarket.

## API

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/articles` | notizie ordinate per score composito con pesi personalizzabili |
| GET | `/articles/{id}` | dettaglio notizia |
| GET | `/categories` | conteggio per categoria |
| POST | `/ingest` | avvia la pipeline completa |
| POST | `/markets/sync` | sync mercati + collegamento notizie (+ previsioni se `PREDICTION_AUTO`) |
| GET | `/markets` | mercati (`q`, `only_linked`, `include_closed`) con ultima previsione |
| GET | `/markets/{id}` | notizie collegate (rilevanza/impatto Jev) e storico previsioni |
| POST | `/markets/{id}/predict` | previsione Jev ora (aggiorna prima il prezzo) |
| GET | `/predictions/opportunities` | mercati con `|edge|` maggiore (`min_edge`, `min_evidence`) |
| GET | `/predictions/calibration` | Brier score su mercati risolti |

## Avvio

```bash
cp .env.example .env   # inserisci TYPESAFE_API_KEY ecc.
docker compose up --build
```

Test (richiedono un Postgres con pgvector, default `postgresql+asyncpg://postgres:password@localhost:5432/newsagg`,
configurabile con `TEST_DATABASE_URL`; TypeSafe e Polymarket sono simulati):

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest
```

`scripts/check_env.py` verifica chiavi configurate e connessione al DB.

> Nota: le nuove tabelle vengono create all'avvio (`create_all`); le tabelle esistenti non
> vengono modificate. Le previsioni non sono consulenza finanziaria.
