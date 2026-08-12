# Lynx

Swiss firm-network and legitimacy analysis tool (FastAPI + SQLite).

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill API keys and secrets
python run.py          # http://localhost:8000
```

Auth is required for all pages except login and `/api/health`. After password login, **2FA enrollment or TOTP/backup verification** is required before the session is fully usable.

### Offline-Demo-Firma (ohne Zefix/Moneyhouse)

Für UI-Demos und Tests ohne Live-APIs gibt es die fiktive Firma **DEMO-FRAUD GmbH** (`CHE-000.000.001`):

- Firmenanalyse → Button **«Demo-Firma laden»**, oder Suche nach `DEMO-FRAUD`
- Direktlink: `/?demo=fraud` bzw. `/?company=DEMO-FRAUD%20GmbH&uid=CHE-000.000.001`
- Fixture: [`app/hr_network/fixtures/demo_fraud_firm.json`](app/hr_network/fixtures/demo_fraud_firm.json) (klar als Demo markiert; liegt im Image unter `app/`, nicht im Docker-Volume `/app/data`)
- Deep-Suchweiten L2–L5 liefern vorgefertigte Graph-Untermengen — **keine** externen API-Calls

Die frühere Admin-Seite `/test` (Layout-Playground) wurde entfernt.

## Production (VPS)

Docker Compose + Caddy (Let’s Encrypt): see [deploy/README.md](deploy/README.md).

**Hetzner (Ubuntu) step-by-step:** [deploy/HETZNER.md](deploy/HETZNER.md).

Set at least `ENVIRONMENT=production`, `SESSION_SECRET`, `TOTP_ENCRYPTION_KEY`, seed passwords, `HTTPS_ONLY=1`, and `DOMAIN`.

## Changelog & Feedback

- **Versionshistorie:** [CHANGELOG.md](CHANGELOG.md) ([Keep a Changelog](https://keepachangelog.com) + [SemVer](https://semver.org))
- **In-App:** `/changelog` liest `CHANGELOG.md`
- **Wishlist / Feedback:** Floating-Button in der App, Board unter `/feedback`, Persistenz in `data/wishlist.json` (Spiegel: [WISHLIST.md](WISHLIST.md))

Hilfsskripte:

```bash
# Conventional Commits → Changelog-Vorschlag (stdout)
python3 scripts/changelog_from_commits.py
python3 scripts/changelog_from_commits.py --since v1.0.0

# Erledigte Wishlist-Einträge → Changelog-Vorschlag
python3 scripts/wishlist_to_changelog.py
```

## Reset runtime data

Clears cases, watchlist, scan history, reports, and SHAB cache. Keeps users.

```bash
python3 scripts/reset_runtime_data.py
```

Profiler fall history stored in the browser (`localStorage`) is not cleared by this script.

## Stack

FastAPI, SQLite, static HTML/JS, optional Anthropic/Ollama for content analysis.
