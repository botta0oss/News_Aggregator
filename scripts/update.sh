#!/bin/sh
# Updates the app on the server: latest code, rebuilt image, restart. Data and backups stay.
set -e
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
