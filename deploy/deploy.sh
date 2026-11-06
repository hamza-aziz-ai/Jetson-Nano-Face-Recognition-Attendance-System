#!/bin/sh
# Build, test, package and install a release on the Jetson Nano, with automatic rollback.
# Runs on the Nano (by the self-hosted runner, or by hand). Usage: sh deploy/deploy.sh vX.Y.Z [app_dir]
#   1. clean build of the tag from the public repository   2. self-tests and version check
#   3. release assets in $DIST (tarball + SHA256SUMS)       4. install over app_dir, keeping config, data and models
#   5. restart services; healthy = both active and status.json reports the new version within 60 s, else roll back
set -eu
TAG=$1
APP=${2:-/home/hamza/facepipe}
REPO_URL=${REPO_URL:-https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System.git}
VER=${TAG#v}
WORK=$(mktemp -d)
DIST=${DIST:-$WORK/dist}
# shipped files; facepipe.env, web_auth, rtsp_auth, data/, gallery/, models/ and tracker.yml stay untouched
FILES="facepipe facepipe-enroll web.py VERSION pgie_scrfd.txt facepipe.service facepipe-web.service facepipe.env.example check_vs_rest.py"

echo "== build $TAG"
git clone -q --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"
cd "$WORK/src"
[ "$(cat VERSION)" = "$VER" ] || { echo "VERSION $(cat VERSION) does not match $TAG"; exit 1; }
make all >/dev/null

echo "== test"
[ "$(./facepipe --version)" = "$VER" ] || { echo "binary reports $(./facepipe --version), expected $VER"; exit 1; }
./facepipe --selftest
FACEPIPE_DATA="$WORK/testdata" python3 web.py --selftest

echo "== package"
mkdir -p "$DIST" "$WORK/pkg/facepipe-$VER"
cp $FILES README.md CHANGELOG.md "$WORK/pkg/facepipe-$VER/"
NAME="facepipe-$VER-jetson-nano-jetpack4.6-arm64.tar.gz"
tar -C "$WORK/pkg" -czf "$DIST/$NAME" "facepipe-$VER"
(cd "$DIST" && sha256sum "$NAME" > SHA256SUMS && cat SHA256SUMS)

echo "== install into $APP"
mkdir -p "$APP/.prev"
for f in $FILES; do if [ -e "$APP/$f" ]; then cp -p "$APP/$f" "$APP/.prev/$f"; fi; done
for f in $FILES; do cp -p "$f" "$APP/$f"; done
if [ ! -e "$APP/tracker.yml" ]; then cp tracker.yml "$APP/"; fi

rollback() {
  echo "== ROLLBACK: restoring the previous version"
  for f in $FILES; do if [ -e "$APP/.prev/$f" ]; then cp -p "$APP/.prev/$f" "$APP/$f"; fi; done
  sudo -n systemctl restart facepipe facepipe-web
  exit 1
}

echo "== restart and health check"
sudo -n systemctl restart facepipe facepipe-web || rollback
i=0
while [ $i -lt 60 ]; do
  sleep 2; i=$((i + 2))
  systemctl is-active -q facepipe && systemctl is-active -q facepipe-web || continue
  # the camera may be offline, so health is "running the new version", not "frames flowing"
  grep -q "\"version\":\"$VER\"" "$APP/data/status.json" 2>/dev/null && { echo "healthy: facepipe $VER after ${i}s"; echo "$DIST"; exit 0; }
done
rollback
