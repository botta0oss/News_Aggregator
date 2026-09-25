# Deploy

<sub>[← Back to the README](../README.md) · [All documentation](README.md) · [Italiano](it/deploy.md)</sub>

Docker images for CPU and GPU, local and home-network use, going online on a VPS with
Cloudflare Tunnel.

## Docker images: CPU or GPU

There are two images:

| File | For | Notes |
|---|---|---|
| `Dockerfile` (default) | Servers, VPSs, ARM machines, PCs without an NVIDIA GPU | CPU-only PyTorch: a much smaller image and a faster build |
| `Dockerfile.cuda` | PCs with an NVIDIA GPU | PyTorch with CUDA; embeddings are computed on the GPU |

To use the GPU (you need the NVIDIA driver and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on the host, x86_64 only):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

In the startup log, `Embedding model ... on cuda:0` confirms the GPU is in use (`on cpu`
otherwise). The two images have different names (`news-aggregator-api:cpu` and `:cuda`), so you
can switch between them without rebuilding every time. A server does not need the GPU: the
model is small and articles arrive in batches.

## Local use and home network

To use it on your own computer you need no tunnel or anything else: `tunnel` and `backup` are
optional services, active only when listed in `COMPOSE_PROFILES`. Without them, compose starts
only the database and the app.

```bash
cp .env.example .env      # TYPESAFE_API_KEY, POSTGRES_PASSWORD and the other keys
docker compose up -d --build
docker compose exec api python -m backend.auth.cli create-user yourname --role admin
```

The dashboard is on **http://localhost:8000**. Sign-in works over HTTP too: with
`SESSION_COOKIE_SECURE=auto` the session cookie does not require HTTPS when the address is
`localhost` (or `127.0.0.1`). For the daily backup locally too: `COMPOSE_PROFILES=backup`.

**From another device at home** (phone, tablet, another PC), for example on
`http://192.168.1.10:8000`, you need two settings in `.env`:

| Variable | Value | Why |
|---|---|---|
| `API_BIND` | `0.0.0.0` | By default port 8000 only answers the machine the app runs on |
| `SESSION_COOKIE_SECURE` | `false` | With an address other than `localhost`, `auto` marks the cookie as `Secure`: over HTTP the browser does not save it and sign-in fails; the sign-in page says so and points to this setting |

Then `docker compose up -d` to apply them. You find the computer's address with `ip addr`
(Linux), `ipconfig` (Windows) or in the network settings (macOS); if it does not answer, check
that the computer's firewall lets port 8000 through.

> [!WARNING]
> Without HTTPS the password and the session travel in clear text: fine only on a trusted home
> network, never on a public one. To get in from outside use
> [Cloudflare Tunnel](#deploy-on-a-vps-with-cloudflare-tunnel): it also works with the app on
> your home computer, and in that case set `SESSION_COOKIE_SECURE=auto` and
> `API_BIND=127.0.0.1` again.

## Deploy on a VPS with Cloudflare Tunnel

The cheapest way to keep it online: a small VPS (for example OVH VPS-1 or Hetzner: 2 vCores,
4 GB of RAM are enough) with Docker, and **Cloudflare Tunnel** for HTTPS. The tunnel goes out
from the server to Cloudflare: no ports to open, no certificates to manage, the server's IP
address stays hidden. You need a domain managed by Cloudflare (free plan); the tunnel is free.

```mermaid
flowchart LR
    U[Browser] -- HTTPS --> CF[Cloudflare]
    CF -- outbound tunnel --> T[cloudflared]
    subgraph VPS
      T --> A[api :8000]
      A --> D[(Postgres)]
      B[daily backup] --> D
    end
```

**1. The server.** Ubuntu 24.04, SSH access with a key. A firewall with only the SSH port open:

```bash
sudo apt update && sudo apt upgrade -y
sudo ufw allow OpenSSH && sudo ufw enable
```

The app's port 8000 only listens on `127.0.0.1` (`API_BIND`): Docker bypasses `ufw` for
published ports, which is why it is not published on the network.

**2. Docker and the code.**

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER      # then log out and back in
git clone https://github.com/botta0oss/News_Aggregator.git && cd News_Aggregator
cp .env.example .env
```

**3. The tunnel.** In the Cloudflare dashboard: *Zero Trust → Networks → Tunnels → Create a
tunnel → Cloudflared*, give it a name and copy the **token** (the long string after `--token`
in the suggested install command: you do not need to run it, `cloudflared` already runs in
compose). Then add a *Public hostname*: a subdomain of your choice (e.g. `news.yourdomain.com`),
type **HTTP**, URL **`api:8000`**.

**4. The `.env` file.** Besides the API keys:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | A random password, before the first start: `openssl rand -hex 24` |
| `COMPOSE_PROFILES` | `tunnel,backup` |
| `CLOUDFLARE_TUNNEL_TOKEN` | The token from step 3 |
| `CLIENT_IP_HEADER` | `CF-Connecting-IP` (visitors' real IP for the sign-in limits) |
| `PUBLIC_URL` | `https://news.yourdomain.com` (links in the Telegram notifications) |
| `API_DOCS_ENABLED` | `false` |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | The first administrator; after the first start remove the password from the file |
| `DAILY_JEV_CALL_LIMIT`, `DAILY_AI_BUDGET_USD` | A cap on the spend for paid calls |
| `APP_LANGUAGE` | `en` or `it`: language of Telegram alerts and news summaries |

**5. Start.**

```bash
docker compose up -d --build        # the first build downloads the model: a few minutes
docker compose logs -f api tunnel   # wait for "Application startup complete" and "Registered tunnel connection"
```

The dashboard is on `https://news.yourdomain.com`. For an extra layer of protection,
*Zero Trust → Access* can ask for an email code even before the sign-in page (free up to 50
users).

**Update** to the latest version (data and backups stay):

```bash
./scripts/update.sh
```

**Backups.** With the `backup` profile a dump is saved every day in `./backups`, kept for
`BACKUP_KEEP_DAYS` days. It is on the same disk as the database: copy it off the server too
(for example with `rclone` to Cloudflare R2, or downloading it with `scp`), or turn on the VPS
backups offered by the provider. To restore one:

```bash
docker compose stop api
docker compose exec -T db pg_restore -U postgres -d postgres --clean --if-exists < backups/newsagg-YYYYMMDD-HHMM.dump
docker compose start api
```

**If something goes wrong.**
- *Error 502 or 1033 from Cloudflare*: the app is not answering yet (at the first start it
  loads the model) or the tunnel is not connected: `docker compose logs api tunnel`.
- *The tunnel does not connect*: wrong token, or in the *Public hostname* the URL is not `api:8000`.
- *Database password changed after the first start*: the database keeps the old one. Update
  it there too: `docker compose exec db psql -U postgres -c "ALTER USER postgres PASSWORD 'new'"`.
