#!/bin/sh
# Publishes the maintenance-page Worker (D-037) on the website's page addresses only, so the frequent /api/ polls,
# files and clips never count against the Workers free plan. Needs `npx wrangler login` once (the owner).
# Usage: sh deploy/maintenance/deploy.sh <hostname>      e.g. attendance.example.com
set -eu
h=${1:?usage: deploy.sh <hostname>}
cd "$(dirname "$0")"
set --
for p in "" "live" "attendance*" "people*" "cameras*" "system*" "login*" "register*" "uploads*" "users*" "account*"; do
  set -- "$@" --route "$h/$p"
done
npx --yes wrangler deploy worker.js --name facepipe-maintenance --compatibility-date 2026-11-01 --no-bundle "$@"
