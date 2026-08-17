#!/usr/bin/env bash
# Hard reset on the VPS: Watchlist, Fälle, Posteingang, Scans, SHAB-Archiv, Caches.
# Benutzerkonten bleiben. Goldlist/Blocklist bleiben (außer --also-lists).
#
# Auf dem Server:
#   cd /opt/lynx
#   bash scripts/hard-reset-server.sh
#
# Optional vorher Backup:
#   docker compose exec app sqlite3 /app/data/fraud_checks.db \
#     ".backup '/app/data/fraud_checks.backup.db'"

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker nicht gefunden. Dieses Skript auf dem VPS ausführen."
  exit 1
fi

if [[ ! -f docker-compose.yml ]]; then
  echo "Kein docker-compose.yml in $ROOT — nach /opt/lynx wechseln."
  exit 1
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx app; then
  echo "Container «app» läuft nicht. Zuerst: docker compose up -d"
  exit 1
fi

echo "Datenbank im Container: /app/data/fraud_checks.db"
echo "Wird geleert: Watchlist (Firmen + Personen), Fälle, Posteingang,"
echo "              Bulk-Scan, Suchverlauf, SHAB-Archiv, Caches."
echo "Bleibt:       Benutzer / Login / 2FA"
echo
read -r -p "Zum Bestätigen RESET tippen: " confirm
if [[ "$confirm" != "RESET" ]]; then
  echo "Abgebrochen."
  exit 1
fi

docker compose cp scripts/reset_runtime_data.py app:/app/scripts/reset_runtime_data.py
docker compose exec app python scripts/reset_runtime_data.py "$@"

echo
echo "Fertig. Im Browser Hard-Reload (Cmd+Shift+R)."
