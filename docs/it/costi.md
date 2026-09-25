# Uso e costi delle API

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../costs.md)</sub>

Registro delle chiamate a pagamento, stima dei costi e limiti giornalieri.


Tra classificazione, riassunti, previsioni, allerte, «Valuta tutti», backtest e più esiti, le
chiamate a pagamento possono crescere in fretta. La pagina **Uso e costi** le tiene sotto
controllo.

**Cosa viene registrato.** Ogni chiamata a Jev, Groq e Gemini, con:
- la **funzione** che l'ha fatta (classificazione, riassunti, riclassificazione, previsioni,
  Valuta tutti, allerte, più esiti, backtest);
- i **token** in entrata e in uscita;
- un **costo stimato**, calcolato dai prezzi che imposti: per milione di token e, per Jev,
  anche per chiamata. Con i prezzi a 0 si contano chiamate e token, ma non il costo.

Ollama gira in locale e non viene contato.

**Limiti giornalieri.** Si impostano dalla pagina (solo admin) e sostituiscono i valori del
`.env`:
- `DAILY_JEV_CALL_LIMIT`: numero massimo di chiamate a Jev;
- `DAILY_AI_BUDGET_USD`: spesa massima stimata per tutte le API.

Il giorno si azzera a mezzanotte (fuso `ALERT_TIMEZONE`). Oltre il limite:
- previsioni, allerte, «Valuta tutti» e backtest si fermano con un messaggio chiaro;
- la classificazione passa all'euristica locale e i riassunti all'estratto del testo, così
  le notizie continuano ad arrivare;
- un banner in tutta la dashboard lo segnala.

All'80 % (`USAGE_WARN_SHARE`) e al 100 % di ogni limite arriva un messaggio Telegram, una
sola volta al giorno, se Telegram è configurato.

**Cosa mostra la pagina.**
- **Oggi:** chiamate e spesa rispetto ai limiti.
- **Ultimi 30 giorni:** grafico per giorno, per costo, chiamate o token, con il dettaglio per
  funzione al passaggio del mouse.
- **Riepilogo** per funzione e per servizio, con la quota di spesa.

Il costo è una stima: fa fede la fattura del fornitore.
