# Playbook: Nach Betrugsfall — Organe auf Watchlist

**Stand:** 2026-08-12  
**Sprache:** DE  
**Auslöser:** Realfall-Muster «Firma X Betrug → Beteiligte gründen/übernehmen kurz danach neue Firmen» (z. B. D9Performance GmbH)

Verwandt: [`WATCHLIST_MONITORING.md`](WATCHLIST_MONITORING.md) · [`WATCHLIST_SCAN_SCALING.md`](WATCHLIST_SCAN_SCALING.md)

---

## Warum Hits über Wochen fehlen konnten

Monitoring läuft **nur** für Personen auf der **Watchlist** — nicht für alle Personen in einer Fallakte und nicht beim reinen Öffnen der Firmenanalyse.

| Früher | Folge |
|--------|--------|
| Fall nur **eröffnet** (`under_review`) | Organe landeten **nicht** automatisch auf der Watchlist |
| Nur manuell beobachtete Personen | Unbeobachtete Beteiligte → **keine** neuen Mandate erkannt |
| Nacht-Cron nur **25**/Nacht, Rolling | Auch beobachtete High-Risk-Personen konnten **Wochen** auf den nächsten Scan warten |
| «In Abklärung» | Firma + Organe → Watchlist (seit Phase‑1) — half nur, wenn der Tag gesetzt war |

**Muster D9Performance:** Betrugsfall Mai → Beteiligte kaufen/gründen im Juli zwei neue Firmen → Team erfährt es erst spät, wenn niemand täglich auf der Watchlist gescannt wurde.

---

## Neuer Ablauf (MVP)

```
Betrugsverdacht
    │
    ├─ «In Abklärung» (Tag) ──► Firma + aktuelle Organe → Watchlist (scan_priority=high)
    │
    └─ Fall eröffnen / Betrug bestätigen
            │
            └─ aktuelle Organe → Watchlist (scan_priority=high)
                    │
                    ▼
         Nacht-Cron 04:15
            1) ALLE high-prio (Cap 50)
            2) Rolling-Rest (Batch 25)
                    │
                    ▼
         Neues Mandat ≠ bekannte Seed-Links
            → NetworkAlert (Posteingang)
            → Digest-E-Mail (wenn SMTP gesetzt)
```

### Was automatisch auf die Watchlist kommt

| Auslöser | Personen | `scan_priority` |
|----------|----------|-----------------|
| Fall **eröffnen** | aktuelle Organe (Zefix/SHAB-Timeline) | `high` |
| Betrug **bestätigen** | aktuelle Organe | `high` |
| Tag **In Abklärung** | aktuelle Organe (+ Firma) | `high` |

Manuelle Watchlist-Einträge bleiben `normal` (Rolling-Queue).

### Alerting (bereits vorhanden — verifiziert)

`person_monitoring.scan_watched_person_incremental`:

1. Moneyhouse → Zefix (optional SHAB) liefert Mandate  
2. Abgleich gegen bestehende `PersonCompanyLink` (Seed / bereits bekannte Firmen)  
3. Nur **neue** Firmen → `relation_type=newly_found` + `NetworkAlert`  
4. Batch: ein Digest an `WATCHLIST_NOTIFY_EMAILS`

---

## Team-Checkliste

1. Bei Verdacht **Fall eröffnen** oder **In Abklärung** setzen — nicht nur analysieren.  
2. Am nächsten Morgen **Posteingang** (`/watchlist?tab=inbox`) und ggf. Team-Mail prüfen.  
3. Admin bei Bedarf: **«Priorisierte jetzt prüfen»** (nur high-prio, sofort).  
4. SMTP/`WATCHLIST_NOTIFY_EMAILS` in `.env` setzen, sonst nur Posteingang.

---

## Konfiguration

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `WATCHLIST_SCAN_HIGH_PRIORITY_CAP` | `50` | Max. high-prio Personen **jede Nacht** |
| `WATCHLIST_SCAN_BATCH` | `25` | Rolling-Rest nach high-prio |
| `WATCHLIST_SCAN_MANUAL_LIMIT` | `5` | UI «Liste fortsetzen» (nur Rolling) |
| `WATCHLIST_NOTIFY_EMAILS` | — | Digest-Empfänger |

---

## API

| Methode | Pfad | Wer |
|---------|------|-----|
| POST | `/api/watched-persons/run-monitoring` | Auth — Rolling fortsetzen |
| POST | `/api/watched-persons/run-high-priority-monitoring` | Admin — nur high-prio |
