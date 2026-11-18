#!/bin/sh
# Publishes the public demo of the website (D-050) at <hostname>/demo/. Static assets only, attached by a Worker
# route: the route intercepts /demo* before the request reaches the tunnel, so the demo answers while the Nano is
# offline, and no DNS record is needed. Never attach it as a Worker Custom Domain - that would make the Worker the
# whole hostname instead of the website.
# Needs `npx wrangler login` once (the owner). Usage: sh deploy/demo/deploy.sh <hostname>
set -eu
h=${1:?usage: deploy.sh <hostname>}
cd "$(dirname "$0")"
npx --yes wrangler deploy --name facepipe-demo --assets ./site --compatibility-date 2026-11-01 --route "$h/demo*"
