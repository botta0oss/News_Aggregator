# Valutazione economica, strategia e portafoglio

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../strategy.md)</sub>

Se una previsione conviene davvero, cosa fare (comprare, aspettare, vendere) e come il portafoglio simulato misura i risultati.

## Valutazione economica e portafoglio simulato

Un edge sulla carta non basta: la valutazione economica (`backend/betting/`) decide se una
previsione conviene davvero, quanto puntare e a che prezzo massimo. Si calcola dopo ogni
previsione e, dal vivo, nella scheda **Conviene?** del dettaglio mercato.

1. **Prezzo reale.** Legge il book del lato da comprare dal CLOB di Polymarket (API
   pubblica, sola lettura) e calcola il prezzo medio che pagheresti per quella cifra.
   Commissione (solo per chi compra dal book, come qui): `tasso × prezzo × (1 − prezzo)` per
   quota, massima a 50¢ e nulla agli estremi. Il tasso dipende dalla categoria (Esteri 0,
   Politica/Tecnologia/Economia 4%, Sport 3%, Cultura/Scienza 5%, Cripto 7%; categoria
   sconosciuta: `DEFAULT_FEE_BPS`). L'endpoint CLOB `/fee-rate` dice se il mercato ha le
   commissioni attive: se risponde 0 la commissione è zero. Senza book stima un book a 4
   livelli, da prezzo medio + metà spread in su, uno spread l'uno dall'altro (40/30/20/10%
   della metà della liquidità dichiarata): chi compra molto paga progressivamente di più.
   Lo segnala sempre.
2. **Probabilità prudente.** `p_prudente = p_blended − z × σ`, dove
   `σ = w × √(p_jev (1 − p_jev) / (MODEL_PSEUDO_COUNT × evidenze + 1))`. Dopo 30 mercati
   risolti σ viene moltiplicata per `√(Brier osservato / Brier atteso)`, dove il Brier atteso
   è quello che avrebbero previsioni perfettamente calibrate (media di `p (1 − p)`): allargata
   se le previsioni blended sono state troppo sicure, ristretta se sono state prudenti (fattore
   tra 0,75 e 2).
3. **Margine netto.** `p_prudente − (prezzo + commissione)` deve superare la soglia del preset.
4. **Tempo.** Il rendimento atteso prudente viene annualizzato sui giorni che mancano alla
   scadenza e deve superare `RISK_FREE_RATE` + il premio del preset.
5. **Quanto puntare.** Il capitale di Kelly è calcolato sul book, perché comprare di più
   peggiora il prezzo. Se ne prende una frazione (in base al preset) e poi si applicano i
   limiti per mercato, evento, categoria, totale investito, liquidità disponibile e quota del
   book. Il **prezzo massimo** da pagare è il prezzo a cui `prezzo + commissione` lascia
   esattamente il margine minimo: `prezzo + commissione(prezzo) = p_prudente − margine minimo`.
6. **Verdetto.** *Conviene*, *Conviene poco* (la puntata è stata ridotta a meno della metà
   dai limiti) oppure *Non conviene*, sempre con i motivi.

| Preset | Kelly | z | Margine netto | Premio annuo | Max per mercato | Max per evento | Max per categoria | Max investito | Liquidità min. | Scadenza max |
|---|---|---|---|---|---|---|---|---|---|---|
| Prudente | ×0,15 | 1,64 | 4 pt | 15% | 2% | 5% | 15% | 40% | 25.000 $ | 120 gg |
| Bilanciato | ×0,25 | 1,0 | 3 pt | 8% | 4% | 8% | 25% | 60% | 10.000 $ | 365 gg |
| Aggressivo | ×0,5 | 0,5 | 2 pt | 3% | 8% | 15% | 40% | 85% | 5.000 $ | 730 gg |

