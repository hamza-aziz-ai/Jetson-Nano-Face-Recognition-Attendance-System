# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html): MAJOR for breaking changes to configuration, data files or deployment, MINOR for new features, PATCH for fixes. See docs/RELEASING.md.

## [Unreleased]

## [1.13.1] - 2026-11-07

### Fixed

- The repository is now private, which silently stopped automatic updates: reading the release list and cloning a tag both need credentials. Both scripts now read a read-only token from `<app_dir>/gh_token` (mode 0600, created by the owner) and pass it through the environment, never on a command line or in a URL. Without the file they say exactly that instead of reporting the device as offline (D-044).

## [1.13.0] - 2026-11-07

### Changed

- The website now ships as compiled machine code. Every deploy turns `web.py` into `webapp.so` with Nuitka and leaves a two-line `web.py` that starts it, so an installed device carries no readable Python (D-043). Nothing changes in how the site is used or run: the same `python3 web.py --selftest`, `--set-password` and `--set-rtsp-password` commands work. A device needs Nuitka for updates to succeed: `pip3 install --user nuitka==4.3.4`.

### Fixed

- Every page asked the device for an icon that no release had ever shipped, and the server answered by closing the connection instead of saying "not found". `web/favicon.svg` is now in the shipped file list, and a missing file answers 404 (docs/bugs/favicon-missing-500.md).

## [1.12.3] - 2026-11-07

### Fixed

- Over the internet, a page that was already open when the device went down kept saying "Page cannot reach the device"; the maintenance page appeared only after a manual refresh. The page's 10 s status check now reloads it when Cloudflare answers for the device, so it switches to the maintenance page by itself, and back to the site when the device returns. The maintenance page now also covers Uploads, Users and Account (run `sh deploy/maintenance/deploy.sh <hostname>` once to add those routes).

## [1.12.2] - 2026-11-07

### Fixed

- On the Cameras page the long USB camera ID ran over the CSI sensor box next to it. Form fields now stay inside their column, and each USB camera is listed by its name first, then its ID.

## [1.12.1] - 2026-11-07

### Fixed

- A USB camera chosen on the Cameras page is now saved by its stable ID (`/dev/v4l/by-id/...`: vendor, model, serial) instead of `/dev/videoN`. After the Wi-Fi card was fitted the webcam came back as `/dev/video0` instead of `/dev/video1`, and recognition stopped until the camera was chosen again. Choose the USB camera once more on the Cameras page to switch to the ID.

## [1.12.0] - 2026-11-07

### Changed

