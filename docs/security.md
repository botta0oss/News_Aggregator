# Access and security

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/sicurezza.md)</sub>

Users and roles, sessions, protections and going online.

The whole API, except sign-in, requires an authenticated user. The dashboard's static files
are public but contain no data.

**Roles**

| Role | Can do |
|---|---|
| `admin` | Everything: view the data, update news and markets, ask Jev for forecasts (paid) |
| `viewer` | View news, markets, forecasts and calibration |

**User management** (there is no public sign-up):

```bash
python -m backend.auth.cli create-user alice --role admin   # asks for the password
python -m backend.auth.cli create-user bob                   # viewer
python -m backend.auth.cli list-users
python -m backend.auth.cli set-password alice                # also closes all her sessions
python -m backend.auth.cli disable bob                       # disables and signs out
python -m backend.auth.cli enable bob
python -m backend.auth.cli revoke-sessions alice
```

The command-line messages follow `APP_LANGUAGE`.

Alternatively, at the first start with no users an admin is created from `ADMIN_USERNAME` and
`ADMIN_PASSWORD`. After the first start remove `ADMIN_PASSWORD` from the `.env` file.

**How it works**

- **Passwords:** Argon2id hash, at least 12 characters, they cannot contain the username.
  Hashes with old parameters are updated at the next sign-in.
- **Server-side sessions:** the `nm_session` cookie contains a random 256-bit token; the
  database only holds its SHA-256 hash. The cookie is `HttpOnly`, `SameSite=Lax` and `Secure`
  (except on `localhost` over HTTP).
- **Expiry:** each session lasts at most `SESSION_TTL_HOURS` and ends after
  `SESSION_IDLE_MINUTES` of inactivity. Sign-out, password change and disabling the user
  revoke it right away.
- **CSRF:** every request that changes data must send the session's token in the
  `X-CSRF-Token` header. The dashboard does it by itself.
- **Sign-in attempts:** after `LOGIN_MAX_ATTEMPTS` failures per IP and username, or
  `LOGIN_MAX_ATTEMPTS_PER_IP` per IP, sign-in is blocked for `LOGIN_WINDOW_MINUTES`. The
  answer is the same whether the user exists or not, and takes the same time.
- **Security headers:** Content-Security-Policy without inline scripts, `X-Frame-Options:
  DENY`, `nosniff`, HSTS when the request arrives over HTTPS.
- **CORS:** off. The dashboard is on the same origin; any external origins must be listed in
  `CORS_ORIGINS` (the `*` wildcard is ignored).

**Going online**

1. Put the app behind HTTPS: the simplest way is Cloudflare Tunnel, ready in the compose file
   (see [Deploy on a VPS](deploy.md#deploy-on-a-vps-with-cloudflare-tunnel), with
   `CLIENT_IP_HEADER=CF-Connecting-IP`); alternatively a reverse proxy (Caddy, nginx, Traefik).
2. With a reverse proxy start uvicorn with `--proxy-headers` (already in the Dockerfile), so
   the attempt limit sees the client's real IP. If the proxy does not run on the same machine,
   add `--forwarded-allow-ips` with its address.
3. Set `API_DOCS_ENABLED=false` so as not to publish the API schema.
4. The attempt limit is in memory: with several workers each one has its own counters. With
   several workers add a limit on the proxy too.
