# Project Memory: Read This First

Snapshot: **2026-11-06 21:45 IST**. Volatile state; verify before acting (see "Start here" in `CLAUDE.md`).

## Versions

- `main`: v1.0.3 (`92eaa15`) plus docs. Released: v1.0.0 to v1.0.3 (GitHub releases with assets and SHA256SUMS).
- The device runs v1.0.3 (deployed 2026-11-06 21:42 IST after a manual nvargus-daemon restart). v1.0.2 was never deployed (rolled back).

## Device

- Jetson Nano 4 GB, JetPack 4.6.6, app on the microSD, camera `csi:0` (Raspberry Pi HQ camera, IMX477, R8 removed). USB webcam on `/dev/video1`; a phone RTSP camera is also available.
- Measured with no face in view: GPU 89-99 %, CPU about 33 % per core, RAM 2.7 of 3.96 GB, 13-15 fps processed (every 2nd frame).
- The installed systemd units predate `deploy/install.sh`; re-run the installer only with the owner's approval.

## In progress

1. **v1.0.3 released and deployed (healthy after 14 s, 14 fps).** Contents: daily log files in the owner's layout (D-030), clean bounded SIGTERM shutdown and watchdog fix (D-029), deploy health on the current `MainPID` with advancing frames, clip finalization result logged, 12-hour times on the web page. Verified on the Nano in a test instance: self-tests, log file format, clean stop in 2.7 s. CSI stall cause found: a killed camera process wedges nvargus-daemon (`SCF: Error Timeout`, then `InvalidState` for every client) until the daemon is restarted (docs/bugs/csi-start-stall.md). Still to do: restart after 20 min and after hours of CSI capture.
2. **Empty clips (owner's report):** five 0-byte `*_clip.mp4` files today (four unknown events, one known), one 23 KB for 4.2 s. v1.0.3 logs how each clip ended; reproduce after deploying (owner in front of the camera; a high `FACEPIPE_MATCH` makes every face unknown).
3. **v1.1.0: adaptive detection (D-028), this commit.** Own SCRFD probe replacing nvinfer; same results as nvinfer on identical frames; empty scene 15 fps, 2.4 detections/s, GPU about 0%. Still to measure with a face present (owner): fps and delivery gaps during recognition.

4. **v1.1.1: automatic deployment (D-031)**, owner chose a pull timer over the GitHub runner: `facepipe-update.timer` every 10 min runs `deploy/update.sh`. Units must be installed on the owner's Nano with approval of the exact files.
5. **Council job-4 running** on the website, registration, uploads and two cameras.

## Next (owner's request of 2026-11-06, in order)

1. End-to-end tests on RTSP, file, USB and CSI (owner in front of the live cameras).
2. Multi-page website (v1.1.0): Overview, Attendance, Live (MJPEG of the RTSP stream), People/Registration, Settings; start/stop/restart the service from the web (narrow sudoers rule, exact commands need owner approval); self-service registration with admin approval and recorded consent; update or delete people.
3. Video upload (v1.2.0): queued jobs beside the live camera, annotated video saved for download, never counted as attendance.
4. Up to 2 cameras (v1.3.0): enable or disable each from the website.

## Still open

- Self-hosted runner: the owner registers it (token never typed by an agent); restricted user and sudo rule need exact-command approval.
- MLOps model manifest; LFW evaluation (download needs owner permission); license choice (InsightFace models are non-commercial).
