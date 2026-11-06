#!/bin/sh
# Render every Mermaid block in tracked Markdown files with mermaid-cli; fail on any parse or render error.
# Usage: sh ci/mermaid-check.sh   (needs Node.js; CI runs it on ubuntu-latest)
set -eu
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
echo '{"args":["--no-sandbox"]}' > "$OUT/puppeteer.json"
n=0
for md in $(git ls-files '*.md'); do
  awk -v out="$OUT" -v base="$(echo "$md" | tr '/.' '__')" '
    /^```mermaid[[:space:]]*$/ { inb=1; k++; f=sprintf("%s/%s_%d.mmd", out, base, k); next }
    /^```[[:space:]]*$/ && inb { inb=0; close(f); next }
    inb { print > f }' "$md"
done
for f in "$OUT"/*.mmd; do
  [ -e "$f" ] || continue
  npx -y @mermaid-js/mermaid-cli@12.0.0 -q -p "$OUT/puppeteer.json" -i "$f" -o "$f.svg" || { echo "FAILED: $(basename "$f")"; exit 1; }
  n=$((n + 1))
done
echo "mermaid ok: $n diagrams rendered"
