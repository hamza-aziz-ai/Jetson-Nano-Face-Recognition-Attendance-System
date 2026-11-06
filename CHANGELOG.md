# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html): MAJOR for breaking changes to configuration, data files or deployment, MINOR for new features, PATCH for fixes. See docs/RELEASING.md.

## [Unreleased]

## [1.0.3] - 2026-11-06

### Added

- Log files (owner's layout): `logs/<year>/<Month>/facepipe.log`, `web.log` and `enroll.log`, one file per day; at midnight the day's file becomes `<module>.log.DD-Mon-YYYY` in its month folder. Line format `[DD-MM-YYYY] hh:mm:ss.fff AM/PM, Weekday [LEVEL] message`. `FACEPIPE_LOGS` sets the folder.
- Each clip now logs how it ended: saved with its size, an error with its cause, or not finalized in time.
- Failed web logins are logged with the client address.

### Changed

- The web page shows times in 12-hour format (09:41 PM); so does the CSV report.

### Fixed

- Stopping the service (SIGTERM) now shuts down cleanly: no new clips, the camera is released (the Argus session is closed, not killed), queued faces are still recorded, the open clip is finalized; a shutdown longer than 10 s is forced (exit 4). Before, the process was killed mid-stream, the most likely cause of the CSI camera stalling on the next start (cause unconfirmed).
- The frame watchdog fired after about 65 s instead of 60 s.
- `deploy.sh` could accept a stale status file or a process that produces no frames: health now requires two advancing reports from the current process (systemd `MainPID`), within the watchdog time plus 60 s.

## [1.0.2] - 2026-11-06

### Fixed

- A CSI camera could stall the pipeline at start (seen when Argus reopened the camera right after the previous process released it); the process stayed "active" with no frames. A frame watchdog now exits after `FACEPIPE_WATCHDOG` seconds without frames (default 60) so systemd restarts it, and `deploy.sh` stops, waits 3 s and starts instead of restarting at once.

## [1.0.1] - 2026-11-06

### Fixed

- `deploy/deploy.sh` stopped with `cp: cannot create regular file ... facepipe: Text file busy` when installing over the running binary; files are now copied beside the target and renamed into place (install and rollback).

## [1.0.0] - 2026-11-06

Every item in the README validation table is now verified on a Jetson Nano: RTSP, USB and CSI cameras, detection, tracking, recognition, enrollment, attendance log, dashboard, event clips with evidence images, and the RTSP stream in VLC.

### Fixed

- CSI cameras delivered no frames to DeepStream on JetPack 4.6 (`nvarguscamerasrc bufapi-version=1`). Verified with the Raspberry Pi HQ Camera (IMX477, resistor R8 removed).
- Evidence images could show the old "Recognizing..." label; the evidence is now the first frame labelled after the decision.

### Added

- `deploy/install.sh`: one-step install on any Jetson Nano with JetPack 4.6 and DeepStream 6.0 (build, TensorRT engines, self-tests, `facepipe.env`, systemd services for the current user and folder).
- `tools/reshape_scrfd.py`: makes the 640x384 SCRFD model, byte-identical to the InsightFace-REST reshape.

### Changed

- Portable: no user names or personal paths anywhere. systemd units are templates (`deploy/*.service.in`); the data folder defaults to `<app folder>/data`; the Makefile uses the system OpenCV unless `OPENCV_PC` is set; `deploy.sh` takes the app folder as an argument and the repository from the checkout; CI fails on personal home paths.

### Removed

- `facepipe.service` and `facepipe-web.service` at the repository root (replaced by the templates).

- Release assets and automated deploy: on each published release, the self-hosted runner on the Nano builds the tag, runs the self-tests, uploads `facepipe-X.Y.Z-jetson-nano-jetpack4.6-arm64.tar.gz` and `SHA256SUMS` to the release, installs it and rolls back automatically if the new version is not healthy within 60 s (`deploy/deploy.sh`, `.github/workflows/deploy.yml`). Off until the repository variable `NANO_RUNNER` is set.

- Face matching metric toggle: `FACEPIPE_METRIC=cosine` (default, `FACEPIPE_MATCH` 0.5) or `l2` (`FACEPIPE_L2_MATCH` 0.95), the same settings as the CCTV system. Events record the metric and the L2 distance. `facepipe --selftest` checks the match rule.

### Changed

- README: System Design (one face from camera to attendance record) and Solution Architecture (change to running device) sections with numbered diagrams and notes; FLOW.md carries a checked-against-code stamp; architecture diagrams show only what exists.

## [0.9.0] - 2026-11-06

First release. Version 1.0.0 will follow once every item in the README validation table is verified on hardware.

### Added

- DeepStream 6.0.1 pipeline for the Jetson Nano: SCRFD-10G detection (640x384, TensorRT FP16) with a custom decoder that keeps 5 facial landmarks, IOU tracking, ArcFace w600k_r50 recognition with Umeyama alignment, cosine gallery matching (threshold 0.5), up to 5 retries for unknown tracks.
- Enrollment binary `facepipe-enroll`; the running service hot-reloads the gallery.
- Daily JSONL attendance log with face snapshots.
- Web dashboard (Python 3.6 standard library): present, absent and unknown faces, live status, search and sort, CSV export, print layout, password login (PBKDF2), Content-Security-Policy with nonces.
- Video outputs: on-screen boxes and names, MJPEG live view (NVJPG), RTSP stream with login (NVENC), event clips from 3 s before to 5 s after each event, full-frame evidence images, weekly rotation of the recordings volume.
- Sources: RTSP (forced to TCP), video files, USB cameras (MJPEG on NVJPG or raw), CSI cameras (Argus); `FACEPIPE_DROP` keeps every Nth frame.
- systemd units, version shown by `facepipe --version`, in `status.json` and in the dashboard footer.
- CI: secrets/IP/biometric-file scan, docs-with-code check, Python 3.6 self-test, compile against NVIDIA's JetPack 4.6 headers with `-Werror`, cppcheck, Mermaid diagram rendering. Release workflow publishing GitHub Releases from tags.
- Engineering documentation (architecture, flows with diagrams, decisions, threat model, failure map, data, assumptions, test checklist, rollback, handover).

### Fixed

- nvtracker dropped every probe-added face (`bInferDone` not set with raw tensor output).
- ArcFace input colour order aligned with the reference server after its BGR bug was found and fixed upstream.
- `nvjpegenc` crash on DeepStream GPU buffers.
- libjpeg ABI clash between OpenCV `imgcodecs` and the L4T JPEG library.
- Wi-Fi RTSP camera at 4-5 fps over UDP.
- USB webcam halving its frame rate in dim light.
- Enrollment leaving its temporary file open, and crashing on an unreadable person folder.

### Known limitations

- Event clips, evidence frames and RTSP playback not yet verified end to end.
- Raspberry Pi HQ Camera (IMX477) needs resistor R8 removed before it works on the Nano.
- Match threshold not tuned on a labelled set; no liveness check.

[Unreleased]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/releases/tag/v0.9.0
