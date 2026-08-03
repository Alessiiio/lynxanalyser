#!/usr/bin/env bash
# Manual update script for the Lynx VPS. Run on the server as root:
#   cd /root/lynxanalyser && ./deploy/update.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== git pull =="
git pull --ff-only

echo "== docker compose up -d --build =="
docker compose up -d --build --remove-orphans

echo "== cleanup old images =="
docker image prune -f

echo "== done =="
docker compose ps
