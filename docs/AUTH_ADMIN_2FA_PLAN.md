# Plan: Benutzerverwaltung & 2FA (TOTP)

**Status:** Umsetzung (Phase A + B-MVP, Pflicht-2FA Tag 1)  
**Datum:** 2026-08-12  
**Auslöser:** Internes Test-Feedback (User löschen / Rollen anpassen; 2FA Login mit Authenticator + Backup-Codes)  
**Sprache:** DE (Team)

## Beschlossen (2026-08-12)

| # | Entscheidung | Detail |
|---|--------------|--------|
| 1 | **Soft-Delete only** | `active=false` — kein Hard-Delete im MVP |
| 2 | **Self-Demote erlaubt** | Admin → Nicht-Admin nur wenn **≥2 aktive Admins** (nach Demote bleibt ≥1) |
| 3 | **2FA Self-Service** | Enrollment für **alle Rollen** (nicht nur Admin-UI) |
| 4 | **2FA Pflicht ab Tag 1** | Nach Passwort: enroll oder TOTP/Backup **bevor** volle Session / App-Zugang |
| 5 | **2FA-Reset** | Nur durch **anderen Admin** — kein Env-Break-glass / `DISABLE_2FA_USER` |
| 6 | **`TOTP_ENCRYPTION_KEY`** | Separater Key in `.env` / `.env.example` (nicht aus `SESSION_SECRET` in Prod) |

Offene Fragen C.4 #1–#6 damit geschlossen. Backup-Codes: **10** alphanumerisch; Role-Change: DB-Role sofort, Session-Feld bei Self-Change aktualisieren.

---

## Ist-Zustand (Kurz)

| Bereich | Heute |
|--------|--------|
| Auth | Cookie-Session (`lynx_session`, Starlette `SessionMiddleware`), `max_age` 8h, `same_site=lax`, `https_only` aus Config |
| Passwörter | `bcrypt` direkt (`app/auth.py`) — **kein** passlib; Timing-Ausgleich bei Login |
| User-Modell | SQLite `users`: `id`, `username` (unique), `password_hash`, `display_name`, `role`, `active`, `created_at` |
| Rollen | `admin`, `case_manager`, `compliance` (+ Legacy `analyst` → `case_manager`) |
| Rechte | Grobkörnig: `require_role(...)`; **Admin umgeht** alle Role-Checks |
| Admin-UI | `/admin`: Liste, Anlegen, Passwort-Reset — **kein** Rollen-Edit, **kein** Löschen/Deaktivieren |
| APIs | `GET/POST /api/users`, `POST /api/users/{id}/reset-password`, `POST /api/me/password`, Login/Logout/`/api/me` |
| Invite | **Keiner** — Admin setzt Initialpasswort; Bootstrap über `SEED_*_PASSWORD` / optional `SEED_ALESSIO_PASSWORD` |
| 2FA | **Nicht vorhanden**; Login setzt Session sofort nach Passwort |
| Secrets-Libs | `cryptography` bereits in `requirements.txt`; **kein** `pyotp` |
| Audit | Kein Auth-Audit-Log; Login-Rate-Limit IP-basiert (`LOGIN_RATE_LIMIT_PER_MINUTE`) |
| Planung-Board | `/admin/planning` + `data/planning.json` — getrennt; dieser Plan lebt als Markdown |

Relevante Dateien: `app/auth.py`, `app/routes/auth.py`, `app/routes/deps.py`, `app/database.py` (`User`, `seed_default_users`), `app/main.py` (Middleware), `static/admin.html` / `admin.js`, `static/login.html`.

---

## Epic A — Benutzerverwaltung

### A.1 Aktuelles Modell

- **Felder:** siehe Tabelle oben; `active=False` blockiert Login und Session-Load bereits (`load_user_from_session`).
- **Rollen:** drei feste Strings in `ALL_ROLES`; UI-Labels in `ROLE_LABELS` / Nav (`static/ui-common.js`).
- **Keine** feingranularen Permissions (keine Bitmaske, keine Feature-Flags pro User).
- **Seed:** leere DB → drei Accounts; `FORCE_RESET_SEED_PASSWORDS=1` überschreibt Seed-User; Alessio-Konto separat.

### A.2 Anforderungen

| Fähigkeit | MVP | Später |
|-----------|-----|--------|
| Benutzer listen | ✅ (existiert) | Filter inaktiv / Suche |
| Rolle ändern | ✅ | optional display_name edit |
| Deaktivieren (Soft-Delete) | ✅ empfohlen | — |
| Hard-Delete | optional / nur mit Guard | wenn Compliance Hard-Purge verlangt |
| Passwort-Reset (Admin) | ✅ (existiert) | + Session-Invalidierung Hinweis |
| Self-Service Passwort | ✅ (existiert) | — |
| Letzten Admin schützen | ✅ | — |
| Self-Delete / Self-Demote Guard | ✅ | — |
| Invite-Link / E-Mail | ❌ | nur wenn Team es will |

