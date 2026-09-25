# Allerte notizie–prezzo

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../alerts.md)</sub>

Notifiche Telegram quando una notizia fresca muove la stima prima del prezzo.


Su Polymarket il vantaggio viene soprattutto dalla velocità: esce una notizia e il prezzo ci
mette minuti o ore ad adeguarsi. Le allerte servono a arrivare prima.

**Quando parte un'allerta.** Ogni volta che le notizie vengono collegate ai mercati:
- nella raccolta completa;
- in un controllo rapido ogni `ALERT_SCAN_MINUTES` minuti, che scarica le fonti e collega le
  notizie senza fare riassunti né classificazioni.

Per ogni collegamento nuovo l'app controlla che:
- la notizia sia fresca (età massima configurabile, 6 ore di default);
- sia pertinente (60 % di default);
- venga da una fonte affidabile e non sia un articolo d'opinione;
- riguardi una categoria seguita.

Per ogni mercato vale la notizia migliore. I mercati in pausa, cioè con un'allerta nelle
ultime 3 ore, vengono saltati.

**Cosa succede.**
1. Il prezzo viene aggiornato da Polymarket in quel momento.
2. Jev valuta il mercato: è una chiamata, contata nel limite giornaliero (30 di default).
3. La valutazione economica decide se conviene e quanto puntare. La scommessa simulata segue
   le regole del portafoglio.
4. Se conviene arriva un messaggio Telegram con la notizia, il prezzo, la stima, l'edge, la
   puntata e il prezzo massimo. Nelle ore silenziose il messaggio arriva senza suono.
   I messaggi sono scritti in `APP_LANGUAGE` (inglese di default, `it` per l'italiano).
5. Ogni `ALERT_FOLLOWUP_MINUTES` minuti viene registrato il prezzo 15 minuti, 1, 6 e 24 ore
   dopo l'allerta.

**I risultati.** Il *movimento a favore* misura di quanti punti il prezzo si è spostato
nella direzione consigliata. Se è positivo, l'allerta è arrivata prima del mercato. La
pagina *Allerte* mostra la media e la quota di allerte a favore per ogni intervallo, e
l'esito dei mercati già risolti. Vengono salvate anche le valutazioni che non convenivano
(«Tutte le valutazioni»), così si vede quanto costano le allerte rispetto a ciò che rendono.

**Configurare Telegram.**
1. Crea un bot con [@BotFather](https://t.me/BotFather) e copia il token.
2. Scrivi un messaggio al bot, poi apri `https://api.telegram.org/bot<TOKEN>/getUpdates`:
   il numero in `chat.id` è il tuo `TELEGRAM_CHAT_ID`. Per un gruppo aggiungi il bot al
   gruppo; l'id inizia con `-`.
3. Metti i due valori nel `.env`, riavvia e premi «Invia messaggio di prova» nella pagina
   *Allerte*.

Il token resta solo nel `.env`: non compare nelle API, nei log o nei messaggi d'errore.
