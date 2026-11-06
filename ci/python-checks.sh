#!/bin/sh
# Inside python:3.6-slim (the Nano's Python): web self-test and syntax checks.
set -eu
python3 --version
python3 web.py --selftest
python3 -m py_compile check_vs_rest.py
for f in mlops/*.py deploy/*.py; do if [ -e "$f" ]; then python3 -m py_compile "$f"; fi; done
if [ -e mlops/check_manifest.py ]; then python3 mlops/check_manifest.py; fi
echo "python checks ok"
