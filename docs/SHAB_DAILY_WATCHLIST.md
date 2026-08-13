# Plan: Tägliche SHAB-Publikationen → Watchlist-Match

**Status:** Phase 1 MVP implementiert (+ leichtes Phase-2 Match)  
**Datum:** 2026-08-13  
**Sprache:** DE  
**Verwandt:** [`WATCHLIST_MONITORING.md`](WATCHLIST_MONITORING.md) · [`WATCHLIST_SCAN_SCALING.md`](WATCHLIST_SCAN_SCALING.md) · [`WATCHLIST_FRAUD_FOLLOWUP.md`](WATCHLIST_FRAUD_FOLLOWUP.md)

---

## Beschlossen (2026-08-13)

| Frage | Entscheidung |
|-------|----------------|
| **Lokal speichern?** | **Ja** — eigenes go-forward Archiv in SQLite (Moneyhouse-komplementär, nicht Ersatz) |
| **Scope** | **Ganze Schweiz** (kein Kantonsfilter) — z. B. GE-Takeover muss sichtbar sein, auch wenn Seeds ZH sind |
| **Start** | **Ab heute go-forward** (Fenster gestern→heute); **Monats-Backfill = später** (stub/Doku, blockiert MVP nicht) |
| **Retention** | MVP: **unbegrenzt** (`SHAB_DAILY_RETENTION_DAYS=0`); Phase-3 Job später; Empfehlung Rohdaten ≥90 Tage behalten |

---

## Empfehlung

**Ja — als komplementäres Signal**, nicht als Ersatz für Moneyhouse-Personenscans.

| Quelle | Stärke | Schwäche |
|--------|--------|----------|
| **SHAB / SOGC (täglich)** | Offizielle Organ-/Handelsregister-Publikationen (Eintritt, Austritt, Neugründung, Mutationen) | Nur was publiziert wird; Namens-Matching; keine MH-«related companies» |
| **Moneyhouse (pro Person)** | Mandatsgraph, verwandte Firmen, oft früher/breiter sichtbar | Rate-Limits, N Calls/Nacht, Identitäts-Ambiguität, kein amtlicher Organ-Status |

**Fazit:** SHAB-Daily = «hat jemand aus der Watchlist heute offiziell eine Organ-/Firmenänderung?» · MH-Rolling = «welche Mandate/Firmen erscheinen im MH-Graph?» Beide behalten; SHAB skaliert besser bei wachsender Watchlist.

---

## Ist-Zustand vs. Lücke

### Bereits vorhanden

| Baustein | Wo | Nutzen |
|----------|-----|--------|
| ZefixREST `POST /shab/search.json` | `app/hr_network/zefix_rest.py`, `person_search.py` | Monatliche Publikationssuche (kein Scrape) |
| Monats-Cache 24 h TTL | `data/shab_month_cache/` (~GB-groß lokal) | Wiederholte Personensuchen; **kein** go-forward Daily-Store |
| Personen-Parser | `app/hr_network/shab_parser.py` (`iter_named_persons_in_message`, Timeline) | Namen + Rollen aus Pub-Text |
| Namens-Identität | `app/hr_network/person_names.py` (`names_same_person`) | Matching Watchlist ↔ SHAB-Label |
| Watchlist-Cron 04:15 | `scheduler.py` → `run_person_monitoring` | **Moneyhouse→Zefix**; SHAB nur optional/`include_shab=false` im Batch |
| Alerts + Digest | `NetworkAlert`, `notify_email.notify_watchlist_new_hits` | Posteingang + SMTP-Digest |

### Neu (Phase 1 + leichtes Phase 2)

| Baustein | Wo |
|----------|-----|
| Daily-Ingest CH-weit | `app/hr_network/shab_daily.py` · Cron **05:45** · Env `SHAB_DAILY_INGEST=1` |
| SQLite-Archiv | `shab_daily_publications` / `shab_daily_ingest_runs` / `shab_daily_matches` in `fraud_checks.db` (Docker-Volume) |
| Watchlist-Match | optional (`SHAB_DAILY_MATCH=1`): `NetworkAlert` mit `Quelle: shab_daily` |
| UI-Hinweis | Watchlist Personen-Tab: «SHAB-Tagesarchiv: letzter Lauf …» |
| Admin-API | `GET /api/shab-daily/status`, `POST /api/shab-daily/run` (Admin) |
| Backfill | **nicht** gebaut — `backfill_stub` / `python -m app.hr_network.shab_daily --backfill` |

---

## Wie es läuft (Ops)

1. Env: `SHAB_DAILY_INGEST=1` (sonst Cron no-op; manueller Admin-Lauf mit `force`).
2. Cron **05:45** (nach MH 04:15): Fenster **gestern–heute**, **ohne** `registryOffices` (= CH-weit).
3. Pagination bis `hasMoreResults=false`; Upsert per **`shabId`** (idempotent).
4. Personen aus Message parsen → JSON `person_names`.
5. Wenn Match an: Watchlist (`active`/`confirmed_fraud`) ↔ `names_same_person` → Alert + optional neuer `PersonCompanyLink`; Dedup über `shab_daily_matches`.
6. Retention-Job: später; Rohdaten bleiben bis dahin.

