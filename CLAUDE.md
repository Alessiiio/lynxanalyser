# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Lynx** — a Swiss firm-network and website-legitimacy analysis tool. FastAPI backend, SQLite (via SQLAlchemy async), vanilla static HTML/JS frontend (no build step, no framework). UI-facing text and code comments are largely German.

Two halves live in one app:
- **Website checker** (`app/checks/`, `app/checker.py`) — scores an arbitrary domain/URL for fraud/legitimacy signals.
- **HR-Network / Firmenanalyse** (`app/hr_network/`) — builds a graph of Swiss companies and people from Zefix/SHAB/Moneyhouse, drives watchlists, cases, and bulk scans.

## Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill API keys and secrets

# Run (http://localhost:8000)
python run.py

# Tests (activate venv first — system python lacks deps like `cryptography`)
python -m pytest                              # all tests
python -m pytest tests/test_case_flow.py       # one file
python -m pytest tests/test_case_flow.py::test_name  # one test
```

There is no lint/format tooling configured (no ruff/black/mypy config in the repo) and no `conftest.py` — each test file is self-contained.

**Test isolation pattern:** tests that touch the DB create a temp SQLite file, set `DATABASE_PATH`/`SESSION_SECRET`/seed-password env vars *before* importing `app.database`, then monkeypatch `db.engine` / `db.async_session` to point at the temp file — only then do they import the modules under test (see `tests/test_case_flow.py`). Follow this pattern for new DB-touching tests rather than relying on the real `fraud_checks.db`.

### Reset runtime data
```bash
python3 scripts/reset_runtime_data.py   # clears cases/watchlist/scan history/reports/SHAB cache, keeps users
```

### Production (VPS)
Docker Compose + Caddy (Let's Encrypt) — single `app` container + `caddy`, see [deploy/README.md](deploy/README.md). Required prod env: `ENVIRONMENT=production`, `SESSION_SECRET`, `TOTP_ENCRYPTION_KEY`, `SEED_*_PASSWORD`, `HTTPS_ONLY=1`, `DOMAIN` — `config.py` raises `RuntimeError` at import time if these are missing/weak in production.

## Architecture

### Entrypoint & middleware (`app/main.py`)
`app/main.py` is wiring only. Routers are included from `app/routes/*.py` (auth, admin, product, network, cases, checker, lists, compliance, swiss_banks). Middleware order matters — **last added is outermost**: `RequireLoginMiddleware` → `SessionMiddleware` → `MutatingOriginMiddleware` → `SecurityHeadersMiddleware` → optional `TrustedHostMiddleware`.

- Auth is required for every path except `PUBLIC_PATHS`/`PUBLIC_PREFIXES` (login, health, static, 2FA enrollment).
- **2FA is mandatory**: after password login, a session is only "usable" once `totp_enabled` is true (`app/routes/deps.py: load_user_from_session`); an incomplete/reset 2FA state clears the session on next request.
- Roles (`app/auth.py`): `case_manager`, `compliance`, `admin` (plus legacy `analyst` alias migrated on startup). `admin` always passes `require_role(...)` checks.
- `run.py` forces **single uvicorn worker** — SQLite writes and in-process rate limiting are not multi-worker safe. Don't add `workers=N`.

### Website checker pipeline (`app/checker.py`, `app/checks/`)
Each check is a `BaseCheck` subclass (`app/checks/base.py`) implementing `async def run(domain, **kwargs) -> CheckResult`, with a `tier` (1=primary, 2=important, 3=technical). New checks are registered by adding an instance to `ALL_CHECKS` in `app/checker.py`.

`run_all_checks()` / `stream_checks()` (SSE-style generator, used for live progress in the UI) orchestrate the pipeline:
1. Cache check (`app/cache.py`) — return early if fresh.
2. **Blocklist fast path** — confirmed-fraud domains (`app/blocklist.py`) skip all checks, get a synthetic critical-risk report (`app/fraud_confirm.py`).
3. **Goldlist fast path** — known-legitimate domains (`app/goldlist.py`) skip everything except `_GOLDLIST_SAFETY_CHECKS` (safebrowsing, virustotal, finma, iscan).
4. Otherwise, all non-LLM checks run **in parallel** with per-check timeouts (`_CHECK_TIMEOUTS`) and limited retries; the LLM content check (`app/checks/llm_content_check.py`) runs *after*, fed a `check_context` built from the other checks' results (`app/checks/llm_context.py`) — costs more, needs the other signals first.
5. `app/scoring.py: calculate_score()` turns `list[CheckResult]` into a `FullReport` (score + verdict); result is cached, saved to `scan_history` (async, non-blocking), and diffed against the previous scan for the same domain.

LLM content analysis uses either Anthropic (`ANTHROPIC_API_KEY`) or a local Ollama model (`OLLAMA_BASE_URL`) — see `config.py`.

### HR-Network / Firmenanalyse (`app/hr_network/`)
Builds a graph of Swiss companies/people from Zefix (official registry, primary identity source) and SHAB (official gazette), with Moneyhouse used only as a secondary fill-in for person→mandate data (never as identity source — see comments in `config.py`). Key pieces:
- `fraud_network.py` / `service.py` — graph construction (`build_fraud_network` does multi-level company/person expansion; `service.py`'s `build_hr_network` is a thin back-compat wrapper).
- `zefix_rest.py`, `zefix_resolve.py`, `shab_parser.py`, `shab_daily.py` — external data sources.
- `watched_companies.py` / `under_investigation_watchlist.py` (companies) and `person_monitoring.py` / `watch_intake.py` (people) — watchlist state; `person_search.py`/`moneyhouse_person.py` for Moneyhouse lookups.
- `company_cases.py` / `case_flags.py` — case workflow (open/suspicious/confirmed-fraud/closed) attached to a watched company.
- `bulk_scan.py` — scan a batch of company names → user selects hits → adds to watchlist.
- `scheduler.py` — APScheduler jobs started in `app/main.py`'s lifespan: nightly person-watchlist rolling scan (04:15), company cache refresh, SHAB daily ingest+match. Tune via `WATCHLIST_SCAN_*` / `COMPANY_CACHE_*` / `SHAB_DAILY_*` env vars in `config.py`.
- `demo_fixture.py` + `app/hr_network/fixtures/demo_fraud_firm.json` — offline demo company **DEMO-FRAUD GmbH** (`CHE-000.000.001`) for UI demos/tests without live Zefix/Moneyhouse calls; deep search levels L2–L5 return canned graph subsets.

### Data layer (`app/database.py`)
Single file with all SQLAlchemy models (`scan_history`, `check_details`, `watched_persons`, `person_company_links`, `network_alerts`, `users`, `shab_daily_*`, `company_cases`, `case_journal_entries`, `company_tags`, `watched_companies`, `bulk_scan_jobs`, `audit_events`, etc.). **No Alembic** — schema evolution is hand-rolled: `init_db()` calls `Base.metadata.create_all` then a sequence of `_migrate_*_columns(conn)` functions that additively `ALTER TABLE` existing SQLite DBs (since `create_all` doesn't alter existing tables). When adding a column to an existing table, add a corresponding `_migrate_*` step rather than assuming a fresh DB.

### Frontend (`static/`)
Plain HTML/JS/CSS per page (e.g. `company-analysis.html` + `.js`, `watchlist.html` + `.js`, `admin.html` + `.js`), no bundler, no framework. `static/style.css` is the single global stylesheet with a token-based design system in `:root` (colors, radii, shadows, fonts). **Read [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) before adding UI** — it documents the canonical `.btn`/`.card`/badge/pill classes and explicitly warns against inventing new near-duplicate component classes (an audit found 33 button classes, 68+ badge classes before consolidation started).

### Config (`config.py`)
All env vars are read once at import time into module-level constants (no pydantic-settings). Production imports fail fast (`RuntimeError`) if secrets are unset or look like defaults — this is intentional; don't relax it. `.env.example` documents every variable.

## Docs worth reading before larger changes
- [docs/AUTH_ADMIN_2FA_PLAN.md](docs/AUTH_ADMIN_2FA_PLAN.md) — user roles/2FA design.
- [docs/WATCHLIST_BULK_SCAN_PLAN.md](docs/WATCHLIST_BULK_SCAN_PLAN.md), [docs/WATCHLIST_SCAN_SCALING.md](docs/WATCHLIST_SCAN_SCALING.md), [docs/WATCHLIST_MONITORING.md](docs/WATCHLIST_MONITORING.md), [docs/SHAB_DAILY_WATCHLIST.md](docs/SHAB_DAILY_WATCHLIST.md) — watchlist/scan architecture and rate-limiting rationale toward Zefix/Moneyhouse.
- [PLANNING.md](PLANNING.md) — pending/approved work items (mirrors `data/planning.json`).
- [WISHLIST.md](WISHLIST.md) — feedback board mirror (source of truth is `data/wishlist.json`); regenerate via `scripts/wishlist_to_changelog.py`.
- [CHANGELOG.md](CHANGELOG.md) — Keep a Changelog + SemVer; `scripts/changelog_from_commits.py` proposes entries from Conventional Commits.
