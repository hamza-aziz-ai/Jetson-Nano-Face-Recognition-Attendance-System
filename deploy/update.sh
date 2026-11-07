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
# The repository is private (D-044): reading the release list and cloning the tag need a read-only token, which the
# owner puts in <app_dir>/gh_token (mode 0600). It travels in the environment, never on a command line (`ps` shows
# those to every user) and never inside a URL (git writes those into the clone's config).
GH_TOKEN=$(cat "$APP/gh_token" 2>/dev/null || true)
export GH_TOKEN
GIT_CRED='!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'

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

LATEST=$(python3 -c "import json, os, sys, urllib.request
req = urllib.request.Request('https://api.github.com/repos/$REPO/releases/latest')
if os.environ.get('GH_TOKEN'):
    req.add_header('Authorization', 'token ' + os.environ['GH_TOKEN'])
print(json.load(urllib.request.urlopen(req, timeout=20))['tag_name'])" 2>/dev/null) || LATEST=""
if [ -z "$LATEST" ]; then
  # log once per outage, not every 10 minutes
  if [ ! -f "$STATE/offline" ]; then
    if [ -z "$GH_TOKEN" ]; then
      log WARNING "cannot read the latest release of $REPO: no read-only token in $APP/gh_token (the repository is private, D-044)"
    else
      log WARNING "cannot read the latest release of $REPO (offline, or the token is invalid, expired or lacks Contents: read)"
    fi
  fi
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
if git -c credential.helper= -c credential.helper="$GIT_CRED" clone -q --depth 1 --branch "$LATEST" "$URL" "$W/src" 2>"$W/clone.err" &&
   (cd "$W" && REPO_URL="$URL" sh "$W/src/deploy/deploy.sh" "$LATEST" "$APP" > "$W/deploy.log" 2>&1); then
  log INFO "deployed $LATEST: $(grep '^healthy' "$W/deploy.log" || true)"
  rm -f "$STATE/failed"
else
  log ERROR "deploy of $LATEST failed (the previous version stays or was restored): $(tail -n 3 "$W/deploy.log" "$W/clone.err" 2>/dev/null | tr '\n' ' ')"
  echo "$LATEST" > "$STATE/failed"
fi
