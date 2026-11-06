# Deploy stopped with "Text file busy"

Date: 2026-11-06. Status: fixed in v1.0.1. Found by: the first real run of `deploy/deploy.sh` (deploying v1.0.0 to the Nano).

**Symptom:** Build, both self-tests and packaging passed (asset SHA-256 501c7cd1...), then `cp: cannot create regular file '<app_dir>/facepipe': Text file busy`. The script stopped; the services kept running the previous build untouched.

**Cause:** Linux refuses to open a running executable for writing, so `cp` cannot overwrite the binary the service is executing.

**Fix:** `install_file` copies to `<target>.new` and renames it over the target. A rename replaces the directory entry; the running process keeps its old file until the restart. Rollback uses the same function.

**Proof:** deploying v1.0.1 with the fixed script (see TEST-CHECKLIST).