**Warum CH-weit:** Kantonsfilter würde z. B. eine Organübernahme in GE unsichtbar lassen, wenn die Watchlist-Person nur über ZH-Seeds bekannt ist.

**Backfill fehlender Monate:** bewusst später — MVP startet go-forward; Historie on-demand weiter über bestehenden Monatsscan / Cache.

---

## Wie SHAB in diesem Codebase funktioniert

1. **API, kein HTML-Scrape:** ZefixREST `/shab/search.json` mit Basic-Auth (wie Zefix).
2. **Fenster:** Payload `publicationDate` / `publicationDateEnd` (Daily: gestern→heute).
3. **Scope:** optional `registryOffices`; **ohne Filter = CH-weit** (Pagination `offset` / `maxEntries`, `hasMoreResults`).
4. **Antwort:** Firmenliste mit `shabPub[]` (`shabId`, `shabDate`, `message`, `mutationTypes`, `registryOfficeCanton`, …).
5. **Öffentliche ZefixPublicREST** wird im Kommentar als langsamere Day-Scan-Alternative erwähnt; produktiv für SHAB-Suche ist ZefixREST.

**Volumen (Orientierung):** kantonal oft ~100–200 Pub-Einträge/Werktag; CH-weit mehr, aber ein Tagesfenster bleibt paginierbar (meist wenige Seiten à 5000).

---

## Ziel-Architektur

```
Cron 05:45 (wenn SHAB_DAILY_INGEST=1)
  → ZefixREST shab/search (publicationDate=gestern, End=heute)  # CH-weit
  → Upsert shab_daily_publications (key=shab_id)
  → Parse Personen (shab_parser)
  → [optional] Match names_same_person gegen watched_persons
  → NetworkAlert + ShabDailyMatch (+ Digest)
```

### Datenmodell

| Store | Inhalt | Retention |
|-------|--------|-----------|
| `shab_daily_publications` | Tag, ehraid/uid, Firma, Roh-message, shabDate, Kanton, mutation_types, person_names | unbegrenzt (MVP); später Job |
| `shab_daily_ingest_runs` | Fenster, Status, Counts | dauerhaft (klein) |
| `shab_daily_matches` | shab_id × person_id (Idempotenz Match) | dauerhaft |
| `PersonCompanyLink` / `NetworkAlert` | Treffer `Quelle: shab_daily` | wie heute |

**Nicht** den bestehenden `shab_month_cache` (7 GB+) als Archiv missbrauchen.

---

## MVP-Phasen

### Phase 0 — Entscheidungen ✅

Siehe **Beschlossen** oben.

### Phase 1 — Ingest only ✅

- Cron + Modul + SQLite-Upsert + Status-Hinweis / API.
- Scope: CH-weit.
- Backfill: stub.

### Phase 2 — Match + Alert (leicht) ✅ MVP

- Parse + Match gegen `watched_persons`.
- `NetworkAlert` + Digest (`source=shab_daily_batch`).
- Dedup über `shab_daily_matches`; neuer Link nur wenn Firma noch unbekannt.

### Phase 3 — Ops / Härte (offen)

- Retention-Job (`SHAB_DAILY_RETENTION_DAYS`).
- Metriken / False-Positive-Flagging.
- Optional UI-Filter «SHAB heute».
- Multi-Monat-Backfill CLI.

### Nicht-Ziele (vorerst)

- Vollhistorie aller SHAB-Jahre lokal.
- Ersatz des MH-Nachtjobs.
- Celery/Redis.
- Scrape von shab.ch HTML.

---

## Env

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `SHAB_DAILY_INGEST` | `0` | Cron/Job aktiv |
| `SHAB_DAILY_MATCH` | `1` | Watchlist-Match nach Ingest |
| `SHAB_DAILY_RETENTION_DAYS` | `0` | `0` = unbegrenzt (noch kein Prune-Job) |

Manuell: `python -m app.hr_network.shab_daily` (force) · `--no-match` · `--backfill` (Stub).

---

## Risiken & Mitigations

| Risiko | Mitigation |
|--------|------------|
| Falsch-Positive (häufige Namen) | `names_same_person`; Residence-Boost; Severity low |
| Speicherwachstum | Phase-3 Retention; nur Message+Metadaten |
| API `hasMoreResults` unvollständig | Pagination + Safety-Cap 50 Seiten; Run-Status error |
| MH und SHAB doppelte Alerts | Match-Log + Links; Message trägt SHAB-ID |
| Feiertage / leere Tage | Idempotent ok |

---

## Relevante Dateien

- `app/hr_network/shab_daily.py` — Fetch, Upsert, Match, Status
- `app/hr_network/scheduler.py` — Cron 05:45
- `app/database.py` — Tabellen
- `app/hr_network/person_search.py` — Monatsscan + Cache (unverändert)
- `app/hr_network/shab_parser.py` — Personen aus Meldung
- `app/hr_network/person_monitoring.py` — MH-Scan; Coverage + SHAB-Hinweis
- `static/watchlist.js` / `watchlist.html` — Statuszeile
- `tests/test_shab_daily.py` — Upsert-Idempotenz
