# Verificare i risultati: backtest e calibrazione

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../verification.md)</sub>

Quanto fidarsi delle previsioni, sul passato e sui mercati che si risolvono.

## Backtest

Il portafoglio simulato e le allerte misurano i risultati man mano che i mercati si
risolvono, cioè in settimane o mesi. Il backtest dà una prima risposta subito: come avrebbe
previsto Jev i mercati già risolti?

**Come funziona.**
1. **Mercati.** Prende i mercati Sì/No chiusi nel periodo scelto, i più scambiati per primi
   (Gamma API, `closed=true`).
2. **Momenti.** Per ogni mercato e ogni orizzonte (1, 7 o 30 giorni prima della chiusura)
   ricostruisce la situazione di quel momento:
   - il **prezzo di allora**, dallo storico della CLOB (`/prices-history`, chiesto a finestre di
     al massimo 14 giorni, orario e, se un mercato risolto non ha punti orari, ogni 12 ore);
   - le **notizie dei 7 giorni precedenti**, da una di due fonti (scelta nel modulo):
     - **archivio**: gli articoli che questa app aveva già salvato a quella data
       (`fetched_at ≤ momento`). Nessuna notizia successiva può entrare: è la misura onesta,
       ma copre solo il periodo in cui l'app era attiva;
     - **Google News** con i filtri `after:`/`before:`: copre qualsiasi periodo, ma i filtri
       per data lasciano passare pagine aggiornate dopo, e i risultati possono sembrare
       migliori di quanto sono. Quelle con data successiva vengono comunque scartate.

     Di base si usa l'archivio quando ha notizie per quel caso, altrimenti Google News. Ogni
     caso registra la fonte usata e i risultati dei soli casi d'archivio sono mostrati a parte.
3. **Selezione delle notizie.** Pertinenza (significato + termini chiave) e scelta delle
   migliori, come nell'app.
4. **Previsione.** Jev riceve la stessa richiesta, con «oggi» impostato a quella data e senza
   vedere il prezzo.
5. **Segnale e scommessa.** Blend, segnale e valutazione economica con il preset del
   portafoglio. Il book storico non esiste: il prezzo è quello di allora più metà dello spread
   tipico.
6. **Confronto.** L'esito reale del mercato dice chi aveva ragione.

Per gli eventi a più esiti, gli esiti mostrati a Jev sono i più probabili **secondo il prezzo di
allora** (fino a `MULTI_MAX_OUTCOMES`, tra i 30 più scambiati), come dal vivo. Se il vincitore
era poco quotato finisce negli «altri esiti»: sceglierli sapendo chi ha vinto renderebbe i
risultati migliori del vero.

**Casi saltati** (senza consumare chiamate):
- prezzo storico non disponibile;
- esito già scontato dal prezzo (sotto il 3 % o sopra il 97 %, disattivabile);
- nessuna notizia in quei giorni.

C'è un limite di chiamate a Jev per ogni backtest. Il lavoro gira in background, con
avanzamento e pulsante per fermarlo. Se il server si riavvia, il backtest risulta interrotto.

**Risultati.**
- Brier score (più basso è meglio) di prezzo, Jev e blended, in totale, per orizzonte e per
  categoria.
- **Vantaggio sul prezzo** (Brier del prezzo − Brier del blended) con l'**intervallo al 95%**,
  calcolato con un bootstrap che ricampiona i mercati interi: gli orizzonti di uno stesso
  mercato condividono l'esito e non sono casi indipendenti. Se l'intervallo comprende lo zero,
  il vantaggio può essere dovuto al caso.
- Quota di segnali giusti e scommesse simulate: profitto e ROI.
- Grafico di calibrazione (previsto contro accaduto).
- Tabella dei casi, con le notizie lette da Jev.

**Parametri suggeriti.**
- La **calibrazione di Jev** (scala di Platt: regressione logistica dell'esito su
  `logit P_jev`, con una leggera penalità verso «nessuna correzione»).
