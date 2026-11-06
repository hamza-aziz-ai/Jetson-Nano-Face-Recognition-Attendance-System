# Project Memory: Read This First

Snapshot: **2026-11-06 21:20 IST**. Volatile state; verify before acting (see "Start here" in `CLAUDE.md`).

## Versions

- `main` HEAD: release commit of v1.0.2 (`c4d0672`) plus agent-file commits. Released: v1.0.0, v1.0.1, v1.0.2 (GitHub releases with assets and SHA256SUMS).
- **The device still runs v1.0.1.** The v1.0.2 deploy rolled back automatically: the new process stalled opening the CSI camera, and `deploy.sh` waited only 60 s, less than the watchdog (60 s) plus systemd restart (10 s). See `docs/bugs/csi-start-stall.md`.

## Device

- Jetson Nano 4 GB, JetPack 4.6.6, app on the microSD, camera `csi:0` (Raspberry Pi HQ camera, IMX477, R8 removed). USB webcam on `/dev/video1`; a phone RTSP camera is also available.
- Measured with no face in view: GPU 89-99 %, CPU about 33 % per core, RAM 2.7 of 3.96 GB, 13-15 fps processed (every 2nd frame).
- The installed systemd units predate `deploy/install.sh`; re-run the installer only with the owner's approval.

## In progress

1. **Agreed with Codex in a council (5 exchanges), 2026-11-06, to implement as v1.0.3:**
   - Idle GPU: replace nvinfer with an own SCRFD probe before nvtracker (reuse `Engine`, `decode_scrfd`, the landmark gate); VIC letterbox + CPU normalize; video buffers never skipped, only inference. Start active; after 10 s without raw detections detect every 6th processed frame (2.5/s); first face restores full rate. Accept only with measured smooth 15 fps on every output; otherwise revisit the split pipeline.
   - CSI stall (cause unconfirmed): SIGTERM/SIGINT through GLib into one cleanup path (stopping flag, no new jobs/clips/evidence waits, capture to NULL, settle recognition, finalize active and finalizing clips) under a 10 s deadline enforced by the watchdog thread (non-zero exit on timeout, active even with `FACEPIPE_WATCHDOG=0`); fix the watchdog start (exits at ~65 s, not 60); `TimeoutStopSec=15s` in the unit template only (device after owner approval).
   - `deploy.sh` health: stable `MainPID`, two advancing status reports after that PID appeared, expected version, both services active; window `FACEPIPE_WATCHDOG + 60` s, 120 s when disabled.
   - Docs: D-028, D-029, D-004 correction, clip pre-roll is relative to the recognition decision, csi-start-stall "mitigated; cause unconfirmed".
2. **Bug reported by the owner: empty clips.** Several `*_clip.mp4` files are 0 bytes (four unknown events and one known), one is 23 KB for 4.2 s; others are fine. No error is logged because the clip finalizer does not log the bus result. Investigate with that logging first.
3. Release v1.0.3 and redeploy; repeat the long-run CSI restart test.

## Next (owner's request of 2026-11-06, in order)

1. End-to-end tests on RTSP, file, USB and CSI (owner in front of the live cameras).
2. Multi-page website (v1.1.0): Overview, Attendance, Live (MJPEG of the RTSP stream), People/Registration, Settings; start/stop/restart the service from the web (narrow sudoers rule, exact commands need owner approval); self-service registration with admin approval and recorded consent; update or delete people.
3. Video upload (v1.2.0): queued jobs beside the live camera, annotated video saved for download, never counted as attendance.
4. Up to 2 cameras (v1.3.0): enable or disable each from the website.

## Still open

- Self-hosted runner: the owner registers it (token never typed by an agent); restricted user and sudo rule need exact-command approval.
- MLOps model manifest; LFW evaluation (download needs owner permission); license choice (InsightFace models are non-commercial).
