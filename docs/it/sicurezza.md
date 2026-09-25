# Accesso e sicurezza

<sub>[← Torna al README](../../README.it.md) · [Tutta la documentazione](README.md) · [English](../security.md)</sub>

Utenti e ruoli, sessioni, protezioni e messa online.


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

1. Metti l'app dietro HTTPS: il modo più semplice è Cloudflare Tunnel, già pronto nel compose
   (vedi [Deploy su un VPS](deploy.md#deploy-su-un-vps-con-cloudflare-tunnel), con
   `CLIENT_IP_HEADER=CF-Connecting-IP`); in alternativa un reverse proxy (Caddy, nginx, Traefik).
2. Con un reverse proxy avvia uvicorn con `--proxy-headers` (già nel Dockerfile), così il limite dei tentativi
   vede il vero IP del client. Se il proxy non gira sulla stessa macchina, aggiungi
   `--forwarded-allow-ips` con il suo indirizzo.
3. Imposta `API_DOCS_ENABLED=false` per non pubblicare lo schema dell'API.
4. Il limite dei tentativi è in memoria: con più worker ognuno ha i suoi contatori. Con più
   worker aggiungi un limite anche sul proxy.
