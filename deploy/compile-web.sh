#!/bin/sh
# Compiles web.py into webapp.so (native code) and replaces web.py with a two-line stub that starts it, so a release
# carries no readable Python (D-043). deploy.sh runs this after `make all` and before the self-tests, so the tests
# exercise exactly what ships. The build machine needs Nuitka: pip3 install --user nuitka==4.3.4
#
# What this does and does not hide: the program's logic becomes machine code and cannot be read back as Python. Text
# inside the program stays readable in the file (page wording, HTML, log messages), as it would in any compiled
# program, and web/app.js and web/app.css are sent to browsers and cannot be hidden at all.
#
# It overwrites web.py in the folder it runs in. deploy.sh runs it in a throwaway clone of the tag.
set -eu
cd "$(dirname "$0")/.."

[ -f web.py ] || { echo "no web.py in $(pwd)"; exit 1; }
grep -q '^def main():' web.py || { echo "web.py has no main(): the stub would have nothing to call"; exit 1; }
if git rev-parse --git-dir >/dev/null 2>&1 && ! git diff --quiet -- web.py; then
  echo "web.py has uncommitted changes and this script overwrites it: commit or stash first"; exit 1
fi

rm -f webapp.so webapp.cpython-*.so
cp web.py webapp.py
# --nofollow-imports: web.py uses only the standard library, which stays on the device and must not be pulled in.
# --jobs=1 and --lto=no keep the C compiler inside the Nano's memory.
python3 -m nuitka --mode=module --nofollow-imports --jobs=1 --lto=no --no-pyi-file --remove-output webapp.py
rm -f webapp.py

# one stable name in the shipped file list: Python imports a plain .so as readily as Nuitka's
# webapp.cpython-36m-aarch64-linux-gnu.so
if [ ! -f webapp.so ]; then
  so=$(ls webapp.cpython-*.so 2>/dev/null | head -1)
  [ -n "$so" ] || { echo "Nuitka produced no module"; exit 1; }
  mv -f "$so" webapp.so
fi

cat > web.py <<'STUB'
#!/usr/bin/env python3
"""Face Attendance website. The program is compiled into webapp.so (D-043); this file only starts it.

Same commands as before:  python3 web.py [--selftest | --set-password <user> | --set-rtsp-password <user>]
"""
import webapp

webapp.main()
STUB

echo "compiled: webapp.so ($(du -h webapp.so | cut -f1)), web.py is now a stub"
