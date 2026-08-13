# Personen-Watchlist: Monitoring

**Stand:** 2026-08-12  
**Sprache:** DE  
**Skalierung:** siehe [`WATCHLIST_SCAN_SCALING.md`](WATCHLIST_SCAN_SCALING.md)

---

## Was wird geprüft?

Für **aktive** / **confirmed_fraud** Personen auf der Watchlist sucht Lynx nach **neuen Mandaten / Firmenverknüpfungen** (nicht «irgendwo online registriert» im Allgemeinen):

1. **Moneyhouse** Personensuche → Firmennamen  
2. **Zefix**-Auflösung der Firmen  
3. Optional (UI-Checkbox): langsamer **SHAB**-Nachscan  

Neue, noch unbekannte Firmen erzeugen:

- `PersonCompanyLink` (`relation_type=newly_found`)
- `NetworkAlert` (Typen u. a. `new_company_founded`, `new_role`) → Tab **Posteingang**

---

## Wann läuft das? (Limits ehrlich)

| Auslöser | Wann | Limit / Verhalten |
|----------|------|-------------------|
| **Cron (APScheduler)** | Täglich **04:15** Serverzeit | Zuerst **alle high-prio** (Cap `WATCHLIST_SCAN_HIGH_PRIORITY_CAP`, Default **50**), danach Rolling **`WATCHLIST_SCAN_BATCH`** (Default **25**); Concurrency **1**; Pause `WATCHLIST_SCAN_DELAY_SEC` |
| **«Liste fortsetzen»** | Manuell im UI | Default **`WATCHLIST_SCAN_MANUAL_LIMIT`** (**5**); nur Rolling (ohne erzwungenen High-Prio-Block) |
| **«Priorisierte jetzt prüfen»** | Admin | Nur `scan_priority=high` (Cap 50) |
| **Mandat-Scan in der Akte** | Manuell pro Person | Eine Person; optional E-Mail bei neuen Alerts |
| Login / Seitenaufruf | **Nein** | — |

**High-prio:** Personen aus Fall-Eröffnung, Betrugsbestätigung oder «In Abklärung» — siehe [`WATCHLIST_FRAUD_FOLLOWUP.md`](WATCHLIST_FRAUD_FOLLOWUP.md).

**Wichtig:** Die gesamte Watchlist wird **nicht** in einer Nacht gescannt (außer die high-prio-Gruppe, die bewusst jede Nacht drankommt). Bei großen normalen Listen rollt der Cursor über mehrere Nächte. Details: [`WATCHLIST_SCAN_SCALING.md`](WATCHLIST_SCAN_SCALING.md).

Ohne laufenden App-Prozess (z. B. Container down) läuft der Cron **nicht**. APScheduler muss installiert sein (`requirements.txt`: `apscheduler`).

Code: `app/hr_network/scheduler.py` → `run_person_monitoring` — startet mit der App (`app/main.py` lifespan).

---

## UI

- **Posteingang** (`/watchlist?tab=inbox`): offene Alerts, Quittieren, Akte öffnen  
- **Personen → «Liste fortsetzen»**: nächste Batch-Personen (Moneyhouse→Zefix)  
- Pro Person: **letzter Scan** (`last_monitored_at`); Hinweis **Abdeckung** (Anteil kürzlich gescannter)  
- **Personenakte**: Einzelscan (Moneyhouse→Zefix, optional SHAB)

---

## E-Mail bei neuen Funden

Optional. Konfiguration in `.env` (siehe `.env.example`):

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`
- `WATCHLIST_NOTIFY_EMAILS` — kommagetrennte Empfänger (Team-Postfächer; User-Tabelle hat kein E-Mail-Feld)

Verhalten:

- **Graceful skip**, wenn SMTP/Empfänger fehlen — Monitoring und Posteingang bleiben aktiv  
- **Batch** (Cron / «Liste fortsetzen»): **ein Digest** nur wenn neue Alerts — **kein** Mail pro Person  
- Einzel-Mail nach manuellem Personen-Scan, wenn neue Alerts entstanden  
- Code: `app/notify_email.py`

Admin-Diagnose: `GET /api/health/detail` → `watchlist_email_configured`, `person_monitoring_cron`, Scan-Batch/Delay.

---

## Env (Scan)

| Variable | Default | Zweck |
|----------|---------|--------|
| `WATCHLIST_SCAN_BATCH` | `25` | Nacht-Cron Rolling-Rest nach high-prio |
| `WATCHLIST_SCAN_HIGH_PRIORITY_CAP` | `50` | Max. high-prio Personen jede Nacht |
| `WATCHLIST_SCAN_DELAY_SEC` | `2` | Pause zwischen Personen |
| `WATCHLIST_SCAN_MANUAL_LIMIT` | `5` | UI «Liste fortsetzen» |

---

## Relevante Dateien

- `app/hr_network/person_monitoring.py` — Scan-Logik, Inbox, Batch-Job, Coverage  
- `app/hr_network/scheduler.py` — täglicher Cron  
- `app/hr_network/moneyhouse_person.py` — Moneyhouse-Suche  
- `app/notify_email.py` — SMTP  
- `static/watchlist.html` / `watchlist.js` — UI  
- `app/routes/network.py` — API  
- `docs/WATCHLIST_SCAN_SCALING.md` — Skalierungsstrategie  

---

## Geplant: SHAB-Tagesliste (komplementär)

Moneyhouse bleibt der Personen-Mandatsgraph. Zusätzlich geplant: **eine** tägliche SHAB/SOGC-Abholung (ZefixREST), lokale Speicherung ab «heute», Personen-Parse und Match gegen `watched_persons` — siehe [`SHAB_DAILY_WATCHLIST.md`](SHAB_DAILY_WATCHLIST.md). Noch nicht gebaut. 
