# Decisions

One entry per meaningful decision, newest last. Each records the options considered, the choice, why, the trade-off, and when to revisit it. All entries: date 2026-11-06, model Claude Opus 5.5; "owner" marks choices the owner made.

## D-001: Native C++ DeepStream application
Options:

1. Python with DeepStream bindings (pyds)
2. C++ application

Chosen: 2
Why: pyds is not installed on JetPack 4.6 and would have to be built; C++ needs nothing extra and keeps the probes cheap on the Nano's 4 ARM cores.
Trade-off: More code than Python; changes need a rebuild.
Revisit if: the pipeline moves to a newer DeepStream with pyservicemaker.

## D-002: Raw tensor output with a custom decoder
Options:

1. nvinfer with a custom bounding-box parser library
2. `network-type=100`, `output-tensor-meta=1`, decode in a pad probe

Chosen: 2
Why: the DeepStream 6.0 detection parser interface has no field for facial landmarks, and alignment needs them.
Trade-off: The probe must set `bInferDone` (see bugs/tracker-dropped-all-faces.md) and do NMS itself.
Revisit if: a newer DeepStream carries landmarks in object meta.

## D-003: Landmarks carried in `misc_obj_info`
Options:

1. NvDsUserMeta with copy and release functions
2. Pack 10 coordinates as int16 into the object's `misc_obj_info`

Chosen: 2
Why: Smallest working change; the tracker keeps the object meta, so the landmarks survive to the recognition probe.
Trade-off: Uses fields another component might want; coordinates are rounded to whole pixels.
Revisit if: another plugin needs `misc_obj_info`.

## D-004: IOU tracker
Options:

1. NvDCF
2. IOU

Chosen: 2
Why: The GPU is already the bottleneck (SCRFD); IOU runs on the CPU and is enough for people walking up to a camera.
Trade-off: More ID switches in crowds or occlusion. Usually they cost extra recognitions, but an association error can also carry a track's cached name to another person (corrected 2026-11-06 in council D-029).
Revisit if: crowded scenes cause repeated recognitions.

## D-005: Recognize once per track, retry unknowns up to 5 times
Options:

1. Secondary nvinfer on every face every frame
2. Once per track
3. Once per track, retried while unknown

Chosen: 3
Why: Recognition costs 41-48 ms per face; once per track keeps the GPU for detection. Retries fixed a real failure where the first frame of a track was unusable.
Trade-off: A person who leaves before the 5th unknown try is not logged as unknown.
Revisit if: unknown-visitor logging must be complete.

## D-006: SCRFD at 640x384
Options:

1. 640x640 (92 ms)
2. 640x384 (51-55 ms)

Chosen: 2
Why: 16:9 cameras waste almost half of a square input; 640x384 nearly doubles throughput.
Trade-off: Landmarks differ by 1-3 px from a 640x640 run; cosine against the server reference is 0.98 instead of 1.0.
Revisit if: small faces are missed.

## D-007: Drop frames before the pipeline (owner)
Options:

1. Detector on every 2nd frame (`interval=1`), tracker on all 30 fps
2. Keep every Nth frame at the source (`FACEPIPE_DROP`, default 2)

Chosen: 2 (owner, after a live comparison)
Why: Same 15 detections per second, half the conversion and tracking work, more headroom.
Trade-off: Less smooth tracking of fast motion.
Revisit if: the tracker loses fast-moving people.

## D-008: ArcFace input in RGB
Options:

1. BGR, to match the old server gallery
2. RGB, the model's training order

Chosen: 2, after the server was fixed (see bugs/reference-server-fed-bgr.md)
Why: RGB is what w600k_r50 was trained on; the server and gallery moved to RGB in CCTV v2.15.2.
Trade-off: Embeddings from galleries made before CCTV v2.15.2 do not match.
Revisit if: an old BGR gallery must be used (`REC_BGR` switch).

## D-009: Daily JSONL files instead of SQLite
Options:

1. SQLite
2. One JSON line per event, one file per day

Chosen: 2
Why: The SQLite development library is not installed; a few hundred lines a day need no database; files are easy to back up and read.
Trade-off: Queries across many days read whole files.
Revisit if: reporting across months becomes slow.

## D-010: Web page on the Python 3.6 standard library
Options:

1. Flask or FastAPI
2. `http.server` with threading

Chosen: 2
Why: No dependencies to install on Python 3.6, works offline, small surface.
Trade-off: Hand-written routing, Range handling and MJPEG.
Revisit if: the page grows into an application with forms and roles.

