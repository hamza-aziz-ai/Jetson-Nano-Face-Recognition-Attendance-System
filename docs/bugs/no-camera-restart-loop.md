# A board with no camera restarted for ever, and no release could be installed on it

Date: 2026-11-16. Status: fixed in v1.14.1. Found by: v1.14.0 failing to deploy on the Nano while the USB camera was
elsewhere, and rolling itself back.

**Symptom:** `systemctl is-active facepipe` answered `activating` for ever, the restart counter reached 337 in an hour
(about one restart every 11 s), `data/status.json` was six days old, the website said recognition was not running, and
the automatic update logged `[ERROR] deploy of v1.14.0 failed (the previous version stays or was restored)` with
`== restart and health check (up to 120s) == ROLLBACK`. The device stayed on v1.13.7.

**Cause:** two things, one of them easy to miss. `facepipe` reached the camera's GStreamer error
(`v4l2src0: Cannot identify device '/dev/v4l/by-id/usb-...-video-index0'`) about six seconds into every start, ended
the main loop and exited 0, so systemd restarted it ten seconds later, for ever. That part was only noisy. The part
that blocked everything was `deploy/deploy.sh`: it calls a deploy healthy when the running process writes two status
reports with **advancing frames**, and its own header said "Needs the camera online". With no camera there are no
frames, ever, so the health check could not pass, the rollback was automatic, and **a board whose camera was elsewhere
could not be updated at all** - including by the fix for the restart loop itself, if it had come as an ordinary
release.

**Fix (D-048):** `facepipe` checks a camera wired to the board before it builds the pipeline - the `usb:` device path
with `access()`, a `csi:` sensor against the board's `vi-output` capture nodes - and when it is not there it waits,
rechecking every 5 s and writing `status.json` with `"waiting":"the USB camera is not connected"`, before the watchdog
starts (the watchdog exits when frames stop advancing, which is exactly what waiting looks like). `deploy.sh` accepts
that report as healthy. The website already had a state for a running service with no picture; it now names the
reason. The same release's `deploy.sh` runs the deploy (`update.sh` calls the clone's copy), so this fix installs
itself on a board with no camera.

**Lesson:** the loop looked like log noise and was triaged as noise. What it actually did was make the device
un-updatable, and the health check that enforced it said so in a comment nobody had reason to read. A rule that an
automated deploy applies - "healthy means frames" - is a deployment constraint, not a detail: when the hardware a
check depends on can legitimately be absent, the check needs a third answer besides healthy and broken. The Orin
system had the same bug in the same week (its D-032), found the same way: by an install rolling back.