**Guards (muss):**

1. **Last admin:** letzter aktiver User mit `role=admin` darf nicht deaktiviert, gelöscht oder zu Nicht-Admin degradiert werden.
2. **Self-delete:** Admin darf sich nicht selbst löschen/deaktivieren (sonst Lockout ohne zweiten Admin).
3. **Self-demote:** eigener Role-Downgrade von Admin → Nicht-Admin nur erlauben, wenn ≥1 anderer aktiver Admin existiert (oder ganz verbieten — siehe Open Questions).
4. **Username:** unveränderlich im MVP (vermeidet Audit-/Historie-Verwirrung); Anzeigename optional editierbar.

**Soft vs Hard Delete:**

- **Empfehlung MVP: Soft-Delete** = `active=False` (+ optional `deactivated_at`, `deactivated_by`).
  - Login und Session-Load greifen schon.
  - Username bleibt unique → Reaktivierung möglich; kein Orphan-Chaos bei `changed_by`-Strings.
- **Hard-Delete:** nur wenn kein Audit-Bezug nötig; Username freigeben. Nicht im ersten Slice, ausser explizit gewünscht.

### A.3 API (Vorschlag)

Alle Endpoints: `require_role("admin")`, Origin-Check wie übrige mutierende `/api/*`.

| Method | Path | Verhalten |
|--------|------|-----------|
| `PATCH` | `/api/users/{id}` | Body: `role?`, `active?`, `display_name?` — mit Guards |
| `DELETE` | `/api/users/{id}` | MVP: Soft (`active=False`) **oder** Alias auf PATCH; Hard nur mit `?hard=1` später |
| bestehend | `GET/POST /api/users`, Reset-Password | unverändert; Reset sollte bei deaktiviertem User 404/400 |

Antworten: weiterhin `user_public_dict` (+ ggf. `totp_enabled` später, ohne Secrets).

**Fehlercodes:** `400` Guard-Verletzung (deutsche `detail`), `404` unbekannt, `403` Nicht-Admin.

### A.4 UI (`/admin`)

In der bestehenden Benutzer-Sektion (`admin-user-card`):

- Dropdown **Rolle** (sofort speichern oder «Speichern»-Button — konsistent zum Rest: eher expliziter Button).
- Toggle / Button **Deaktivieren** / **Reaktivieren**.
- Bestätigungsdialog bei Deaktivieren (Username nennen).
- Deaktivierte User visuell absetzen (bereits «· inaktiv» vorgesehen).
- Keine neue Seite nötig.

### A.5 Security / Audit

- Nur Admins; CSRF/Origin bereits via `MutatingOriginMiddleware`.
- Nach Deaktivieren: bestehende Session des Opfers stirbt beim nächsten Request (`active` Check) — gut; **kein** serverseitiges Session-Store zum Revoken nötig.
- **Empfohlen (MVP-light):** App-Log-Zeile bei Role-Change / Deactivate (wer → wen, alt/neu). Eigenes `auth_audit`-Table erst wenn Compliance es verlangt.
- Passwort-Reset nach Soft-Delete: blockieren bis Reaktivierung.

### A.6 Phasen

| Phase | Scope | T-Shirt |
|-------|--------|---------|
| **A-MVP** | `PATCH` Rolle + `active`; Guards; Admin-UI | **S** |
| **A2** | display_name edit; bessere Confirm-UX; Filter | **XS** |
| **A3** | Hard-Delete + Username-Freigabe; Audit-Table | **M** |
| **A4** | Invite-Flow / Passwort-Set-Link | **L** (Out of Scope bis angefragt) |

---

## Epic B — 2FA TOTP + Backup-Codes

### B.1 Bibliotheken

| Bedarf | Empfehlung | Begründung |
|--------|------------|------------|
| TOTP | **`pyotp`** | Standard, RFC 6238, QR-URI (`provisioning_uri`) |
| QR-Bild | Client: `otpauth://` + Bibliothek im Browser **oder** Server: `qrcode` + PNG | MVP: Client-QR aus URI reicht oft |
| Backup-Codes hashen | bestehendes **`bcrypt`** (wie Passwörter) | konsistent, kein passlib nötig |
| TOTP-Secret at rest | **`cryptography.fernet.Fernet`** | `cryptography` schon Dependency |
| Key-Ableitung | `TOTP_ENCRYPTION_KEY` in `.env` (32-url-safe-base64) **oder** HKDF aus `SESSION_SECRET` mit festem Info-String | expliziter Key bevorzugen (Rotation ohne Session-Break) |

