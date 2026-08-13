# Changelog

Alle wichtigen Änderungen an **Lynx** stehen hier.

Format nach [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Wo finden?** In der App unter **Changelog** (Account-Menü oder «Mehr») — Seite `/changelog`.  
Die Texte sind bewusst **einfach gehalten**, damit das ganze Team sie versteht.

## [Unreleased]

### Added

- **Akte: Als Verdächtig markieren**: In der Bestätigung Tag «In Abklärung», Firma + Organe auf die Watchlist, Akte wird geschlossen.
- **Akte öffnen → Firmen-Watchlist**: Beim Eröffnen landet die Firma (nicht nur Organe) auf der Firmen-Watchlist.
- **L5 im Hintergrund**: Fehlt der Cache für Suchweite 5, startet Lynx den Scan beim Akte-Öffnen async — die Akte ist sofort nutzbar.
- **Admin Inkognito**: Admins können im Account-Menü «Inkognito» einschalten. Dann erscheinen ihre Firmen-Suchen **nicht** in der Team-Historie.
- **Benutzer endgültig löschen**: Nach dem Deaktivieren kann ein Admin den Benutzer komplett entfernen (nicht nur deaktivieren).
- **SHAB-Tagesarchiv (Schweiz)**: Jede Nacht speichert Lynx die aktuellen Handelsregister-Meldungen des Tages. Steht jemand auf der Watchlist, landet der Treffer im **Posteingang**.
- **Watchlist: Firmen und Personen**: Eigene Tabs für Firmen und Personen; Listen als CSV exportierbar (Name und Adresse).
- **Bulk-Scan (Admin)**: Viele Firmennamen auf einmal einfügen → Lynx sucht sie → du wählst aus → auf die Watchlist.
- **«In Abklärung» → Watchlist**: Wenn du eine Firma als «In Abklärung» markierst, kommen Firma **und aktuelle Organe** automatisch auf die Watchlist. (Markierung entfernen ändert die Watchlist nicht.)
- **Priorisierte Überwachung nach Betrugsfall**: Bei einem Fall werden betroffene Personen/Firmen bevorzugt nachgeprüft — damit Nachfolge-Mandate nicht untergehen.
- **E-Mail bei Watchlist-Treffern**: Wenn E-Mail (SMTP) eingerichtet ist, erhältst du Hinweise zu neuen Mandaten/Verknüpfungen (Sammelmail nach dem nächtlichen Scan oder nach «Liste fortsetzen»).
- **Demo-Firma ohne Live-API**: Eine feste Demo-Firma zum Vorführen und Testen — ohne echte Register-Abfragen.
- **Warnungen Adresse / Organe auch bei älteren Firmen**: Frische Änderungen an Sitz oder Organen werden auch bei schon länger bestehenden Firmen angezeigt (nicht nur bei Neugründungen).
- **Benutzerverwaltung (Admin)**: Rolle ändern, deaktivieren / wieder aktivieren; Schutz, dass der letzte Admin nicht «weggeklickt» wird.
- **Pflicht-2FA**: Alle Rollen brauchen Zwei-Faktor-Login (App-Code + Backup-Codes). Konto-Seite `/account` für Status und Passwort ändern. Admins können 2FA für andere zurücksetzen.

### Changed

- **Akte-Flow vereinfacht**: Stepper nur noch In Prüfung → Bestätigung → Dokumentation; Reporting/Compliance ausgeblendet (Backend bleibt). Zahlungshit nur bei Bestätigung. Sicherung mit Ja/Nein. Namen kopieren statt PDF. Banner «Nächster Schritt» entfernt.
- **Watchlist-Oberfläche**: Tabs Firmen | Personen | Posteingang | Fälle; Bulk-Scan nur für Admins.
- **Login erst nach 2FA**: Die volle Sitzung gibt es erst nach dem zweiten Faktor (oder nach dem erstmaligen Einrichten).
- **Firmenanalyse aufgeräumt**: Status (unvollständig / Cache / nächster Schritt) steht **unter** dem Graphen; Warnungen kompakt unter der Firm-Leiste. Organigramm-Schalter entfernt (nur noch Netzwerk-Graph). «In Abklärung» und Akte bleiben gut sichtbar; HR / Profiler unter **Mehr**.
- Personen-Seitenleiste: Geschlecht dezent (m/w); Heimatort-Fussnote entfernt.
- Graph-Verschieben in Microsoft Edge funktioniert wieder zuverlässig.

### Fixed

- Demo-Firma: Fehler behoben, wenn die Daten-Datei im Docker-Setup nicht gefunden wurde.

## [1.2.0] - 2026-08-12

### Added

- **Suchweite 1–5** in der Firmenanalyse (statt nur «Ebenen»): klarere Schritte für Register, Mandate und Netzwerk erweitern
- Firm-Leiste mit Name/UID zum Kopieren; Tag **«In Abklärung»** (sichtbar fürs Team)
- **Team-Suchhistorie** auf der Firmenanalyse-Startseite (eigene und Team-Suchen)
- Statuskarte bei unvollständiger Personensuche: Moneyhouse-Treffer bestätigen oder ignorieren
- Moneyhouse als Ergänzung für Personen/Mandate; Firmenidentität weiter über Zefix
- SHAB-Personenparser für italienische und französische Meldungen (z. B. Tessin / Romandie)
- Organigramm neben dem Graphen; Kopier-Helfer für Netz-/Akte-Daten
- Ruhige Idle-Startseite («Firmennetzwerke analysieren»)
- Zeitleiste: Personen rein/raus, lesbarere SHAB-Texte
- In-App Feedback / Wishlist; Admin-Planung unter `/admin/planning`

### Changed

- Mandats-Entdeckung: Zefix/SHAB zuerst, Moneyhouse nur Ergänzung
- Idle-Start ohne Puls-Shortcuts — ruhiger Such-Start
- Personen im Graph: klareres Zusammenführen von Namen/Rollen; ehemalige Mitglieder bleiben erkennbar
- SHAB-Zeitleiste und Meldungen lesbarer
- Docker: System-Chromium statt Download von extern (hilft auf manchen VPS)

### Fixed

- Moneyhouse bei häufigen Nachnamen: bessere Zuordnung zur bekannten Firma
- Deep-Analyse zeigt Moneyhouse- und SHAB-Treffer getrennt
- Lange Suchweite-5-Scans: Timeouts erhöht (kein Abbruch mitten im Scan durch Proxy/Health-Check)

## [1.1.0] - 2026-07-31

### Added

- In-App Changelog-Seite (`/changelog`) und Link im Account-Menü
- Admin-Zugang über Account-Dropdown
- L4/L5 Firmennetz-Cache (7 Tage, geteilt) inkl. «Neu laden»
- Watchlist-Personenflags: «Unerwünschter Kunde» und «AML»
- Firmenknoten im Netz öffnen die Firmenanalyse in neuem Tab

### Changed

- Ehemalige Personen im Graph besser erkennbar; ehemalige Mitglieder standardmässig eingeklappt
- Branchen-Hinweis: Fokus auf den Tätigkeitskern
- Session-Cookies: `Secure` nur wenn `DOMAIN` gesetzt (lokales HTTP ok)

### Fixed

- Changelog- und L4/L5-Cache nach Deployment/Neustart
- Falsch-positive Branchenmeldung bei abweichendem Firmenzweck

## [1.0.0] - 2026-07-29

### Added

- Erste Lynx-Basis: Firmenanalyse, Fälle, Watchlist, Profiler, Website-Check
- Anmeldung mit Rollen (Admin / Case Manager / Compliance)
- Zefix-/SHAB-Netzwerk, Dokumentation/Akte, Compliance-Abläufe
- VPS-Setup: Docker Compose, Caddy, produktionsnahe Defaults
- Deploy-Dokumentation (`deploy/README.md`, `deploy/HETZNER.md`)
