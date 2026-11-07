#!/bin/sh
# Install facepipe on a Jetson Nano (JetPack 4.6.x with DeepStream 6.0.1). Run from the folder you want it in:
#   sh deploy/install.sh              build, build engines, create facepipe.env, install and start the services
#   sh deploy/install.sh --no-service everything except the systemd services
# Nothing is tied to a user or path: the services run as whoever runs this, from this folder.
# Safe to run again (it keeps facepipe.env, data, gallery, models and logins).
set -eu
APP=$(cd "$(dirname "$0")/.." && pwd)
USER_NAME=$(id -un)
cd "$APP"
DS=/opt/nvidia/deepstream/deepstream-6.0
TRTEXEC=/usr/src/tensorrt/bin/trtexec

echo "== checks"
[ -d "$DS" ] || { echo "DeepStream 6.0 not found in $DS (install it: sudo apt install deepstream-6.0)"; exit 1; }
[ -x "$TRTEXEC" ] || { echo "trtexec not found at $TRTEXEC (part of JetPack TensorRT)"; exit 1; }
id -nG "$USER_NAME" | grep -qw video || echo "warning: $USER_NAME is not in the video group (GPU and cameras need it): sudo usermod -aG video $USER_NAME"

echo "== build"
make all

echo "== models"
mkdir -p models data
for f in scrfd_10g_384x640.onnx w600k_r50.onnx; do
  [ -f "models/$f" ] || { echo "missing models/$f: see README, Getting started, step 1"; exit 1; }
done
if [ ! -f models/scrfd_384x640_b1.plan ]; then
  echo "building the SCRFD engine (about 7 minutes)"
  "$TRTEXEC" --onnx=models/scrfd_10g_384x640.onnx --fp16 --workspace=1024 --saveEngine=models/scrfd_384x640_b1.plan >/dev/null
fi
if [ ! -f models/r50_b16.plan ]; then
  echo "building the ArcFace engine (about 9 minutes)"
  "$TRTEXEC" --onnx=models/w600k_r50.onnx --fp16 --workspace=1024 --minShapes=input.1:1x3x112x112 \
    --optShapes=input.1:8x3x112x112 --maxShapes=input.1:16x3x112x112 --saveEngine=models/r50_b16.plan >/dev/null
fi

echo "== self-tests"
./facepipe --selftest
# a throwaway data folder: the self-test writes and deletes camera files and test events
T=$(mktemp -d)
FACEPIPE_DATA="$T" python3 web.py --selftest
rm -rf "$T"

if [ ! -f facepipe.env ]; then
  cp facepipe.env.example facepipe.env
  echo "created facepipe.env: set FACEPIPE_URI to your camera"
fi

[ "${1:-}" = "--no-service" ] && { echo "done (no services installed)"; exit 0; }

echo "== services (as $USER_NAME, from $APP)"
for u in facepipe.service facepipe-web.service facepipe-update.service facepipe-update.timer; do
  sed -e "s#@USER@#$USER_NAME#g" -e "s#@APP_DIR@#$APP#g" "deploy/$u.in" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable facepipe.service facepipe-web.service
# automatic updates (D-031): deploy.sh needs `sudo -n systemctl stop|start facepipe facepipe-web` without a password
sudo systemctl enable --now facepipe-update.timer
# the website's Start/Stop/Restart (D-033): exactly these three commands for facepipe, never for the web itself
RULE="$USER_NAME ALL=(root) NOPASSWD: /bin/systemctl start facepipe, /bin/systemctl stop facepipe, /bin/systemctl restart facepipe"
echo "$RULE" > /tmp/facepipe-web.sudoers
if sudo visudo -cf /tmp/facepipe-web.sudoers >/dev/null; then
  sudo install -m 0440 -o root -g root /tmp/facepipe-web.sudoers /etc/sudoers.d/facepipe-web
else
  echo "sudoers rule rejected by visudo; service control on the website will not work"
fi
rm -f /tmp/facepipe-web.sudoers
[ -f web_auth ] || echo "set the dashboard login first: python3 web.py --set-password <user>"
sudo systemctl restart facepipe facepipe-web
echo "done: dashboard on http://<this-device>:$(grep -E '^FACEPIPE_WEB_PORT=' facepipe.env | cut -d= -f2 || echo 8000)"
echo "enroll people: put photos in gallery/<name>/ and run ./facepipe-enroll gallery"
