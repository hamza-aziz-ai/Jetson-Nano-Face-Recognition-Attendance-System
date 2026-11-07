#!/bin/sh
# Build, test, package and install a release on the Jetson Nano, with automatic rollback.
# Runs on the Nano (by the self-hosted runner, or by hand). Usage: sh deploy/deploy.sh vX.Y.Z <app_dir>
# The repository URL comes from REPO_URL, else from this checkout's git remote.
#   1. clean build of the tag from the public repository   2. self-tests and version check
#   3. release assets in $DIST (tarball + SHA256SUMS)       4. install over app_dir, keeping config, data and models
#   5. restart services; healthy = both active, and the current facepipe process (systemd MainPID, unchanged) wrote two
#      status reports with the new version and advancing frames; else roll back. Needs the camera online.
set -eu
TAG=$1
APP=$(cd "$2" && pwd)
REPO_URL=${REPO_URL:-$(git -C "$(dirname "$0")" remote get-url origin)}
VER=${TAG#v}
WORK=$(mktemp -d)
# The clone holds the whole source, including facepipe.cpp. Leaving it on a device undoes the point of shipping the
# website compiled (D-043), so it goes on every exit, rollback included (docs/bugs/deploy-left-source-in-tmp.md).
trap 'rm -rf "$WORK"' EXIT
# release assets; the workflow sets DIST to keep them, otherwise they go with the folder above
DIST=${DIST:-$WORK/dist}
# shipped files; facepipe.env, web_auth, rtsp_auth, data/, gallery/, models/ and tracker.yml stay untouched
FILES="facepipe facepipe-enroll web.py webapp.so VERSION facepipe.env.example deploy/update.sh web/layout.html web/public.html web/app.css web/app.js web/logo.svg web/favicon.svg"

echo "== build $TAG"
# the repository is private (D-044): same read-only token as update.sh, kept out of argv and out of the clone's config
GH_TOKEN=$(cat "$APP/gh_token" 2>/dev/null || true)
export GH_TOKEN
git -c credential.helper= -c credential.helper='!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' \
  clone -q --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"
cd "$WORK/src"
[ "$(cat VERSION)" = "$VER" ] || { echo "VERSION $(cat VERSION) does not match $TAG"; exit 1; }
make all >/dev/null
# web.py becomes webapp.so plus a two-line stub (D-043), before the tests, so they run against what ships
sh deploy/compile-web.sh

echo "== test"
[ "$(./facepipe --version)" = "$VER" ] || { echo "binary reports $(./facepipe --version), expected $VER"; exit 1; }
./facepipe --selftest
FACEPIPE_DATA="$WORK/testdata" python3 web.py --selftest

echo "== package"
mkdir -p "$DIST" "$WORK/pkg/facepipe-$VER"
cp $FILES README.md CHANGELOG.md "$WORK/pkg/facepipe-$VER/"
cp -r deploy tools "$WORK/pkg/facepipe-$VER/"
NAME="facepipe-$VER-jetson-nano-jetpack4.6-arm64.tar.gz"
tar -C "$WORK/pkg" -czf "$DIST/$NAME" "facepipe-$VER"
(cd "$DIST" && sha256sum "$NAME" > SHA256SUMS && cat SHA256SUMS)

echo "== install into $APP"
mkdir -p "$APP/.prev/deploy" "$APP/deploy" "$APP/.prev/web" "$APP/web"
for f in $FILES; do if [ -e "$APP/$f" ]; then cp -p "$APP/$f" "$APP/.prev/$f"; fi; done
# copy next to the target, then rename: cp onto a running binary fails with "Text file busy"
install_file() { cp -p "$1" "$2.new" && mv -f "$2.new" "$2"; }
for f in $FILES; do install_file "$f" "$APP/$f"; done
if [ ! -e "$APP/tracker.yml" ]; then cp tracker.yml "$APP/"; fi

# stop, give the Argus camera daemon a moment to release a CSI camera, then start. A recognition service stopped on the
# website stays stopped (data/desired, D-033): only the web is started then.
wanted_stopped() { [ "$(cat "$APP/data/desired" 2>/dev/null)" = "stopped" ]; }
restart_services() {
  sudo -n systemctl stop facepipe facepipe-web && sleep 3 || return 1
  if wanted_stopped; then sudo -n systemctl start facepipe-web; else sudo -n systemctl start facepipe facepipe-web; fi
}

rollback() {
  echo "== ROLLBACK: restoring the previous version"
  for f in $FILES; do if [ -e "$APP/.prev/$f" ]; then install_file "$APP/.prev/$f" "$APP/$f"; fi; done
  restart_services
  exit 1
}

# window: the watchdog (FACEPIPE_WATCHDOG, default 60 s) plus restart and start-up time, so one stalled start can recover
WD=$(sed -n 's/^FACEPIPE_WATCHDOG=\([0-9]*\).*/\1/p' "$APP/facepipe.env" 2>/dev/null | tail -1)
WD=${WD:-60}
if [ "$WD" -gt 0 ]; then WINDOW=$((WD + 60)); else WINDOW=120; fi
field() { sed -n "s/.*\"$1\":\([0-9]*\).*/\1/p" "$APP/data/status.json" 2>/dev/null; }

echo "== restart and health check (up to ${WINDOW}s)"
restart_services || rollback
i=0; pid=""; seen=0; last_u=""; last_f=""
while [ $i -lt $WINDOW ]; do
  sleep 2; i=$((i + 2))
  if wanted_stopped; then  # healthy = the web runs the new version; recognition stays off as wanted
    systemctl is-active -q facepipe-web && ! systemctl is-active -q facepipe && { echo "healthy: web $VER, recognition stopped as wanted"; echo "$DIST"; exit 0; }
    continue
  fi
  systemctl is-active -q facepipe && systemctl is-active -q facepipe-web || continue
  p=$(systemctl show -p MainPID --value facepipe)
  # a new process (first start or a restart by the watchdog): only its own reports count
  if [ "$p" != "$pid" ]; then pid=$p; seen=$(date +%s); last_u=""; continue; fi
  grep -q "\"version\":\"$VER\"" "$APP/data/status.json" 2>/dev/null || continue
  u=$(field updated); f=$(field frames)
  [ -n "$u" ] && [ -n "$f" ] && [ "$u" -ge "$seen" ] || continue
  if [ -n "$last_u" ] && [ "$u" -gt "$last_u" ] && [ "$f" -gt "$last_f" ]; then
    echo "healthy: facepipe $VER (pid $pid) at $f frames after ${i}s"; echo "$DIST"; exit 0
  fi
  last_u=$u; last_f=$f
done
rollback