- Con Jev calibrato, il **peso massimo di Jev** che dà il Brier più basso al blended.
- Con quel peso, l'**edge minimo** che avrebbe reso di più puntando 1 $ per segnale, se ci
  sono almeno 10 scommesse.
- **Verifica fuori campione.** I mercati vengono ordinati per data di chiusura: i parametri si
  stimano sul 70% più vecchio e si provano sul 30% più recente, come si userebbero dal vivo.
  Un valore è *consigliato* solo se migliora anche lì; i valori mostrati sono poi ristimati su
  tutti i mercati. Servono almeno 10 mercati per parte, altrimenti nulla è consigliato.
- L'affidabilità dipende dai **mercati diversi** (non dai casi): *bassa* sotto 30, *media*
  sotto 100, *alta* da 100 in su.

Un admin può applicare i valori consigliati con un clic: vengono salvati nel database,
valgono per le previsioni successive e sostituiscono i valori del `.env` finché non premi
«Ripristina i valori del .env». Si possono modificare solo `MODEL_WEIGHT_MAX`, `MIN_EDGE`,
`JEV_CALIB_A` e `JEV_CALIB_B`.

**Il limite.** Jev potrebbe conoscere già l'esito di eventi passati: i mercati risolti prima
della data fino a cui arrivano le conoscenze del suo modello possono dare risultati troppo
buoni. Per una misura onesta scegli mercati chiusi dopo quella data e, quando l'archivio li
copre, le sole notizie dell'archivio.

## Calibrazione

Quando un mercato seguito si risolve, il sync lo rileva e salva l'esito.
`/predictions/calibration` confronta, sull'ultima previsione fatta per ogni mercato, il
**Brier score** (errore quadratico medio, più basso è meglio) di:

- `brier_market`: il prezzo di mercato al momento della previsione;
- `brier_model`: la stima grezza di Jev;
- `brier_blended`: la probabilità blended usata per i segnali.

Il sistema aggiunge valore solo se `brier_blended` è stabilmente **inferiore** a
`brier_market` su molti mercati. Con pochi mercati risolti il confronto non è significativo:
`gain_blended` e `gain_model` danno il vantaggio sul prezzo con l'intervallo al 95%
(bootstrap sui mercati). Se l'intervallo comprende lo zero, il vantaggio può essere dovuto al caso.

**Seconda opinione.** `second_opinion` in Calibrazione (scheda «La seconda opinione») guarda
l'ultima previsione con una seconda opinione per ogni mercato risolto: il suo Brier score
contro quello di Jev e del prezzo sugli stessi mercati, il vantaggio sul prezzo con
l'intervallo al 95% (`gain_second`), quante volte era in disaccordo con un segnale, in quante
di queste aveva ragione Jev e il risultato medio per quota delle scommesse bloccate dal
disaccordo (`blocked_result_per_share`, il lato di Jev comprato al prezzo della previsione:
negativo vuol dire che bloccarle ha fatto risparmiare). Se la seconda opinione prevede peggio
del prezzo e le scommesse bloccate avrebbero guadagnato, spegnila con
`SECOND_OPINION_ENABLED=false`.

**Prezzo di chiusura (CLV).** Il sync registra l'ultimo prezzo di ogni mercato mentre si
scambia ancora (`last_trading_price`): è la «chiusura», la stima del mercato quando tutte le
informazioni sono note. Comprare stabilmente sotto la chiusura è il segno più affidabile di un
vantaggio reale e si misura molto prima che i mercati risolti bastino per il Brier:
- `signal_clv` in Calibrazione: di quanto il prezzo si è mosso verso ogni segnale fino alla chiusura;
- nel portafoglio simulato: chiusura del lato comprato − prezzo medio pagato, per scommessa e
  in media (`clv`; `clv_open` è il movimento finora sulle aperte);
- nelle allerte: movimento dal prezzo dell'allerta alla chiusura, nella direzione consigliata.
