# Project Memory: Read This First

Snapshot: **2026-11-07 00:10 IST**. Volatile state; verify before acting (see "Start here" in `CLAUDE.md`).

## Versions

- `main`: v1.0.3 (`92eaa15`) plus docs. Released: v1.0.0 to v1.0.3 (GitHub releases with assets and SHA256SUMS).
- The device runs v1.2.0 (website + service control), deployed by its own update timer (D-031) on 2026-11-07 00:08 IST. v1.0.2 was never deployed (rolled back).

## Device

- Jetson Nano 4 GB, JetPack 4.6.6, app on the microSD, camera `csi:0` (Raspberry Pi HQ camera, IMX477, R8 removed). USB webcam on `/dev/video1`; a phone RTSP camera is also available.
- Measured with no face in view: GPU 89-99 %, CPU about 33 % per core, RAM 2.7 of 3.96 GB, 13-15 fps processed (every 2nd frame).
- The installed systemd units predate `deploy/install.sh`; re-run the installer only with the owner's approval.

## In progress

1. **v1.0.3 released and deployed (healthy after 14 s, 14 fps).** Contents: daily log files in the owner's layout (D-030), clean bounded SIGTERM shutdown and watchdog fix (D-029), deploy health on the current `MainPID` with advancing frames, clip finalization result logged, 12-hour times on the web page. Verified on the Nano in a test instance: self-tests, log file format, clean stop in 2.7 s. CSI stall cause found: a killed camera process wedges nvargus-daemon (`SCF: Error Timeout`, then `InvalidState` for every client) until the daemon is restarted (docs/bugs/csi-start-stall.md). Still to do: restart after 20 min and after hours of CSI capture.
2. **Empty/short clips: cause found and fixed in v1.1.2** (IDR only every 256 frames; idrinterval=15). PR fix/clip-preroll.
3. **v1.1.0: adaptive detection (D-028), this commit.** Own SCRFD probe replacing nvinfer; same results as nvinfer on identical frames; empty scene 15 fps, 2.4 detections/s, GPU about 0%. Still to measure with a face present (owner): fps and delivery gaps during recognition.

4. **v1.1.1: automatic deployment (D-031)**, owner chose a pull timer over the GitHub runner: `facepipe-update.timer` every 10 min runs `deploy/update.sh`. Units must be installed on the owner's Nano with approval of the exact files.
5. **Website plan agreed with Codex (D-033):** v1.2 site + service control (built in PR feat/website-v1.2; sudoers rule installed on the owner's Nano with approval), v1.3 registration with safe gallery publication, v1.4 uploads after a capacity test, v1.5 two cameras. First: empty-clip bug and active-face cadence (owner in front of the camera).
6. **PR practice (D-032):** code via branch + PR with auto-merge on the 5 CI checks; docs straight to main with [skip ci]; releases by the workflow when a merged change raises VERSION. PR #1 merged that way.

## Owner decisions 2026-11-07 (D-034)

- Next work, in this order: registration by invitation (v1.3), then the Cameras page (source choice CSI/USB/RTSP, RTSP list with enable/disable, one active), then two RTSP cameras at once. Video upload after those.
- Registration: invitation link only (single use, 48 h), admin approval; rejected photos deleted at once, unreviewed after 7 days; client IP only in the web log.

## Next (owner's request of 2026-11-06, in order)

1. End-to-end tests on RTSP, file, USB and CSI (owner in front of the live cameras).
2. Multi-page website (v1.1.0): Overview, Attendance, Live (MJPEG of the RTSP stream), People/Registration, Settings; start/stop/restart the service from the web (narrow sudoers rule, exact commands need owner approval); self-service registration with admin approval and recorded consent; update or delete people.
3. Video upload (v1.2.0): queued jobs beside the live camera, annotated video saved for download, never counted as attendance.
4. Up to 2 cameras (v1.3.0): enable or disable each from the website.

## Still open

- Self-hosted runner: the owner registers it (token never typed by an agent); restricted user and sudo rule need exact-command approval.
- MLOps model manifest; LFW evaluation (download needs owner permission); license choice (InsightFace models are non-commercial).
