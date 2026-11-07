# Every deploy left a full copy of the source on the device

Date: 2026-11-07. Status: fixed in v1.13.5. Found by: the owner, asking "Source code is nowhere on the Nano?" after the compiled build and the cleanup of `~/facepipe`.

**Symptom:** none visible. `~/facepipe` held nothing but the two eight-line `web.py` stubs, and `webapp.so` carried no readable Python. `/tmp` held five directories, one per deploy, each with `facepipe.cpp`, `web.py`, `check_vs_rest.py` and the packaged release: the entire source, on the device, on disk.

**Cause:** `deploy/deploy.sh` created its working directory with `mktemp -d` and never removed it. `deploy/update.sh` does clean up after itself, which is why this stayed hidden: the folder it makes goes away, and the one the `deploy.sh` inside it makes does not. `/tmp` here is on the 235 GB disk, not a RAM filesystem, so nothing cleared it between reboots either.

**Fix:** `trap 'rm -rf "$WORK"' EXIT` in `deploy.sh`, so the clone goes on every exit, a rollback included. The release workflow sets `DIST` to a path of its own, so the release assets are unaffected; a deploy run by hand that wants to keep them must set `DIST` too.

**Lesson:** compiling the website (D-043) protected one file while the build left all of them beside it. Protecting source is a property of the whole device, not of the file that was compiled, and the thing that looks temporary is worth checking first: `mktemp -d` without a `trap` is a leak that only shows up in a place nobody lists.
