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
#   SEED_ADMIN_PASSWORD / SEED_CASE_MANAGER_PASSWORD / SEED_COMPLIANCE_PASSWORD
#   HTTPS_ONLY=1
#   ENVIRONMENT=production
#   API keys as needed (Zefix, Safe Browsing, …)
```

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

## Notes

- The app container is not published on the host; only Caddy exposes 80/443.
- `FORWARDED_ALLOW_IPS=*` is safe in this layout because nothing else can reach the app.
- Set `ALLOWED_HOSTS` to your real domain (compose already injects `$DOMAIN`).

## Auto-Deploy via GitHub Actions

Bei jedem Push auf `main` deployt GitHub Actions automatisch (`.github/workflows/deploy.yml`).

Einmalige Einrichtung:

1. **Deploy Key** (read-only, damit der Server das Repo pullen kann): Settings → Deploy keys → Add deploy key. Public Key liegt lokal bereit; privaten Key in `~/.ssh/github_deploy_key` auf dem Server ablegen (siehe `deploy/bootstrap.sh`).
2. **Actions Secrets** unter Settings → Secrets and variables → Actions:
   - `VPS_HOST` — Server-IP
   - `VPS_USER` — `root`
   - `VPS_SSH_KEY` — privater Key, mit dem GitHub Actions sich per SSH einloggt (separat vom GitHub-Deploy-Key)
3. Auf dem Server einmalig `deploy/bootstrap.sh` ausführen (Docker installieren, Repo nach `/opt/lynx` klonen).
4. `.env` in `/opt/lynx` anlegen und befüllen, danach `docker compose up -d --build`.

Ab dann reicht `git push` auf `main` — GitHub Actions verbindet sich per SSH und macht `git pull && docker compose up -d --build`.
