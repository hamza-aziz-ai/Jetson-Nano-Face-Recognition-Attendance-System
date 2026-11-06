# Project Memory: Read This First

Snapshot: **2026-11-06 21:45 IST**. Volatile state; verify before acting (see "Start here" in `CLAUDE.md`).

## Versions

- `main` HEAD: release commit of v1.0.2 (`c4d0672`) plus agent-file commits. Released: v1.0.0, v1.0.1, v1.0.2 (GitHub releases with assets and SHA256SUMS).
- **The device still runs v1.0.1.** The v1.0.2 deploy rolled back automatically: the new process stalled opening the CSI camera, and `deploy.sh` waited only 60 s, less than the watchdog (60 s) plus systemd restart (10 s). See `docs/bugs/csi-start-stall.md`.

## Device

- Jetson Nano 4 GB, JetPack 4.6.6, app on the microSD, camera `csi:0` (Raspberry Pi HQ camera, IMX477, R8 removed). USB webcam on `/dev/video1`; a phone RTSP camera is also available.
- Measured with no face in view: GPU 89-99 %, CPU about 33 % per core, RAM 2.7 of 3.96 GB, 13-15 fps processed (every 2nd frame).
- The installed systemd units predate `deploy/install.sh`; re-run the installer only with the owner's approval.

## In progress

1. **v1.0.3 (this commit):** daily log files in the owner's layout (D-030), clean bounded SIGTERM shutdown and watchdog fix (D-029), deploy health on the current `MainPID` with advancing frames, clip finalization result logged, 12-hour times on the web page. Verified on the Nano in a test instance: self-tests, log file format, clean stop in 2.7 s. Next: CI, tag, deploy with `deploy.sh`, then long-run CSI restart tests.
2. **Empty clips (owner's report):** five 0-byte `*_clip.mp4` files today (four unknown events, one known), one 23 KB for 4.2 s. v1.0.3 logs how each clip ended; reproduce after deploying (owner in front of the camera; a high `FACEPIPE_MATCH` makes every face unknown).
3. **v1.1.0: adaptive detection (D-028)**, agreed with Codex: own SCRFD probe replacing nvinfer; idle after 10 s without faces, every 6th frame; accept only with measured smooth 15 fps outputs.

## Next (owner's request of 2026-11-06, in order)

1. End-to-end tests on RTSP, file, USB and CSI (owner in front of the live cameras).
2. Multi-page website (v1.1.0): Overview, Attendance, Live (MJPEG of the RTSP stream), People/Registration, Settings; start/stop/restart the service from the web (narrow sudoers rule, exact commands need owner approval); self-service registration with admin approval and recorded consent; update or delete people.
3. Video upload (v1.2.0): queued jobs beside the live camera, annotated video saved for download, never counted as attendance.
4. Up to 2 cameras (v1.3.0): enable or disable each from the website.

## Still open

- Self-hosted runner: the owner registers it (token never typed by an agent); restricted user and sudo rule need exact-command approval.
- MLOps model manifest; LFW evaluation (download needs owner permission); license choice (InsightFace models are non-commercial).
