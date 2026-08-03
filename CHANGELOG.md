# Changelog

All notable changes to **Lynx** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- In-App Feedback / Wishlist (Floating-Formular, API, Pflege unter `/feedback`)
- Keep-a-Changelog-Quelle `CHANGELOG.md` als kanonische Versionshistorie
- Hilfsskripte: Conventional-Commits → Changelog-Vorschläge; erledigte Wishlist → Changelog-Vorschläge

## [1.1.0] - 2026-07-31

### Added

- In-App Changelog-Seite (`/changelog`) und Account-Menü-Link
- Admin-Zugang über Account-Dropdown
- L4/L5 Firmennetz-Disk-Cache (7 Tage, geteilt) inkl. «Neu laden» / Cache-Hinweis
- Watchlist-Personenflags: «Unerwünschter Kunde» und «AML»
- Firmenknoten im Beziehungsnetz öffnen Firmenanalyse in neuem Tab

### Changed

- Ehemalige Personen im Graph kontrastreicher; ehemalige Mitglieder standardmässig eingeklappt
- Branchen-Hinweis: Match auf Tätigkeitskern statt Handelsregister-Boilerplate
- Session-Cookies: `Secure` nur wenn `DOMAIN` gesetzt (lokales HTTP)

### Fixed

- Changelog- und L4/L5-Cache-Verhalten nach Deployment/Neustart
- Falsch-positive Branchenmeldung («100% der Fälle») bei abweichendem Firmenzweck

## [1.0.0] - 2026-07-29

### Added

- Erste veröffentlichte Lynx-Basis: Firmenanalyse, Fälle, Watchlist, Profiler, Website-Check
- FastAPI + SQLite, rollenbasierte Anmeldung (Admin / Case Manager / Compliance)
- Zefix-/SHAB-Netzwerk (Ebenen 1–5), Dokumentation/Akte, Compliance-Flows
- VPS-taugliches Setup: Docker Compose, Caddy, gehärtete Production-Defaults
- Deploy-Dokumentation (`deploy/README.md`, `deploy/HETZNER.md`)
