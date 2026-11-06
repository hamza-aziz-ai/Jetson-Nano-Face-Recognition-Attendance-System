#!/bin/sh
# Inside python:3.6-slim (the Nano's Python): web self-test and syntax checks.
set -eu
python3 --version
python3 web.py --selftest
python3 -m py_compile check_vs_rest.py
for f in mlops/*.py; do [ -e "$f" ] && python3 -m py_compile "$f"; done
[ -e mlops/check_manifest.py ] && python3 mlops/check_manifest.py
echo "python checks ok"