- Unknown faces are logged only from a clear view (owner's choice, after 25 "unknowns" in a morning that were mostly the owner at bad angles): roughly frontal, eyes at least 28 px apart in the camera's own resolution, not cut off at the picture's edge, not too dark. The clearest try of each face is the one saved. A face that never shows clearly is labelled Unknown on screen but nothing is saved (counted as `unclear` in the log); if it turns clear later it gets one more try. Known people are recognized exactly as before. Each event records the quality numbers of its picture, and the limits can be tuned with `FACEPIPE_UNKNOWN_*` settings. Agreed with Codex in a council (D-042).

### Fixed

- A recognition try that failed before reaching recognition (queue full, picture not readable) used up one of the face's 5 tries; it no longer does.

## [1.11.3] - 2026-11-07

### Changed

- Every match number names its rule, `L2 0.87` or `Cosine 0.78`, and the Match and Best match headings name the rule in use, e.g. "Match (L2 ≤ 0.90)" (owner: "It should be clear on Webpage whether the match is using COSINE or L2").

## [1.11.2] - 2026-11-07

### Fixed

- Match numbers followed the cosine score even when face matching was set to L2 (owner: "-0.04" under L2 looked wrong). Overview, Attendance and the unknown-face list now show the number of the rule that decided, e.g. "L2 0.87" (distance, lower is closer) or "0.78" (cosine). The CSV has a new `match_rule` column (`l2` or `cosine`) before `best_match` (bugs/match-shown-as-cosine.md).

## [1.11.1] - 2026-11-07

### Fixed

- The email and phone fields on the Users page had the browser's plain look; they now match the other fields (owner).

## [1.11.0] - 2026-11-07

### Added

- Recognition state shown very clearly (owner): while recognition is stopped, not running or without a camera picture, every page shows a red or amber banner saying so, with who stopped it and when ("Recognition is STOPPED. No attendance is recorded and the live view is off. Stopped by Hamza at 11:30 AM."). The System page shows the state in large type, and its Start, Stop and Restart buttons read "Starting…", "Stopping…" or "Restarting…" until the action is done. The web log names who started, stopped or restarted it.

## [1.10.1] - 2026-11-07

### Security

- The RTSP stream (`rtsp://...:8554/live`) accepted any viewer that could reach the device, also from the internet (owner's report). Now only the local network may watch it in VLC or an NVR: every request from an address that is not private, link-local or loopback is answered 403 Forbidden, which also covers a router port forward. Its internal video input listens on the device only. The Live page shows the RTSP address only to visitors on the local network (D-041, bugs/rtsp-reachable-from-internet.md).

## [1.10.0] - 2026-11-07

### Added

- User accounts (owner): a Users page for administrators adds people by email, phone number (with country code) or both, as **admin** or **viewer**. Viewers see everything (Overview, Live, Attendance, CSV, faces, evidence, clips, the System and Cameras pages without their controls) but change nothing and do not see People, Video upload or Users (owner's choice). The device sends no email or SMS: the admin gets a single-use set-password link (48 h) to pass on. Design agreed with Codex in a council (D-040).
- Your account page: every user's own sign-in names and password change.
- New favicon, and a GitHub social preview (`docs/social-preview.svg`).

### Changed

- Sign-in accepts an email, a phone number or the owner's user name. All sessions start again once after this update (new cookie format). A role or password change, or deleting an account, ends that account's sessions at once; downloads and the live view stop too.
- New passwords need at least 15 characters (NIST SP 800-63-4), also for `--set-password`; spaces are allowed.
- After 5 wrong passwords an account pauses briefly (up to 30 s), besides the existing per-visitor limit.

## [1.9.0] - 2026-11-07

### Added

- Two network cameras at once (owner): enable up to two RTSP cameras on the Cameras page. One recognition process handles both (one detector and one recognizer in memory: 2.3 GB in use, against 3.45 GB for two separate processes); each camera reconnects on its own and the other keeps running. The live view, RTSP stream, clips and evidence frames show both side by side; a camera that is down is marked offline on the picture and on the Overview. Events record the camera, and the 5-minute rule applies per person per camera, as in the CCTV system (D-039).
- Video upload (owner): a new Video upload page runs face recognition on a recorded video (H.264 or H.265, up to 1920x1080 and 30 minutes; 500 MB on the home network, 95 MB through the internet address) and offers the annotated copy for download, with the names found. Live cameras always go first: a video is processed while no one is in view. Uploaded videos never count as attendance and are deleted after 7 days (owner) (D-039).

### Changed

- Evidence frames are now taken only from a frame certified to show the decided name on that camera, not "the next frame"; with two cameras the next frame could be the other camera's (D-039, agreed with Codex in a council).
- Enrollment and video processing never run at the same time (a shared lock); approving a registration during an upload asks to try again when it is done.

### Fixed

- `deploy/install.sh` ran the web self-test against the real `data/` folder, where the camera test deletes the camera setup; it now uses a temporary folder like `deploy.sh`.

## [1.8.1] - 2026-11-07

### Fixed

- Duplicate entries (owner): the same person was logged again each time the tracker lost and found the face, seconds apart. Now the CCTV system's 5-minute rule applies: a person already logged in the last 5 minutes is not logged again (no event, snapshot, frame or clip), and an unknown face is not logged again if it matches an unknown face logged in the last 5 minutes. A restart keeps the rule for known people (D-038).

### Added

- Maintenance page (owner): when the Nano cannot be reached through the Cloudflare Tunnel, a Cloudflare Worker (`deploy/maintenance/`) shows "Down for maintenance" in the site's look and reloads every 30 s, instead of Cloudflare's error page (D-037).

### Changed

- Remote access without Cloudflare Access (owner, 2026-11-07): the site's own sign-in page is the only lock (D-035 update, docs/REMOTE-ACCESS.md).

## [1.8.0] - 2026-11-07

### Added

- Sign-in page (owner: "Can we have an application login popup instead of this"): the browser's login pop-up is gone. Pages lead to `/login` and back; a signed session cookie lasts 12 hours, survives restarts and automatic updates, and ends at once when the password changes (`--set-password`). Sign out is in the header. Design agreed with Codex in a council (D-036).
- Own "Page not found" page instead of plain text.

### Changed

- HTTP Basic login is no longer accepted (scripts sign in at `/login` and keep the cookie). Form tokens no longer expire when the website restarts.

## [1.7.2] - 2026-11-07

### Fixed

- The System page named the camera from `facepipe.env` ("CSI camera, sensor 0") while recognition used the camera chosen on the Cameras page; it now shows the camera in use, for example "Phone Camera (network camera at <host>)" (docs/bugs/system-page-wrong-camera.md).

## [1.7.1] - 2026-11-07

### Fixed

- The Live page froze on its last frame when the web server restarted (an automatic update at 01:59 did it while the owner watched): the picture now reconnects by itself. `/api/status` reports when the server started; the page reopens the video when that changes, when the device is reachable again, or 3 s after the stream breaks.

## [1.7.0] - 2026-11-07

### Added

- Face matching on the System page (owner): choose cosine similarity or L2 distance and set each threshold (cosine 0.30-0.90, L2 0.50-1.20); saved to `data/matching.conf`, which facepipe reads over `FACEPIPE_METRIC`, `FACEPIPE_MATCH` and `FACEPIPE_L2_MATCH`; recognition restarts with it (D-025).

## [1.6.2] - 2026-11-07

### Changed

- Overview and Attendance show an enrolled person's own picture (profile photo) instead of the camera's face crop; the crop stays in the evidence frame, and unknown faces keep their crop (owner's request).
- Registration page: the whole page is one dark backdrop with the card centred (no large empty area below), and the button reads "Send Registration", centred (owner's review).

## [1.6.1] - 2026-11-07

### Fixed

- A person recognized in the first seconds after a (re)start got no clip, only the evidence frame (owner's report: "Where does the clip part have gone, only frame is there?"). The H.264 encoder had not produced its first frame yet, and the event silently went without. Such an event now reserves its clip, which starts at the encoder's first keyframe (without pre-roll); any event that still cannot have a clip logs why.

## [1.6.0] - 2026-11-07

### Added

- Device health on the System page (owner: "temperature, fan, memory, CPU/GPU load, so I can shut the service remotely"): temperature per zone, per-core CPU load and clock, GPU load and clock, fan power, memory and swap, uptime; refreshes every 5 s (`/api/health`); a warning above 80 °C or under 400 MB free memory, an alert above 90 °C.
- Behind a Cloudflare Tunnel the visitor's address comes from `CF-Connecting-IP`, trusted only on connections from this host (cloudflared), so login lockout and the registration rate limit apply per visitor, not to everyone at once.

## [1.5.0] - 2026-11-07

### Changed

- Website redesign (owner: "a professional look like an enterprise solution yet user friendly and easy to navigate"): a sidebar with icons for the six pages and the live status, a header with the page name and status, refined cards, tables, buttons and forms, and a menu button on small screens.
- Registration page: its own layout, centred, with a drop zone for photos, previews of the chosen photos and a clearer consent box; the form uses the full card width.
- The footer no longer claims that no data leaves the device; it says recognition runs on the device.

## [1.4.1] - 2026-11-07

### Fixed

- People page: person cards were 200 px wide, so the New photos and Remove buttons overlapped the name and the photo count (owner's screenshot). Cards are now at least 280 px with the buttons in a row underneath.

## [1.4.0] - 2026-11-07

### Added

- Cameras page (D-034): choose network cameras (RTSP), a USB camera or a CSI camera and the resolution; add, test, enable, disable and remove network cameras (one active in this release); revert to the previous setup. Recognition restarts with the new camera.
- facepipe reads the chosen camera from `data/cameras.active`, which overrides `FACEPIPE_URI` (the fallback); the address of a camera chosen this way is not logged.

## [1.3.0] - 2026-11-07

### Added

- Registration by invitation (D-033, D-034): the admin creates a single-use link (48 h) on the People page; the person submits a name, 2-5 photos and consent from any device on the network; the admin approves or rejects. New photos for an enrolled person work the same way, and people can be removed from recognition (past attendance stays).
- `facepipe-enroll --check <photo> <out.jpg>`: CPU-only validation and re-encoding of an uploaded photo.

### Changed

- Approval enrolls a candidate gallery in a staging folder and publishes it only when it is complete; the live gallery is never left half-written.
- Live recognition detects a new gallery by inode and nanosecond time (was: whole seconds) and forgets names cached on tracks.

### Fixed

- `facepipe-enroll` replaced the gallery even when no photo could be enrolled; it now keeps the previous one.

## [1.2.2] - 2026-11-07

### Changed

- System page: storage is labelled by where it lives: "microSD card" (system, app, data and logs) and "Recordings" (a volume on the microSD card), instead of "App storage" and "Recordings volume" (owner's question).
- A person's picture on the website is `gallery/<name>/profile.jpg` (or `.png`) when present, otherwise their first photo.

### Fixed

- `web.py --selftest` (run by every deploy) wrote its expected refusals and failed logins into the device's real `web.log`; it now logs to a temporary folder.

## [1.2.1] - 2026-11-07

### Fixed

- Start, Stop and Restart were refused as "expired form or another site": under `Referrer-Policy: no-referrer` Chrome posts `Origin: null`. Pages now use `same-origin` (docs/bugs/web-actions-refused.md).
- Pausing the live view left a broken image; it now keeps the last frame, labelled Paused.

### Changed

- Confirmations use the site's own dialog instead of the browser's pop-up.

## [1.2.0] - 2026-11-07

### Added

- Multi-page website (D-033): Overview, Live, Attendance, People and System, with one layout and navigation; the live camera view has its own page.
- Start, Stop and Restart of the recognition service from the System page, through a sudoers rule limited to those three commands (`deploy/install.sh` installs it); a stop is kept across automatic updates (`data/desired`).
- System page: frame and detection rates, camera, storage, last update and recent log lines.

### Changed

- Styles and scripts moved to `web/` files, so the Content-Security-Policy allows no inline code; the match bar uses `<meter>`.
- Downloads and clips are streamed in chunks instead of being read into memory; the server limits concurrent requests (24), live viewers (4), request bodies (4 KB) and slow clients (30 s).
- Failed logins: 10 in 15 minutes lock that client out without hashing.

## [1.1.2] - 2026-11-06

### Fixed

- Event clips came out empty or short (owner's report): the encoder made an IDR keyframe only every 256 frames, so a clip usually started without SPS/PPS and its parser dropped frames until the next IDR. Every keyframe is now an IDR (1 s apart); clips are about 8 s with 3 s before the event. Each clip logs how much pre-roll it started with (docs/bugs/clips-empty-or-short.md).

## [1.1.1] - 2026-11-06

### Added

- Automatic deployment (D-031): `facepipe-update.timer` runs `deploy/update.sh` every 10 minutes; a newer GitHub release is deployed with that release's `deploy.sh` (build, self-tests, health check, rollback). No token needed. `deploy/install.sh` installs and enables the timer; `deploy.sh` now also installs `deploy/update.sh`, so the updater updates itself. Log: `logs/<year>/<Month>/update.log`.

## [1.1.0] - 2026-11-06

### Added

- Adaptive detection (D-028): the face detector runs on every frame while faces are around and on every 6th frame after 10 s without one (`FACEPIPE_IDLE_AFTER`, `FACEPIPE_IDLE_STEP`; `FACEPIPE_IDLE_AFTER=0` turns it off). Live view, RTSP and clips stay at full frame rate. Empty scene on the CSI camera: GPU from 89-99% to about 0% between detections, 14.8-15.0 fps instead of 12.2, about 5 °C cooler within a minute.
- `status.json` reports `det_fps` (detections per second); the minute log line includes it, and the switches between idle and active are logged.

### Changed

- The detector is called from a probe with its own TensorRT engine (the same engine file as enrollment) and a VIC letterbox, instead of `nvinfer`; `pgie_scrfd.txt` is no longer used or shipped.

### Fixed

- The first status line after start showed an impossible frame rate (no measured interval yet).

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

[Unreleased]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.8.0...HEAD
[1.8.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.7.2...v1.8.0
[1.7.2]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.7.1...v1.7.2
[1.7.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.6.2...v1.7.0
[1.6.2]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.6.1...v1.6.2
[1.6.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.4.1...v1.5.0
[1.4.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.2.2...v1.3.0
[1.2.2]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.1.2...v1.2.0
[1.1.2]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.3...v1.1.0
[1.0.3]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/hamza-aziz-ai/Jetson-Nano-Face-Recognition-Attendance-System/releases/tag/v0.9.0