**Portafoglio simulato** (pagina *Portafoglio*):
- **Scommesse automatiche:** ogni previsione con verdetto *Conviene* o *Conviene poco* diventa una scommessa virtuale al prezzo reale del momento, al massimo una aperta per mercato.
- **Vendita:** dopo ogni aggiornamento dei mercati e ogni nuova previsione le posizioni aperte
  vengono riviste con la [strategia](#strategia-di-acquisto-e-vendita): se il piano dice
  «Vendi», le quote vengono vendute sul book (stato «venduta», con prezzo e motivo). Si può
  disattivare («Vendi automaticamente») o vendere a mano. Le vendite in guadagno contano come
  vinte.
- **Chiusura:** avviene quando il mercato si risolve in modo definitivo (prezzo a 1/0 e, se
  Gamma lo riporta, `umaResolutionStatus` = «resolved»: un esito solo proposto o contestato
  può ancora cambiare). Una quota vincente vale 1 $; se il mercato si chiude 50-50 ogni quota
  vale 0,50 $ e la scommessa risulta «annullata» (fuori dalla percentuale di vittorie).
- **Cosa mostra:** valore attuale, profitti realizzati e latenti, percentuale di vittorie, curva del capitale e confronto tra profitto atteso e reale.
- **Esclusioni:**
  - singole scommesse, anche già chiuse (non contano nei risultati e si possono riammettere);
  - mercati, eventi o categorie, che le scommesse automatiche saltano.
- **Impostazioni:** si possono scegliere il preset, attivare o disattivare le scommesse e le vendite automatiche, oppure ricominciare con un nuovo capitale.

## Strategia di acquisto e vendita

La scheda **Cosa fare** del dettaglio di un mercato (e di ogni esito dei mercati a più esiti)
trasforma previsione e valutazione economica in istruzioni (`backend/betting/strategy.py`):

| Azione | Quando | Cosa indica |
|---|---|---|
| **Compra SÌ / NO** | La valutazione dice *Conviene* | Ordine limite: quote, prezzo massimo, cifra. Appena comprate, un ordine limite di vendita |
| **Aspetta** | Il vantaggio c'è, ma al prezzo attuale costi e incertezza se lo mangiano | Il prezzo a cui conviene: un ordine in attesa |
| **Evita** | Il problema non è il prezzo: mercato poco liquido, scadenza oltre il preset, limiti di rischio pieni | I motivi |
| **Nessuna azione** | La stima è vicina al prezzo | A che prezzo comprare SÌ o NO diventerebbe conveniente |
| **Tieni** | C'è una posizione e il prezzo è ancora sotto la stima | L'ordine limite di vendita |
| **Vendi** | Il prezzo ha raggiunto la stima, o una nuova previsione l'ha girata | Quante quote e a che prezzo |

**Prezzi a cui la decisione cambia.** Per ogni prezzo del SÌ tra 1¢ e 99¢ la stima blended
viene ricalcolata come nella previsione (Jev calibrato unito a quel prezzo) e si rifanno i
controlli: segnale (`MIN_EDGE`), margine dopo commissioni e incertezza, rendimento annuo.
Il più alto prezzo che li passa tutti è «compra SÌ sotto»; il più basso per il NO è «compra
NO sopra». Il dettaglio li mostra su una scala dei prezzi insieme al prezzo attuale.

**Quando vendere.** Tenere una quota vale la probabilità che vinca, scontata per il tempo in
cui il capitale resta bloccato al rendimento che il preset chiede (tasso senza rischio + premio).
Vendere vale il prezzo di acquisto offerto meno la commissione. Il prezzo di vendita è il più
basso a cui vendere rende almeno quanto tenere:

```
vendi se  bid − commissione(bid)  ≥  P(lato vince | prezzo = bid) / (1 + rendimento richiesto)^(giorni/365)
```

Con poco tempo alla scadenza coincide quasi con la stima; con molti mesi è più basso, perché
incassare prima libera il capitale. Non c'è uno stop-loss fisso: se il prezzo scende ma la
stima no, la quota è ancora più conveniente. Si vende invece quando una nuova previsione dice
che conviene il lato opposto.

**Perché sì / perché no.** Ogni piano elenca i motivi: la differenza tra stima e prezzo, le
notizie che Jev ha giudicato a favore o contro (con i titoli), il margine dopo i costi, il
rendimento annuo, il tempo, la probabilità di perdere, il book stimato, le esclusioni e i
risultati passati (vantaggio sul prezzo nei mercati risolti e prezzo di chiusura dei segnali).

**Fiducia** *alta*, *media* o *bassa*: sale con notizie forti e risultati passati buoni;
scende con notizie deboli, stima incerta, prezzi stimati senza book e risultati passati
assenti o negativi.

Il piano compare anche nelle opportunità (con i prezzi del momento della previsione), nel
portafoglio (piano d'uscita di ogni posizione aperta) e nelle notifiche Telegram (ordine di
acquisto e di vendita).
