# Lynx auf Hetzner Cloud (Ubuntu)

Ziel: App unter `https://deine-domain.ch` mit Docker Compose + Caddy (Let’s Encrypt).

## 1. Hetzner Console — Server & Zugang

1. [Hetzner Cloud Console](https://console.hetzner.cloud) → **Servers** → Server öffnen (oder neu anlegen: Ubuntu 22.04/24.04, Cx22 reicht zum Start).
2. **IP notieren** (IPv4).
3. **SSH-Zugang:**
   - Unter **Security → SSH Keys** deinen öffentlichen Key hinterlegen und dem Server zuweisen, **oder**
   - Beim ersten Start das per E-Mail/Console vergebene Root-Passwort nutzen.
4. **Firewall** (empfohlen): Rules erlauben
   - TCP `22` (dein IP oder `` für Setup)
   - TCP `80`
   - TCP `443`
5. Test vom Mac:

```bash
ssh root@DEINE_VPS_IP
```

Wenn das geht, weiter. Sonst zuerst SSH debuggen (Key, Firewall, Console → Reset Root Password).

## 2. Domain → VPS

Beim Domain-Registrar (oder Hetzner DNS):

| Typ | Name | Wert |
|-----|------|------|
| A | `@` (oder Hostname, z. B. `lynx`) | `DEINE_VPS_IP` |
| AAAA | optional | IPv6 der Servers, falls genutzt |

Warten bis DNS zeigt (oft wenige Minuten):

```bash
dig +short deine-domain.ch A
```

Muss die VPS-IP zurückgeben, sonst kein gültiges TLS-Zertifikat.

## 3. Docker auf dem VPS

Als `root` auf dem Server:

```bash
apt-get update
apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sh
docker compose version
```

## 4. Projekt hochladen

**Auf dem Mac** (im Projektordner), ohne Secrets/DB/Cache:

```bash
cd ~/Desktop/website-legitimacy-checker-main

rsync -avz --progress \
  --exclude 'venv' \
  --exclude '.env' \
  --exclude '.git' \
  --exclude 'fraud_checks.db' \
  --exclude 'fraud_checks.db-*' \
  --exclude 'data/shab_month_cache' \
  --exclude 'case_reports' \
  --exclude 'compliance_reports' \
  --exclude '__pycache__' \
  --exclude '.DS_Store' \
  ./ root@DEINE_VPS_IP:/opt/lynx/
```

Oder per Git: Repo auf den Server klonen (dann trotzdem **kein** `.env` committen).

## 5. `.env` auf dem Server

```bash
ssh root@DEINE_VPS_IP
cd /opt/lynx
cp .env.example .env
nano .env
```

Mindestens setzen:

```env
ENVIRONMENT=production
DOMAIN=deine-domain.ch
HTTPS_ONLY=1
SESSION_SECRET=HIER_LANGEN_ZUFALLSWERT
SEED_ADMIN_PASSWORD=mindestens12zeichen
SEED_CASE_MANAGER_PASSWORD=mindestens12zeichen
SEED_COMPLIANCE_PASSWORD=mindestens12zeichen
SEED_ALESSIO_PASSWORD=mindestens12zeichen
FORCE_RESET_SEED_PASSWORDS=1
```

Zufalls-Secret erzeugen:

```bash
openssl rand -hex 32
```

API-Keys (Zefix, Safe Browsing, …) nach Bedarf ergänzen. Speichern, nano beenden (`Ctrl+O`, Enter, `Ctrl+X`).

## 6. Starten

```bash
cd /opt/lynx
export DOMAIN=deine-domain.ch
docker compose up -d --build
docker compose ps
docker compose logs -f caddy
```

Wenn Caddy Zertifikat geholt hat: **https://deine-domain.ch/login**

Danach in `.env` setzen: `FORCE_RESET_SEED_PASSWORDS=0` und App neu starten:

```bash
docker compose up -d
```

## 7. Falls etwas hakt

| Symptom | Check |
|---------|--------|
| Kein HTTPS / Caddy-Fehler | DNS zeigt noch nicht auf VPS; Ports 80/443 zu; Domain in `.env` falsch |
| App startet nicht | `docker compose logs app` — oft fehlendes `SESSION_SECRET` / Seed-Passwort |
| 502 | App noch am Bauen; `docker compose ps` |
| Login geht, Cookie fehlt | `HTTPS_ONLY=1` und wirklich über HTTPS öffnen |

## Kurz-Checkliste

- [ ] SSH `root@IP` funktioniert  
- [ ] Firewall 22 / 80 / 443  
- [ ] DNS A → VPS-IP (`dig` ok)  
- [ ] Docker installiert  
- [ ] Code unter `/opt/lynx`  
- [ ] `.env` mit DOMAIN + Secrets  
- [ ] `docker compose up -d --build`  
- [ ] Login unter HTTPS  
