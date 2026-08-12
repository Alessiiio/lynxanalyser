# Plan: Bulk-Scan → Watchlist (Firmen + Personen)

**Status:** Phase-1-MVP implementiert (2026-08-12)  
**Datum:** 2026-08-12  
**Auslöser:** Bestätigter Pipeline-Wunsch für Fraud-Testlauf (mehrere Firmennamen → Lynx-Scan → Auswahl → Watchlist → Export DS)  
**Sprache:** DE (Team)

---

## Beschlossen (2026-08-12)

| # | Entscheidung | Detail |
|---|--------------|--------|
| 1 | **Pipeline** | Mehrere Firmennamen → Lynx **scannt** → Output zur **Auswahl** → gewählte Zeilen → **Watchlist** |
| 2 | **Watchlist-Inhalt** | **Firmen und Personen**, **trennbar** (Filter/Tabs) für gezielte Suche |
| 3 | **Export-Felder** | **Firmenname, Adresse** (ausreichend für DS); Personen: **Name, Adresse/Wohnort** |
| 4 | **«In Abklärung»** | Erstellt **automatisch** Watchlist-Einträge: **Firma + aktuelle Organe** (Personen) |
| 5 | **Suchweite Bulk-Scan** | Default **3** |
| 6 | **Laufweise** | **Async** mit Fortschrittsanzeige (Polling) |
| 7 | **Bulk-Scan Rechte** | Nur **Admin** (UI + API) |
| 8 | **Dedup Firmen-Watchlist** | Primär **UID**, Fallback **Name** (Regel **A**) |
| 9 | **Tag entfernen** | Watchlist-Einträge **bleiben** (Tag ≠ Watchlist-Lebenszyklus; sicherer Default) |

**Regel:** Antworten oben sind verbindlich für den Bau. Personen/Organe bei «In Abklärung» sind freigegeben (Frage #3 = Firma + Organe).

---

## Ist-Zustand (nach MVP)

| Bereich | Stand |
|--------|--------|
| Personen-Watchlist | SQLite `watched_persons` (+ Links, Scans, Status-Historie); UI `/watchlist` |
| Firmen-Watchlist | `watched_companies` + Tab **Firmen** + CSV-Export |
| «In Abklärung» | Tag + Auto-Upsert Firma + aktuelle Organe → Watchlist |
| Bulk-Scan | Admin-Tab: Paste → Job (`bulk_scan_jobs` / `bulk_scan_items`) → Progress → Auswahl → Watchlist |
| Export CSV | Firmen: Firmenname;Adresse · Personen: Name;Adresse |

Relevante Dateien: `app/database.py`, `app/hr_network/watched_companies.py`, `app/hr_network/bulk_scan.py`, `app/hr_network/under_investigation_watchlist.py`, `app/hr_network/company_tags.py`, `app/routes/network.py`, `static/watchlist.html` / `watchlist.js`, `static/company-analysis.js`.

---

## Ziel-Architektur (umgesetzt)

### 1. Datenmodell

- `watched_companies` — Dedup A (UID → Name)
- `watched_persons` — unverändert, Organe via bestehende Upserts
- `bulk_scan_jobs` / `bulk_scan_items` — Async-Job + kompaktes `result_json`

### 2. «In Abklärung» → Watchlist

Beim `POST /api/company-tags` (`under_investigation`):

1. Tag speichern  
2. Upsert Firma (`source_reason=under_investigation`)  
3. Upsert **aktuelle** Organe als Personen  

Personenquelle: Client sendet `persons` aus `lastAnalysis.persons_table` (bevorzugt). Server hat **keine** Session-Kopie von `lastAnalysis` — sonst L2-Fetch (`build_fraud_network` level=2).

Beim Entfernen des Tags: **kein** Auto-Remove von der Watchlist.

### 3. Bulk-Scan (Admin)

- Default Suchweite **3**, `max_person_searches=4`, Concurrency **2**
- Worker: Background-Task + DB-Progress; UI pollt `GET /api/bulk-scan/{id}`

### 4. Auswahl → Watchlist

`POST /api/watchlist/bulk-add` mit `{type: company|person, …}` (Admin).

### 5. Export CSV

- `GET /api/watched-companies/export.csv`
- `GET /api/watched-persons/export.csv`

---

## UX-Flow

```
1. Eingabe     Paste (eine Firma pro Zeile) und/oder CSV-Spalte Name
2. Optionen    Suchweite (Default 3)
3. Start       Job anlegen → Progress (n/N)
4. Ergebnisse  Tabelle: Firma | UID | Adresse | Organe | Checkbox
5. Übernehmen  «Auswahl zur Watchlist»
6. Listen      /watchlist → Tabs Firmen | Personen
7. Export      CSV je Tab
```

---

## Phasen

### Phase 1 — MVP Testlauf ✅

- Paste-Namen → Bulk-Job → Progress → Ergebnis-Tabelle  
- Auswahl Firmen + Personen → Watchlist  
- Tabs Firmen / Personen  
- CSV-Export  
- «In Abklärung» → Firma + aktuelle Organe  

### Phase 2 — später

- CSV-Upload-Datei, Dedup-Reports, Re-Scan, Adress-Refresh  
- Monitoring analog Personen (Firmen-Änderungen / SHAB)  
- Grössere Queues, Retry, Admin-Quota  
- Evtl. unified entity-model  

---

## API (MVP)

| Methode | Pfad | Zweck | Rolle |
|---------|------|--------|-------|
| POST | `/api/bulk-scan` | Job anlegen | Admin |
| GET | `/api/bulk-scan/{id}` | Status + Items | Admin |
| POST | `/api/watchlist/bulk-add` | Auswahl → Watchlist | Admin |
| GET | `/api/watched-companies` | Liste | Auth (wie Watchlist) |
| GET | `/api/watched-companies/export.csv` | DS-Export Firmen | Auth |
| GET | `/api/watched-persons/export.csv` | DS-Export Personen | Auth |
| POST | `/api/company-tags` | Tag + Watchlist-Side-Effect | Auth |

---

## Nicht-Ziele (dieser Plan)

- Keine Celery/Redis-Infrastruktur  
- Kein Ersatz der Fall-Akte (`company_cases`) durch Watchlist  
- Kein vollständiges Personen-Monitoring für neu gebulk-te Firmen im MVP  

---

## Offene Fragen — beantwortet

1. **Suchweite Bulk-Scan:** Default **3**  
2. **Laufweise:** Async mit Fortschrittsanzeige  
3. **«In Abklärung» → Watchlist:** Firma **+ aktuelle Organe**  
4. **Bulk-Scan Rechte:** nur **Admin**  
5. **Dedup:** UID primär, Name-Fallback (**A**)  
