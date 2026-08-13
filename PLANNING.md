# Planung / Ideen (Admin)

Quelle: [`data/planning.json`](data/planning.json) · 0 Einträge

## Auth / Admin (ausstehend)

Ausführlicher Umsetzungsplan (noch nicht gebaut):

- **[docs/AUTH_ADMIN_2FA_PLAN.md](docs/AUTH_ADMIN_2FA_PLAN.md)** — Benutzerverwaltung (Rollen, Soft-Delete) und 2FA (TOTP + Backup-Codes)

## Watchlist / Bulk-Scan (ausstehend)

Pipeline für Fraud-Testlauf (mehrere Firmennamen → Scan → Auswahl → Watchlist Firmen+Personen → CSV Export):

- **[docs/WATCHLIST_BULK_SCAN_PLAN.md](docs/WATCHLIST_BULK_SCAN_PLAN.md)** — Architektur, UX, Phasen, offene Fragen
- **[docs/WATCHLIST_SCAN_SCALING.md](docs/WATCHLIST_SCAN_SCALING.md)** — Rolling-Scan der Personen-Watchlist ohne API-Spam
- **[docs/WATCHLIST_MONITORING.md](docs/WATCHLIST_MONITORING.md)** — Cron / Limits / Digest-E-Mail
- **[docs/SHAB_DAILY_WATCHLIST.md](docs/SHAB_DAILY_WATCHLIST.md)** — Tägliche SHAB-Publikationen lokal speichern + gegen Watchlist matchen (Phase 1 MVP; Backfill später)

## Firmenanalyse UX (ausstehend)

- **Layout-Cleanup Überblick:** Graph (Beziehungsnetzwerk) zu weit unten nach Firm-Bar / Banners / Suchweite — UX-Vorschläge im Chat 2026-08-12, noch **nicht umgesetzt** (Approval vor Implementierung).
