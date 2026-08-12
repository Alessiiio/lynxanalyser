# Changelog

All notable changes to **Lynx** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-08-12

### Added

- **Suchweite 1–5** in der Firmenanalyse (UI statt reiner «Ebenen»): Phasen-Karten für Register (SW2), Mandate / Netzwerk erweitern (SW3+), Mini-CTA «Netzwerk erweitern»; SW3-Label «Weitere Firmen (Mandate)»
- Firm-Leiste mit Name/UID-Klick zum Kopieren, Status und Aktionen; Tag **«In Abklärung»** (team-sichtbar, SQLite) — Badge in Autocomplete und Team-Suchen, unabhängig von der Fall-Akte
- **Team-Suchhistorie** auf der Firmenanalyse-Startseite: eigene und Team-Suchen (API + SQLite), mit Benutzer und Zeit; «Eigene leeren» entfernt nur die eigenen Einträge
- Statuskarte **unvollständige Personensuche / Identitätsbestätigung**: Moneyhouse-Treffer bestätigen oder ignorieren; `confirm-identity` baut das Netz **inkrementell** nach (ohne vollen Neu-Scan, wenn Graph/Cache vorliegt)
- Moneyhouse-Personensuche als **Fill-in** für Mandate (Watchlist + Firmennetz SW3+), mit Seed-Firma als Soft-Gate (Boost / Soft-Accept); Firmenidentität immer über Zefix
- SHAB-Personenparser für **italienische und französische** SOGC-Meldungen (Rollen/Nationalität, z. B. TI / Romandie)
- Organigramm-Ansicht neben dem Graphen; Kopieren-Helfer für Netz-/Akte-Daten
- Idle-Startseite Firmenanalyse (neutrale Tagline «Firmennetzwerke analysieren»)
- Zeitleisten-Typen: Personen rein/raus, bereinigter SHAB-Volltext (ohne harte Mittelkürzung)
- In-App Feedback / Wishlist (Floating-Formular, Pflege unter `/feedback`)
- Admin-Planung unter `/admin/planning`

### Changed

- Mandats-Entdeckung: **Zefix/SHAB primär**, Moneyhouse nur Ergänzung (`MONEYHOUSE_PERSON_SEARCH`, Default an); Cache-Key v6
- Idle-Start: Pulse-Shortcuts (offene Fälle / Watchlist / Schnelllinks) entfernt zugunsten ruhiger Such-Startseite
- Personen-Identität im Graph: Merge über Namens-Fingerprint / Mittelname (kein Merge unterschiedlicher Vornamen); eine Kante Person↔Firma mit zusammengeführten Rollenlabels; Former-Status bleibt erhalten; Case-Flags robuster indiziert
- SHAB-Timeline und Meldungen lesbarer (volle bereinigte Meldung, kompakte Personen-Chips)
- Docker-Image: System-Chromium via apt statt Playwright-CDN-Download (geo-block auf manchen VPS-Netzen)

### Fixed

- Moneyhouse-Identität bei gängigen Nachnamen: bevorzugte Seed-Bestätigung über `relatedCompanies`; Soft-Gate wenn MH der Zefix-Seed-Firma hinterherhinkt
- Deep-Analyse-Status zeigt Moneyhouse- und SHAB-Treffer getrennt
- Lange Suchweite-5-Scans: Caddy-/Proxy- und Health-Timeouts erhöht (kein HTML-502 / Container-Neustart mitten im Scan)

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
