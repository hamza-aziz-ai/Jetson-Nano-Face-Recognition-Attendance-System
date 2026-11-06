#!/bin/sh
# Automatic deployment, pull-based (D-031): run every 10 minutes by facepipe-update.timer. Usage: sh deploy/update.sh <app_dir>
# If the repository's latest GitHub release is newer than <app_dir>/VERSION, deploy it with that release's deploy.sh
# (clean build, self-tests, health check, automatic rollback). Needs no token and no inbound access: it only reads the
# public release list. A release whose deploy failed is not retried; a newer release is. Never downgrades.
# Log: <app_dir>/logs/<year>/<Month>/update.log (same layout and line format as facepipe.log, D-030).
set -eu
APP=$(cd "$1" && pwd)
REPO=${FACEPIPE_REPO:-hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System}
STATE="$APP/data/update"
mkdir -p "$STATE"

log() {  # log LEVEL message: rotate yesterday's file to update.log.DD-Mon-YYYY first, like facepipe.cpp
  dir="$APP/logs/$(date +%Y)/$(LC_ALL=C date +%B)"
  mkdir -p "$dir"
  f="$dir/update.log"
  if [ -f "$f" ] && [ "$(date -r "$f" +%F)" != "$(date +%F)" ]; then
    b="$f.$(LC_ALL=C date -r "$f" +%d-%b-%Y)"
    cat "$f" >> "$b" && rm -f "$f"
  fi
  echo "$(LC_ALL=C date '+[%d-%m-%Y] %I:%M:%S.%3N %p, %A') [$1] $2" >> "$f"
  echo "[$1] $2"
}

exec 9>"$STATE/lock"
flock -n 9 || exit 0  # a deploy is still running

LATEST=$(python3 -c "import json, sys, urllib.request
r = urllib.request.urlopen('https://api.github.com/repos/$REPO/releases/latest', timeout=20)
print(json.load(r)['tag_name'])" 2>/dev/null) || LATEST=""
if [ -z "$LATEST" ]; then
  # log once per outage, not every 10 minutes
  [ -f "$STATE/offline" ] || log WARNING "cannot read the latest release of $REPO (offline?)"
  touch "$STATE/offline"
  exit 0
fi
rm -f "$STATE/offline"

CUR="v$(cat "$APP/VERSION")"
[ "$LATEST" = "$CUR" ] && exit 0
[ "$(printf '%s\n%s\n' "$CUR" "$LATEST" | sort -V | tail -1)" = "$LATEST" ] || exit 0  # never downgrade
[ "$(cat "$STATE/failed" 2>/dev/null || true)" = "$LATEST" ] && exit 0                 # failed before: wait for a newer one

log INFO "deploying $LATEST (running $CUR)"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
URL="https://github.com/$REPO.git"
if git clone -q --depth 1 --branch "$LATEST" "$URL" "$W/src" 2>"$W/clone.err" &&
   (cd "$W" && REPO_URL="$URL" sh "$W/src/deploy/deploy.sh" "$LATEST" "$APP" > "$W/deploy.log" 2>&1); then
  log INFO "deployed $LATEST: $(grep '^healthy' "$W/deploy.log" || true)"
  rm -f "$STATE/failed"
else
  log ERROR "deploy of $LATEST failed (the previous version stays or was restored): $(tail -n 3 "$W/deploy.log" "$W/clone.err" 2>/dev/null | tr '\n' ' ')"
  echo "$LATEST" > "$STATE/failed"
fi
