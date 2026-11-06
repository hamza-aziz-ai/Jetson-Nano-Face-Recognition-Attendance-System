# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html): MAJOR for breaking changes to configuration, data files or deployment, MINOR for new features, PATCH for fixes. See docs/RELEASING.md.

## [Unreleased]

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

[Unreleased]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/releases/tag/v0.9.0
