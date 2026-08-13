# Personen-Watchlist: Scan-Skalierung

**Stand:** 2026-08-12  
**Sprache:** DE  
**Verwandt:** [`WATCHLIST_MONITORING.md`](WATCHLIST_MONITORING.md)

---

## Problem

Die Watchlist soll **alle** beobachteten Personen regelmäßig prüfen, ob sie neu bei einer Firma «mitmischen» (neues Mandat / Organrolle) — Fraud-Frühwarnung.

Quellen (Moneyhouse → Zefix, optional SHAB) haben **Rate-Limits**. Ein Vollscan der gesamten Liste in einer Nacht würde wie Spam wirken, Blockaden riskieren und bei vielen Treffern die Inbox/E-Mail überfluten.

---

## Ist-Zustand vs. Bedarf

| | Heute (ehrlich) | Bedarf bei großer Liste |
|--|-----------------|-------------------------|
| **Nacht-Cron** | 04:15: **high-prio alle** (Cap **50**) + Rolling **`WATCHLIST_SCAN_BATCH`** (**25**) | Fall-Organe täglich; Rest zyklisch |
| **Manuell «Liste fortsetzen»** | UI-Limit **5** (API max. 50), Rolling | Fortsetzen der Warteschlange |
| **Admin «Priorisierte jetzt prüfen»** | Nur high-prio | Sofort-Check nach Fall |
| **Auswahl** | High zuerst; Rolling: älteste / nie gescannte | Prioritäts-Tier + Coverage |
| **Parallelität** | **1** Person nach der anderen | Schonend für Moneyhouse/Zefix |
| **Pause** | Optional `WATCHLIST_SCAN_DELAY_SEC` (+ leichtes Jitter) | Kein Burst |
| **E-Mail** | **Ein Digest** pro Batch nur wenn neue Alerts; Einzelscan kann eine Mail senden | Kein Mail-Sturm pro Person im Nachtlauf |
| **Abdeckung** | Messbar über `last_monitored_at` / Coverage-% | Ops sichtbar in UI + Health |

**Fazit:** High-prio (Fall / In Abklärung) wird **jede Nacht** abgedeckt (bis Cap). Der Rest rollt weiter — bei z. B. 500 normalen Personen und Batch 25 braucht es **~20 Nächte** für einen vollen Zyklus. Playbook: [`WATCHLIST_FRAUD_FOLLOWUP.md`](WATCHLIST_FRAUD_FOLLOWUP.md).

---

## Ziel-Architektur (Skalierung)

### 1. Rolling Full Coverage (Cursor)

- Jede aktiv überwachte Person hat **`last_monitored_at`** (`person_watch_scans.last_run_at`).
- Nachtjob wählt die **N nie/ältesten** Personen (Batch-Größe aus Env).
- Nach erfolgreichem Scan wird der Zeitstempel gesetzt → nächste Nacht rückt die Warteschlange weiter.
- Zykluszeit ≈ `ceil(aktive_Personen / Batch) / Nächte` (z. B. 7 vs. 30 Tage je nach Batch).

### 2. Schonendes Abrufprofil

| Maßnahme | Empfehlung |
|----------|------------|
| Concurrency | **1** (kein paralleles Moneyhouse) |
| Delay | `WATCHLIST_SCAN_DELAY_SEC` zwischen Personen (z. B. 2–5 s) |
| Jitter | kleines Random ±30 % am Delay |
| Backoff | bei API-Fehlern Pause erhöhen / Restbatch abbrechen |
| Moneyhouse-Cap | Batch-Obergrenze = Cap pro Nacht; kein Nachziehen am Tag |

### 3. Prioritäts-Warteschlange

1. **`scan_priority=high`** — Fall eröffnet / bestätigt / In Abklärung / Shell-Takeover: **jede Nacht** (Cap `WATCHLIST_SCAN_HIGH_PRIORITY_CAP`)  
2. **Rolling** — älteste / nie gescannte zuerst; soft Hint nach `source_reason`  

Spalte `watched_persons.scan_priority` (`high` | `normal`).

### 4. Alerts & E-Mail

- Dedup bleibt über bestehende Firmenlinks (kein zweiter Alert für bekannte Firma).
- **Nacht / «Liste fortsetzen»:** nur **ein Digest**, wenn `alerts > 0`.
- Kein Per-Person-Mail im Batch.
- Einzelscan in der Akte: weiter eine Mail möglich (manueller Kontext).

### 5. Metriken

| Metrik | Bedeutung |
|--------|-----------|
| `last_monitored_at` | Letzter Batch-/Einzel-Scan pro Person |
| Coverage % | Anteil aktiver Personen mit Scan in den letzten *X* Tagen (UI-Hinweis) |
| Batch-Ergebnis | `scanned`, `new_links`, `alerts`, `email`, `coverage` |

---

## Konfiguration (Env)

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `WATCHLIST_SCAN_BATCH` | `25` | Rolling-Rest nach high-prio im Nacht-Cron |
| `WATCHLIST_SCAN_HIGH_PRIORITY_CAP` | `50` | Max. high-prio Personen jede Nacht |
| `WATCHLIST_SCAN_DELAY_SEC` | `2` | Pause zwischen Personen (0 = aus) |
| `WATCHLIST_SCAN_MANUAL_LIMIT` | `5` | UI-Button «Liste fortsetzen» |
| `WATCHLIST_NOTIFY_EMAILS` | — | Digest-Empfänger (kommagetrennt) |

Formel grob: für Zyklus in **D** Tagen braucht man  
`Batch ≥ ceil(aktive_Personen / D)`.

---

## MVP (umgesetzt 2026-08-12)

- Rolling-Auswahl: nie gescannt / ältestes `last_run_at` zuerst  
- Sequentiell + konfigurierbarer Delay + Jitter  
- Env-Batch für Cron; manueller Limit in UI  
- API/UI: `last_monitored_at`, Abdeckungs-Hinweis  
- Digest nur bei neuen Alerts im Batch  

## MVP Follow-up (2026-08-12, D9Performance-Lücke)

- Fall eröffnen / bestätigen → Organe auf Watchlist mit `scan_priority=high`  
- Nacht: alle high-prio (Cap 50) + Rolling-Rest  
- Admin-Button «Priorisierte jetzt prüfen»  
- Playbook: [`WATCHLIST_FRAUD_FOLLOWUP.md`](WATCHLIST_FRAUD_FOLLOWUP.md)

## Später (nicht MVP)

- Explizite Priority-Queue-Tabelle / Fairness-Weights  
- Exponential Backoff + Circuit-Breaker gegen Moneyhouse  
- Firmen-Watchlist-Monitoring analog  
- Dedizierte Worker-Queue (Celery/Redis) nur falls Batch-Jobs die App blockieren  

---

## Offene Produktfragen

1. Gewünschte **Vollabdeckung**: ca. **7** oder **30** Tage? (bestimmt Batch-Größe)  
2. **Max. API-Calls / Nacht** (Moneyhouse+Zefix), harte Obergrenze?  
3. Wer soll den **Digest** erhalten (`WATCHLIST_NOTIFY_EMAILS`)?  
