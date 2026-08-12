# Lynx — VPS deploy (Docker Compose + Caddy)

## Prerequisites

- VPS with Docker and Docker Compose plugin
- DNS A/AAAA record for your domain pointing at the VPS
- Ports 80 and 443 open

## Setup

1. Copy the project to the server (without `.env`, local DB, or SHAB cache).

2. Create `.env` from the example and fill secrets:

```bash
cp .env.example .env
# Set at least:
#   DOMAIN=your.example.com
#   SESSION_SECRET=<long random>
#   TOTP_ENCRYPTION_KEY=<Fernet key — see below>
#   SEED_ADMIN_PASSWORD / SEED_CASE_MANAGER_PASSWORD / SEED_COMPLIANCE_PASSWORD
#   HTTPS_ONLY=1
#   ENVIRONMENT=production
#   API keys as needed (Zefix, Safe Browsing, …)
```

Generate secrets:

```bash
# Session cookie signing
python -c "import secrets; print(secrets.token_hex(32))"
# TOTP secret encryption (Fernet) — required in production
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**2FA:** All users must enroll TOTP (Authenticator) after password login before using the app. Admins can reset another user’s 2FA under `/admin` (not their own).

3. Start:

```bash
export DOMAIN=your.example.com
docker compose up -d --build
```

Caddy obtains a Let’s Encrypt certificate for `$DOMAIN` automatically.

4. Open `https://your.example.com/login` and sign in with the seed accounts.

5. Set `FORCE_RESET_SEED_PASSWORDS=0` after the first successful boot (if you used `1`).

## Reset operational data (keeps users)

```bash
docker compose exec app python scripts/reset_runtime_data.py
```

Browser-only Profiler history (`localStorage`) is not cleared by this script — clear site data on the client if needed.

## Backup

SQLite lives in the `lynx_data` volume (`DATABASE_PATH=/app/data/fraud_checks.db`).

```bash
docker compose exec app sqlite3 /app/data/fraud_checks.db ".backup '/app/data/fraud_checks.backup.db'"
docker compose cp app:/app/data/fraud_checks.backup.db ./fraud_checks.backup.db
```

## Update existing VPS (redeploy)

Production does **not** hot-reload. After code changes:

```bash
# On the Mac — sync code (no .env / DB / caches), e.g. rsync as in deploy/HETZNER.md
# Or: git pull on the server if the repo was cloned there

ssh root@DEINE_VPS_IP
cd /opt/lynx
# Confirm env still has ZEFIX_USERNAME / ZEFIX_PASSWORD
# Optional: MONEYHOUSE_PERSON_SEARCH=1 (default) or =0 to disable MH fill-in
export DOMAIN=deine-domain.ch
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 app
```

Then in the browser: hard-refresh (Cmd/Ctrl+Shift+R) so `company-analysis.js` / CSS reload.

Quick checks: `/api/health`, login, Firmenanalyse SW2 on a known firm.

## Long scans (Suchweite 5) and HTTP 502 HTML

`POST /api/fraud-network/analyze` is a **synchronous** request. L5 (2. Ring / SHAB+Zefix) often runs **several minutes**. If the reverse proxy aborts early, the browser gets **HTML 502** from Caddy (not JSON from FastAPI) — UI: *Server lieferte HTML statt JSON (HTTP 502)*.

The `Caddyfile` sets ~**20m** upstream `response_header_timeout` / read/write timeouts and long server `write`/`idle` timeouts. Compose uses a more tolerant app **healthcheck** so Docker does not restart mid-scan on brief health blips.

After changing `Caddyfile` or compose healthcheck, redeploy (see **Update existing VPS** above). Caddy-only config reload:

```bash
cd /opt/lynx
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
# or: docker compose up -d --build
```

### Verify

```bash
# While a cold L5 runs in the browser, on the VPS:
docker compose logs -f --tail=100 app caddy

# Timed API call (needs session cookie from browser DevTools):
curl -sS -o /tmp/l5.json -w "http=%{http_code} time=%{time_total}s type=%{content_type}\n" \
  -X POST "https://$DOMAIN/api/fraud-network/analyze" \
  -H "Content-Type: application/json" \
  -H "Cookie: lynx_session=…" \
  -d '{"level":5,"ad_hoc_company":{"name":"…","uid":"…"},"max_person_searches":8}'
# Expect: http=200, type=application/json, time often >60s on cold L5.
```

Demo workarounds if L5 still fails: use **SW3/SW4**, wait for cache (7 days), avoid cold L5 on the first firm in a session.

## Notes

- The app container is not published on the host; only Caddy exposes 80/443.
- `FORWARDED_ALLOW_IPS=*` is safe in this layout because nothing else can reach the app.
- Set `ALLOWED_HOSTS` to your real domain (compose already injects `$DOMAIN`).
- Keep **one** uvicorn worker (SQLite); do not scale `app` replicas without a different DB story.