**Nicht empfohlen:** passlib zusätzlich einführen; eigene TOTP-Implementierung.

### B.2 Datenmodell (Vorschlag)

Neue Spalten an `users` (Migration analog bestehender `_migrate_*` in `database.py`):

| Spalte | Typ | Hinweis |
|--------|-----|---------|
| `totp_secret_encrypted` | Text/String nullable | Fernet-Ciphertext; null = nicht enrolled |
| `totp_enabled` | Boolean default False | erst true nach erfolgreicher Verify bei Enrollment |
| `totp_confirmed_at` | DateTime nullable | |
| `backup_codes_hash` | Text/JSON | Liste bcrypt-Hashes; Klartext nie speichern |
| `backup_codes_generated_at` | DateTime nullable | |

Optional separat: `totp_pending_secret_encrypted` während Enrollment (verhindert Half-State), oder pending nur in kurzlebiger Session.

**Backup-Codes:** z. B. 10 Codes à 8–10 alphanumerisch; einmal anzeigen; bei Nutzung Hash entfernen (single-use); bei Erschöpfung Admin-Reset oder Neu-Generierung nach TOTP.

### B.3 Enrollment-UX

1. Eingeloggt → Account/Sicherheit (MVP: Abschnitt unter `/admin` für Admins **und** Self-Service unter Account-Menü / kleine `/account`-Seite — Open Question).
2. «2FA einrichten» → Server erzeugt Secret, speichert encrypted (pending), liefert `otpauth://` + Base32 für manuelle Eingabe.
3. User scannt in Authy / Google Authenticator.
4. User gibt einen aktuellen 6-stelligen Code ein → Verify → `totp_enabled=True`, Backup-Codes generieren, **einmal** anzeigen + Download/Copy-Warnung.
5. «Ich habe Codes gespeichert» bestätigt → fertig.

Abbruch: pending Secret verwerfen.

### B.4 Login-Flow

```
POST /api/login {username, password}
  → ungültig: 401 (wie heute)
  → ok, kein 2FA: Session setzen (wie heute)
  → ok, 2FA an: KEIN volles user_id in Session;
       stattdessen pending: session["pending_2fa_user_id"] + kurze TTL-Markierung
       Response: { "needs_2fa": true, "methods": ["totp","backup"] }

POST /api/login/2fa {code}   # oder {backup_code}
  → prüft pending_2fa_user_id
  → TOTP (Fenster ±1 Step) oder Backup-Code
  → Erfolg: pending löschen, normale Session setzen
  → Fehler: Rate-Limit (enger als Passwort), kein User-Enumeration-Detail
```

**Form-Login** (`POST /login`): gleichen Zwei-Schritt-Flow in `login.html` (zweites Feld einblenden).

**Middleware:** Pfade `/api/login/2fa` (+ ggf. static) public; Session mit nur `pending_2fa_*` gilt **nicht** als eingeloggt (`load_user_from_session` nur bei echtem `user_id`).

### B.5 Recovery / Admin-Reset

| Szenario | Verhalten |
|----------|-----------|
| User verliert Phone, hat Backup-Codes | Login mit Backup-Code; danach neu enrollen empfohlen |
| Alle Codes weg | Anderer Admin: `POST /api/users/{id}/reset-2fa` → Secret+Codes löschen, `totp_enabled=False` |
| Letzter Admin ohne Codes/Phone | Break-glass: `FORCE_RESET_SEED_PASSWORDS` hilft **nicht** bei 2FA — braucht CLI/Env-Flag z. B. `DISABLE_2FA_USER=<username>` einmalig beim Start **oder** dokumentierter SQLite-Eingriff (nur Ops) |
| Nach Passwort-Reset durch Admin | 2FA bleibt aktiv (sicherer Default); optional Checkbox «2FA auch zurücksetzen» |

### B.6 Session-Implikationen

- Volle Session erst nach 2FA (sonst Pending-Bypass).
- Nach Enrollment: bestehende Sessions anderer Devices bleiben gültig (MVP ok); optional später `session_version` auf User.
- Logout löscht alles inkl. pending.
- Password-Change: 2FA unverändert; optional Re-Auth mit TOTP für sensible Admin-Actions (später).

### B.7 Security-Hinweise

- Secrets nie in Logs / API-Responses nach Confirm.
- Backup-Codes: bcrypt + timing-safe compare; Rate-Limit 2FA-Endpoint.
- Brute-Force TOTP: 6 Digits + Rate-Limit + Account-Lock soft (z. B. nach N Fehlern pending verwerfen).
- QR nur über HTTPS in Production (`HTTPS_ONLY` / `DOMAIN`).
- `MutatingOriginMiddleware`: `/api/login` ist whitelisted — `/api/login/2fa` ebenfalls whitelisten oder Origin weiter erlauben wie Login.