## D-011: Logins
Chosen: dashboard password stored as a salted PBKDF2 hash (200,000 iterations); RTSP password stored in a 0600 file, because gst-rtsp-server checks the password itself.
Why: Faces must not be viewable without a login; RTSP Basic auth cannot use a hash.
Trade-off: Plain HTTP and RTSP: credentials travel in clear on the LAN.
Revisit if: the device is reachable beyond a trusted LAN (add TLS).

## D-012: Live view as MJPEG from NVJPG
Options:

1. HLS from NVENC
2. WebRTC
3. MJPEG

Chosen: 3
Why: Every browser plays MJPEG in an image element with no player library; NVJPG encodes it in hardware; frames are written only while someone watches.
Trade-off: More bandwidth than H.264 (about 3 MB/s at 1080p 15 fps).
Revisit if: viewers are on slow links.

## D-013: RTSP server without its development headers
Options:

1. Install libgstrtspserver-1.0-dev
2. Declare the few functions used and link the installed runtime library

Chosen: 2
Why: No package changes on the device.
Trade-off: Hand-written prototypes must match the 1.14 ABI.
Revisit if: more of the RTSP server API is needed.

## D-014: Event clips instead of continuous recording (owner)
Chosen: H.264 clips from 3 s before to 5 s after each event, from an in-memory ring of encoded frames, plus a full-frame JPEG.
Why: Continuous 4 Mbps recording needs about 43 GB a day.
Trade-off: Nothing is recorded between events.
Revisit if: incidents between events matter.

## D-015: Recordings on an image-file volume on the microSD (owner)
Options:

1. Real partition (shrink the root filesystem offline)
2. 64 GB ext4 image file mounted with `loop,nofail`

Chosen: 2
Why: Same isolation as a partition, no risk to the root filesystem, no offline work.
Trade-off: Slight loop-device overhead; size fixed at creation.
Revisit if: write performance matters.

## D-016: Separate enrollment binary
Why: OpenCV `imgcodecs` and the L4T JPEG library cannot share a process (see bugs/libjpeg-abi-clash.md); enrollment needs `imread`, the live pipeline needs `nvjpegenc`.
Trade-off: Two binaries from one source file.

## D-017: Application on the microSD (owner)
Why: The external SSD is the owner's Windows backup disk; the microSD had 204 GB free.
Trade-off: microSD write endurance; recordings are capped at 64 GB and snapshots are small.

## D-018: Match threshold 0.5
Why: Same as the CCTV system's `COSINE_THRESHOLD`; live matches of the enrolled owner scored 0.50-0.78.
Trade-off: Not tuned on a labelled set; see ASSUMPTIONS.md.
Revisit if: false accepts or rejects are seen.

## D-019: RTSP over TCP
Why: Over UDP on Wi-Fi the camera delivered 4-5 fps because of packet loss; TCP gave a steady 30 fps.
Trade-off: Higher latency when the link stalls.

## D-020: CI compiles against NVIDIA's own JetPack headers (owner chose full compile)
Options:

1. Light CI only (self-test, static analysis, scans)
2. Compile inside a JetPack container under ARM emulation
3. Compile on a normal x86 runner against headers unpacked from NVIDIA's r32.7 arm64 packages

