# Lynx

Swiss firm-network and legitimacy analysis tool (FastAPI + SQLite).

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill API keys and secrets
python run.py          # http://localhost:8000
```

Auth is required for all pages except login and `/api/health`.

## Production (VPS)

Docker Compose + Caddy (Let’s Encrypt): see [deploy/README.md](deploy/README.md).

Set at least `ENVIRONMENT=production`, `SESSION_SECRET`, seed passwords, `HTTPS_ONLY=1`, and `DOMAIN`.

## Reset runtime data

Clears cases, watchlist, scan history, reports, and SHAB cache. Keeps users.

```bash
python3 scripts/reset_runtime_data.py
```

Profiler fall history stored in the browser (`localStorage`) is not cleared by this script.

## Stack

FastAPI, SQLite, static HTML/JS, optional Anthropic/Ollama for content analysis.