### B.8 Rollout-Phasen

| Phase | Policy | T-Shirt |
|-------|--------|---------|
| **B0** | Schema + Libs + Feature-Flag `TOTP_AVAILABLE=1` | **S** |
| **B-MVP** | Optional enrollment + Login-Challenge + Backup-Codes + Admin-Reset | **M** |
| **B2** | **Enforce für `admin`** (Grace-Period z. B. 7 Tage, Banner) | **S** |
| **B3** | Enforce für alle Rollen | **S** |
| **B4** | WebAuthn / Passkeys | **L** (nicht jetzt) |

Empfohlene Reihenfolge für internes Tool: **B-MVP → B2 (Admin Pflicht) → B3 nach Team-OK**.

---

## C — Abhängigkeiten, Schätzung, Tests, Offene Fragen

### C.1 Reihenfolge

```
A-MVP (Rollen + Soft-Delete)
  → parallel möglich mit B0 (Schema/Libs)
B-MVP (optional 2FA)
  → B2 Admin-Enforce
  → A2 Polish / B3 alle User
```

User-CRUD zuerst ist sinnvoll: Admin-Reset-2FA und User-Deaktivieren brauchen dieselbe Admin-UI. Rein technisch sind A und B entkoppelt.

### C.2 T-Shirt-Schätzung (gesamt)

| Slice | Size | Grobe Annahme |
|-------|------|----------------|
| A-MVP | **S** | ~0.5–1 Tag |
| B-MVP | **M** | ~2–3 Tage (API + Login-UI + Enrollment + Tests) |
| B2 Enforce Admin | **S** | ~0.5 Tag |
| Docs (.env.example, deploy) | **XS** | mit B0/B-MVP |

### C.3 Demo- / Test-Checkliste

**User management**

- [ ] Rolle Case Manager → Compliance; Nav/Landing passt nach Re-Login (Session speichert `role` — **bei Role-Change Session-Role aktualisieren oder User ausloggen**)
- [ ] Letzten Admin degradieren/deaktivieren → abgelehnt
- [ ] Sich selbst deaktivieren → abgelehnt
- [ ] Deaktivierter User: Login 401; offene Session → 401/Redirect
- [ ] Reaktivieren + Login ok
- [ ] Nicht-Admin: `PATCH /api/users/*` → 403

**2FA**

- [ ] Enrollment: QR/URI, falscher Code → kein Enable
- [ ] Korrekter Code → Backup-Codes genau einmal sichtbar
- [ ] Login: Passwort ok → Challenge; ohne Code keine App-APIs
- [ ] TOTP ok → volle Session
- [ ] Backup-Code ok → Code nicht wiederverwendbar
- [ ] Admin Reset 2FA → Login nur noch Passwort
- [ ] Rate-Limit 2FA; Timing/Enumeration grob wie Passwort-Login
- [ ] Replay alter pending_2fa Session nach Logout unmöglich

**Session-Role-Falle (wichtig):** `api_login` schreibt `request.session["role"]`. Nach `PATCH` Rolle muss entweder Session-Feld aktualisiert werden (wenn Opfer = aktueller User) oder Nav weiter aus DB/`/api/me` kommen (heute: `__lynxUser` aus `/api/me` — prüfen, ob irgendwo Session-Role für Auth genutzt wird: `require_role` nutzt **DB-User**, ok; Session-Role evtl. nur kosmetisch — trotzdem bei Login-Refresh konsistent halten).

### C.4 Offene Fragen an Product / Ops

**Erledigt 2026-08-12** — siehe «Beschlossen» oben.

Verbleibend optional (nicht blockierend):

7. Backup-Codes: **10** alphanumerisch (umgesetzt).
8. Role-Change: DB-Role sofort; Session-Role bei Self-Change aktualisiert (kein Force-Logout des Opfers).
9. Persistentes Auth-Audit-Table: weiterhin App-Log-Zeilen; Table später bei Compliance-Bedarf.

---

## Out of Scope (dieser Plan)

- OAuth / SSO / Magic Links
- WebAuthn
- E-Mail-Versand von Invite/Backup-Codes
- Feingranulare Permissions jenseits der drei Rollen
- Hard-Delete / Env-Break-glass für 2FA
- Automatische Planning-Board-Karten (siehe Zeiger in `PLANNING.md`)

---

## Zeiger fürs Team

- Ausführlicher Plan: **diese Datei**
- Kurzverweis: [`PLANNING.md`](../PLANNING.md), [`WISHLIST.md`](../WISHLIST.md)
- Umsetzung später: eigene Commits/PR; Changelog-Eintrag unter Added wenn shipped