Chosen: 3
Why: JetPack 4 containers do not contain CUDA or TensorRT (they mount them from a Jetson), so option 2 cannot compile off-device. Headers are architecture-independent; unpacking the official .deb files (SHA-256 checked against NVIDIA's Packages index, no install scripts run) gives a real compile of both binaries in Ubuntu 18.04 with GCC 7.5, cached after the first run.
Trade-off: No link step in CI (the Jetson libraries are not available); OpenCV in CI is Ubuntu's 3.2 headers, the Nano uses 4.9. Linking happens on the device during deploy.
Revisit if: a Jetson becomes available as a trusted CI machine for pull requests.

## D-021: Actions drive old images with docker run
Why: Current GitHub actions (Node 20) need a newer glibc than Ubuntu 18.04 and Python 3.6 images have, so jobs run actions on the host runner and the checks inside `docker run`.
Trade-off: Slightly longer workflow files. NVIDIA headers are included with `-isystem` in CI (`INC=-isystem`): TensorRT 8.2's own headers use deprecated APIs and would fail `-Werror` as plain includes, while on the device they are system headers.

## D-022: Diagrams as Mermaid in Markdown, rendered in CI
Options:

1. Image files (draw.io, Lucid exports)
2. Mermaid blocks beside the prose they explain

Chosen: 2, same practice as the companion CCTV repository
Why: Text diagrams diff and review like code; images go stale silently. Each FLOW section has a diagram, a key mapping names to code and a numbered trace whose numbers match the diagram. Fixed colours: blue entry, green step, amber decision, purple outcome, red error, grey data stores; dotted arrows for optional or not-yet-built paths.
Trade-off: The same-change rule alone drifts (seen in the CCTV repository), so CI renders every diagram with mermaid-cli 12.0.0 and the pull-request template asks which diagram sections a change touched. CI cannot check that a diagram is still true, only that it renders.
Revisit if: diagrams are generated from code instead.

## D-023: Semantic Versioning, one VERSION file, releases from tags
Options:

1. Untagged main
2. Version from `git describe`
3. A `VERSION` file, `CHANGELOG.md`, GitHub Releases from `vX.Y.Z` tags

Chosen: 3
Why: The device may build from a source archive with no git history, so the version must live in a file; Keep a Changelog notes become the release notes; the Release workflow reruns the full CI and refuses a tag that does not match `VERSION` or has no changelog section.
Trade-off: Two places to edit when releasing (VERSION and CHANGELOG), checked by the workflow.
Revisit if: releases become frequent enough to automate the bump.

The first release is 0.9.0, not 1.0.0: 1.0.0 is reserved for when every item in the README validation table is verified on hardware.

## D-024: README System Design and Solution Architecture
Chosen: two README sections right after the introduction, following the owner's other repositories (CCTV and HalalLens): System Design traces one face from camera to attendance record, Solution Architecture traces a change to the running device. Each has one top-to-bottom Mermaid diagram with numbered nodes, a how-to-read line, numbered notes with bold lead-ins naming real settings and decision IDs, and a link to the detailed doc. FLOW.md and ARCHITECTURE.md hold the detail and win when they disagree with the README.
Why: A reader sees the runtime path and the delivery path in two minutes, without file paths.
Trade-off: Three places to keep in sync; the pull-request template asks about all three, CI renders the diagrams, and FLOW.md records the commit it was last checked against. Only what exists on main is drawn; planned work lives in HANDOVER.md. No versions of other components are named in README notes.

## D-025: Cosine or L2 matching, chosen by a setting (owner)
Options:

1. Cosine only
2. A toggle between cosine similarity and L2 distance

Chosen: 2 (owner request), default cosine
Why: The CCTV system exposes both (`COSINE_THRESHOLD` 0.5, `L2_THRESHOLD` 0.95); the Nano uses the same model and normalisation, so the same settings carry over. Embeddings are L2-normalised, so L2 = sqrt(2 - 2 cosine): the closest photo is identical under both, and only the threshold rule differs. The defaults are not equivalent: L2 0.95 equals cosine 0.549, slightly stricter than cosine 0.5.
Trade-off: Two thresholds to tune; events always store the cosine score and also the L2 distance and the metric used, so the dashboard and reports stay comparable.
Revisit if: a labelled evaluation shows one metric and threshold clearly better.

## D-026: Release assets built on the Nano; no Docker images (owner)
Options:

1. Docker images on GitHub Packages (ghcr.io)
2. Release assets: binaries and files the Nano installs, with checksums
3. Both

Chosen: 2 (owner)
Why: The project runs natively on the Nano (no Docker on the device), and a CI machine could not run a JetPack 4 image's GPU code anyway (JetPack 4 containers mount CUDA and TensorRT from the host). The binaries must link against the device's JetPack libraries, so they are built and tested on a Nano by the self-hosted runner (owner choice for CD) and attached to the release with `SHA256SUMS`.
Trade-off: Assets exist only for releases deployed while the runner is registered; without it, `deploy/deploy.sh` is run by hand and the release has no assets.
Revisit if: Docker is used on a Jetson, or a central host runs the pipeline.

Runner security (owner: block pull requests from anyone else): the Deploy workflow triggers only on published releases and manual dispatch, never on pull requests; it uses the `production` environment; workflows from outside contributors need the maintainer's approval (repository setting); the job is skipped unless the repository variable `NANO_RUNNER` is `true`.

## D-027: Portable install with no user or device data (owner)
Chosen: anyone with a Jetson Nano on JetPack 4.6 and DeepStream 6.0 installs with `sh deploy/install.sh` from any folder. systemd units are templates filled with the current user and folder; every path defaults to the app folder; the Makefile uses the system OpenCV (JetPack 4.1.1) unless `OPENCV_PC` points at a custom build; `deploy.sh` takes the app folder as an argument and reads the repository URL from its checkout; `tools/reshape_scrfd.py` reproduces the detector model byte for byte (md5 be7f0a4616d911b072ac980ac45988c2, the same as the deployed model). CI fails on any `/home/<name>/` path.
Why: Owner request: the repository must not depend on the owner's username or other data, and must run on another Nano with minimal changes.
Trade-off: The two models still have to be obtained by hand (InsightFace license); the install script checks for them and says where they come from.

## D-028: Adaptive detection that keeps video at 15 fps (owner, council)
Options:

1. Split pipeline: detection branch and video branch, metadata handed across by timestamp
2. Own SCRFD call in a probe instead of nvinfer, deciding per frame whether to detect
3. Two nvinfer instances with different intervals, or restarting the pipeline to change `interval`

Chosen: 2 (owner chose "adaptive, keep video smooth"; design agreed with Codex in a council, 5 exchanges). Model: Claude Opus 5.5 with Codex GPT-6.1 Sol (council).
Why: nvinfer's `interval` is changeable only in NULL or READY state. Option 2 keeps one frame path and reuses `Engine`, `decode_scrfd` and the landmark gate; video buffers are never skipped, only inference. Policy: start active; after 10 s without a raw detection, detect every 6th processed frame (2.5/s); the first face restores full rate. Detector work idle: about 765 to 128 ms per second (arithmetic, not a GPU-load prediction).
Trade-off: Up to about 400 ms more before the first detection of someone entering an empty scene. Detection runs in the streaming thread, so smoothness must be measured, not assumed.
Revisit if: measured output cadence falls below 15 fps under recognition load (then option 1).
Status: implemented in v1.1.0. Same results as nvinfer on identical frames (gallery test video: same box, embedding cosine 1.0000, same match 0.979). Empty scene on the CSI camera: 14.8-15.0 fps (12.2 before), 2.4 detections/s, GPU about 0% between detections. Still to measure: delivery gaps and fps with faces present during recognition bursts.

## D-029: Bounded shutdown and recovery (council)
Options:

1. Keep being killed by SIGTERM; rely on the watchdog
2. Handle SIGTERM: one cleanup path, bounded by a deadline the watchdog enforces
3. Also restart nvargus-daemon automatically

Chosen: 2. Model: Claude Opus 5.5 with Codex GPT-6.1 Sol (council).
Why: The CSI stall followed starts after a long-running process was killed mid-stream; a clean stop closes the Argus session. Order: mark stopping (no new clips or evidence waits), pipeline to NULL, finish queued recognitions, finalize active and finalizing clips; the watchdog thread forces exit 4 after 10 s, also when the frame rule is off. The handler is installed just before the main loop, so a stall before it still dies on SIGTERM at once. `deploy.sh` accepts only reports from the current `MainPID` with advancing frames (Codex: a crashed process's reports could otherwise pass for its stalled replacement).
Trade-off: A forced exit can lose queued work or an unfinished clip. `TimeoutStopSec=15s` is in the unit template; existing installs keep systemd's default until `install.sh` is re-run with the owner's approval. No automatic nvargus-daemon restart (needs root; recovery by the watchdog was seen).
Revisit if: the stall recurs with clean stops (then capture a backtrace and consider an nvargus-daemon restart).

## D-030: Daily log files with the owner's layout (owner)
Options:

1. journald only
2. logrotate on a single file
3. Each process writes and rotates its own file

Chosen: 3
Why: The owner's layout (`logs/<year>/<Month>/<module>.log`, backups `<module>.log.DD-Mon-YYYY` in the month folder, at most 31 a month) and line format (`[DD-MM-YYYY] hh:mm:ss.fff AM/PM, Weekday [LEVEL]`) do not fit logrotate's naming, and logrotate would need a root cron entry. Rotation happens with the first line after midnight; a file left by a run that ended before midnight is renamed by date at the next start; a second run on the same day appends to that day's backup. Lines also go to stderr, so `journalctl` keeps working.
Trade-off: Prints from libraries (GStreamer, DeepStream, TensorRT plugins) reach only the journal. No deletion of old months: the owner set no retention, and the logs are small (one status line a minute).
Revisit if: logs fill the microSD, or a retention rule is set.

## D-031: Automatic deployment by a pull timer on the Nano (owner)
Options:

1. Self-hosted GitHub Actions runner on the Nano (the Deploy workflow, D-026)
2. A systemd timer on the Nano that polls the public release list and runs `deploy.sh`

Chosen: 2 (owner)
Why: The runner needs a registration token typed on the device and an agent with outbound GitHub access that executes workflow jobs; a public repository adds risk to that. The timer needs nothing secret: every 10 minutes `deploy/update.sh` reads the latest release tag (public API, no token) and, if it is newer than `VERSION`, runs that release's `deploy.sh`, which already builds, self-tests, health-checks and rolls back. A failed release is not retried until a newer one exists; it never downgrades; a lock stops overlapping runs; an unreachable GitHub is logged once per outage. Same model as the CCTV edge watcher on the Orin.
Trade-off: Up to 10 minutes from release to deploy; the Nano needs internet for updates (it stays fully offline for its work); a deploy needs the camera online (health = advancing frames), otherwise it rolls back and that release is skipped. The Deploy workflow stays in the repository for a runner, off unless `NANO_RUNNER` is set.
Revisit if: several devices need staged rollouts, or the Nano loses internet access permanently.

## D-032: Pull requests with auto-merge for code, docs straight to main, releases from VERSION (owner)
Options:

1. Push everything to main and tag releases by hand (the practice until v1.1.1)
2. The CCTV repository's practice: code through a branch and pull request merged automatically on green CI; docs-only changes straight to main; releases made by a workflow, never by hand

Chosen: 2 (owner: "Contact the CCTV session to know how we decide when to create a PR and when to directly push to main")
Why: CI becomes the merge gate for anything a device runs, and a release cannot be skipped or tagged on an unchecked commit. Code, workflows, deploy scripts, configs and the Makefile go through `feat/`, `fix/`, `ci/` or `docs/` branches with Conventional Commit titles and a What/Proof body; pull requests auto-merge (squash, branch deleted) once the 5 CI checks pass on `main`'s branch protection. Docs and Markdown only go straight to main with `[skip ci]`. A merged change that raises `VERSION` makes the release workflow run CI on that commit, tag it and publish with the CHANGELOG section; the device timer then deploys it (D-031). This repository is public, so GitHub's native auto-merge and branch protection do it; the CCTV repository needs its own automerge workflow only because it is private on GitHub Free.
Trade-off: Code changes wait for CI (about 3 minutes) before reaching main; admins can still push to main directly, so the rule is followed by practice, not forced.
Revisit if: other contributors join (then require reviews).

## D-033: Website, registration, uploads and two cameras: the plan (council)
Owner's request: a professional multi-page website with the live stream, service start and stop, camera enable and disable, video upload with inference, and a registration portal. Owner's answers: uploads queued beside live, up to 2 cameras, self-service registration with approval, MJPEG for the browser.
Chosen, agreed with Codex in a council (2 exchanges; messages crossed three times on auth and storage). Model: Claude Opus 5.5 with Codex GPT-6.1 Sol (council).

- v1.2 website: stdlib `http.server` grown into a small package with templates and static assets, `web.py` stays the entry point. Basic auth kept (one administrator); POST-only changes with CSRF tokens and checked Origin/Host; public `/register` routes separate from admin routes; bounded threads, bodies and MJPEG clients; downloads streamed in chunks (today `Handler.file` reads a whole range into memory). Workflow state in stdlib sqlite3; attendance stays JSONL.
- v1.2 service control: an owner-approved sudoers rule for start, stop and restart of `facepipe` only, fixed arguments, observed state shown; the web never controls itself. An intentional stop survives camera saves and automatic updates (a desired-state file that `deploy.sh` honours).
- v1.3 registration: admin-made single-use invitations (128-bit tokens, expiring); updates bound to a chosen person; opaque IDs, Unicode names; consent record; photos decoded and re-encoded on the CPU before enrollment; admin review. The gallery is published only after enrollment succeeds, by one recoverable writer, with a generation counter so live recognition drops cached names. Fixes the enroll bug that replaces the gallery even when no photo succeeded. Retention, client-IP storage and erasure of history are owner decisions.
- v1.4 uploads: a lean `--job` mode first, then a capacity test beside live recognition (memory and swap trace, live delivery gaps; MemAvailable never under 300 MB, live stays 15 fps). If it fails, one process owns the engines with live-priority scheduling. Live is never paused. No attendance from uploads.
- v1.5 two cameras: one process, shared engines, an aggregate detection budget (two active frames cost about 102 ms), per-camera state and `(camera, track)` keys, enable or disable by validated config and a clean restart.

Rejected: polkit (Ubuntu 18.04's polkit cannot limit to one unit); a fully public registration page (open biometric upload endpoint); two full processes for two cameras (memory); pausing live for uploads (owner chose beside live).
Trade-off: Plain HTTP on the LAN still exposes the login and faces to anyone sniffing it.
Revisit if: the upload capacity test fails, or the device leaves a trusted LAN (then TLS).
