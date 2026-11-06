# CSI camera stalled the pipeline at start

Date: 2026-11-06. Status: fixed by a watchdog (v1.0.2). Found by: the first v1.0.1 deploy, which rolled back automatically.

**Symptom:** The new process logged its version and camera line, then nothing for 60 s: no recordings or RTSP line, no status report. `deploy.sh` saw no healthy status and rolled back. A retry a minute later started in under a second, and plain restarts did not reproduce it.

**Cause (most likely):** The new process created `nvarguscamerasrc` 6 ms after the old one released the CSI camera; the Argus daemon intermittently blocks a client that reconnects that fast. The stall was before the GLib main loop ran, so the existing status timer never fired, and systemd kept seeing an "active" service.

**Fix:** A watchdog thread, started first thing in `main`, exits the process (code 3) when the frame counter has not moved for `FACEPIPE_WATCHDOG` seconds (default 60; 0 turns it off); systemd restarts it 10 s later. `deploy.sh` now stops the services, waits 3 s, then starts them.

**Trade-off:** A camera that is connected but sends nothing causes a restart every 70 s, which also retries the camera.
