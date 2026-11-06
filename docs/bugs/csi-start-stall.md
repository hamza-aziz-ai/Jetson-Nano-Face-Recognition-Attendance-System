# CSI camera stalled the pipeline at start

Date: 2026-11-06. Status: cause found 2026-11-06; fixed by the clean shutdown in v1.0.3 (long-run restarts still to repeat). Found by: the first v1.0.1 deploy, which rolled back automatically.

**Symptom:** The new process logged its version and camera line, then nothing for 60 s: no recordings or RTSP line, no status report. `deploy.sh` saw no healthy status and rolled back. A retry a minute later started in under a second, and plain restarts did not reproduce it.

**Cause (most likely):** The new process created `nvarguscamerasrc` 6 ms after the old one released the CSI camera; the Argus daemon intermittently blocks a client that reconnects that fast. The stall was before the GLib main loop ran, so the existing status timer never fired, and systemd kept seeing an "active" service.

**Fix:** A watchdog thread, started first thing in `main`, exits the process (code 3) when the frame counter has not moved for `FACEPIPE_WATCHDOG` seconds (default 60; 0 turns it off); systemd restarts it 10 s later. `deploy.sh` now stops the services, waits 3 s, then starts them.

**Trade-off:** A camera that is connected but sends nothing causes a restart every 70 s, which also retries the camera.

**Update (v1.0.3):** The v1.0.2 deploy stalled again, this time after a 3 s pause, so "reopened too fast" does not explain it. Both stalls followed a process that had run the CSI camera for a long time and was killed by SIGTERM mid-stream (no signal handler), so the Argus session was never closed. v1.0.3 handles SIGTERM with a clean, bounded shutdown (D-029). The rollback also showed that a 60 s health window cannot wait for a 60 s watchdog: `deploy.sh` now waits the watchdog time plus 60 s and accepts only reports from the current process. The cause stays unconfirmed until repeated long-run restarts pass; on a new stall, take a backtrace (`gdb -p <pid> -batch -ex "thread apply all bt"`) before the watchdog fires.

**Cause found (deploying v1.0.3, 2026-11-06 21:39 IST):** stopping the old v1.0.1 process (killed by SIGTERM mid-capture) left the camera daemon wedged. `journalctl -u nvargus-daemon` showed `SCF: Error Timeout` from `abortCaptures()`, `shutdown()` and `deleteSession()`, and `Cleaning up 1 outstanding streams`. From then on every new client got `(Argus) Error InvalidState` and an immediate end of stream (or no frames at all), including the rolled-back v1.0.1, until `nvargus-daemon` was restarted. A pause or a facepipe restart cannot help; only a clean release of the session (v1.0.3) prevents it, and restarting the daemon recovers it.
**Recovery (manual, documented in ROLLBACK):** `sudo systemctl stop facepipe && sudo systemctl restart nvargus-daemon && sudo systemctl start facepipe`. Then the v1.0.3 deploy was healthy after 14 s, and a clean `systemctl restart facepipe` stopped in 0.6 s and started again at 14 fps with no daemon errors. The `ModuleNotPresent` lines the daemon prints at every start are its normal probe of empty camera slots.
