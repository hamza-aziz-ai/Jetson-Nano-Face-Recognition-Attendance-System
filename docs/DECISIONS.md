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
Update 2026-11-07 (owner): the toggle is also on the website's System page (`data/matching.conf` overrides the `facepipe.env` values; bounded thresholds; recognition restarts).

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

## D-034: Registration by invitation, retention, and camera choice (owner)
Owner's answers of 2026-11-07, refining D-033:

- **Registration:** by invitation link only. The admin creates a single-use link on the People page, valid 48 hours; the person submits a name, 2-5 photos and consent; nothing is enrolled until the admin approves. An open registration page was offered and declined.
- **Retention of submissions:** a rejected registration's photos are deleted at once; one never reviewed is deleted after 7 days.
- **Client IP address:** not stored with a registration; only the web log keeps it, like other requests.
- **Cameras:** the website chooses how the device is integrated: a network camera (RTSP), a USB camera or a CSI camera. Several cameras make sense only for RTSP, because network cameras can sit in different places, while USB and CSI cameras are physically attached to the device. So: one USB or one CSI camera, or a list of RTSP cameras that can each be enabled or disabled, with up to 2 running at once (owner's earlier answer). Order: the camera page with the source choice and the RTSP list first (one RTSP camera active), then two RTSP cameras at once.

Why: owner decisions (exposure, retention, personal data, product scope). RTSP camera addresses contain logins, so they stay in a mode-0600 file on the device and the website never shows the password part.
Revisit if: a site needs a USB or CSI camera together with a network camera.

## D-035: Remote access through a Cloudflare Tunnel with Cloudflare Access (owner)
Options:

1. Home network only
2. Port forwarding on the router with a certificate on the Nano
3. Cloudflare Tunnel, site login only
4. Cloudflare Tunnel with Cloudflare Access in front

Chosen: 4 (owner: hostname `attendance.hamzaaziz.dev`, Access in front, cloudflared download approved). Practice taken from the owner's Medical-ChatBot and HalalLens projects: remotely managed tunnel created by the owner, token only in a private file, pinned cloudflared without auto-update, no cloudflared logs, the public hostname added last. Adapted to the Nano: no Docker, so the official arm64 `.deb` and a systemd service with `DynamicUser=yes` (`deploy/cloudflared-facepipe.service`); the visitor address comes from `CF-Connecting-IP`, trusted only on connections from this host.
Why: the owner wants to see who came and went, and the device's health, from anywhere, and to stop recognition remotely if something goes wrong. A tunnel opens no inbound port. The site holds faces and attendance of a household, so Access (the owner's email) stands in front of the site's own login; `/register*` and `/static/*` bypass Access because invitation links are for people without an account and still need a valid single-use invitation.
Trade-off: Faces viewed remotely pass through Cloudflare (TLS ends at its edge); the device needs internet for remote access; a Cloudflare outage cuts remote access, not the home network.
Revisit if: the site must be reachable without Cloudflare, or more administrators need access.
Update 2026-11-07 (owner): Cloudflare Access removed ("I want to remove that cloudflare login and I do want people to reach to https://attendance.hamzaaziz.dev/"), after being told the site's own sign-in page then faces the internet. The sign-in page (D-036) with the per-visitor lockout is the only lock; the password must be strong.

## D-036: Sign-in page with a signed session cookie instead of HTTP Basic (owner, with Codex (council))
Model: Claude Opus 5.5 with Codex GPT-6.1 Sol (council, agreed after 3 exchanges).
Options:

1. Keep HTTP Basic (browser pop-up)
2. Sign-in page with server-side sessions (a table of random ids)
3. Sign-in page with a stateless signed cookie

Chosen: 3. Token `v1.<expiry>.<HMAC-SHA256>` over a purpose label, the expiry and the current password hash, keyed by 32 random bytes in `data/session.key` (0600, created once; damaged = the website refuses to start). Absolute 12 h; cookie `fp_session`, host-only, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` when the visitor used HTTPS (only `X-Forwarded-Proto` from cloudflared on this host counts). `web_auth` is read again when it changes and written atomically, so a new password ends every session and open form. Form tokens use the same key with their own label, so they survive restarts. Basic is no longer accepted (browsers resend cached Basic credentials, which would defeat sign-out). POSTs, the sign-in included, need the browser's Origin (or Referer) to equal this site, scheme included. After sign-in only known page paths are followed (`next`). Pages redirect to the sign-in; data, video and files answer 401 without a challenge; the live stream closes when the session ends.
Why: the owner asked for the site's own sign-in instead of the browser pop-up, and for a sign-out. Codex found that "starts with /" let `/\evil.example` redirect away, that the cached password hash made "a new password ends sessions" untrue, and that `CF-Connecting-IP` says nothing about HTTPS.
Rejected: a Host allowlist (the cookie is host-only and forms carry an unguessable token; it would also put host names into the repository); a separate pre-sign-in form token (one account, so a forced sign-in gains an attacker nothing).
Trade-off: Sign-out clears the cookie but cannot revoke a stolen token before it expires (12 h); delete `data/session.key` and restart the website to end every session. On the home network the site is plain HTTP, so the password and cookie cross the LAN in clear (as with Basic).
Revisit if: more than one account is needed, or sessions must be revocable one by one.

## D-037: Maintenance page as a Cloudflare Worker on the page addresses (owner)
Options:

1. Cloudflare's own error page (error 1033 / 502)
2. Cloudflare custom error pages
3. A Worker that passes requests through and answers with a maintenance page only when the Nano cannot

Chosen: 3 (owner asked for a custom maintenance page "shown when tunnel is broken" and signed in to Cloudflare with `wrangler login` for it). `deploy/maintenance/worker.js`, no dependencies: HTTP 530, 502, 504 and 520-529 from the origin, or a failed fetch, become a 503 page in the site's look with `Retry-After: 30` and a 30 s reload; the website's own answers, its 503s included, pass through. Routes only on page addresses (`deploy/maintenance/deploy.sh <hostname>`).
Why: when the tunnel or the Nano is down, the request never reaches the device, so the page must live at Cloudflare. A Worker is code in the repository and works on the free plan; it runs only on page addresses so the status polls (every 5-10 s per open page), files and clips do not count against the 100,000 requests a day.
Trade-off: Pages now pass through a Worker (about 1 ms); if the daily limit were ever reached, those page requests would fail until the next day (remove with `npx wrangler delete --name facepipe-maintenance`). Status polls and the live stream still get Cloudflare's error while the device is down; the open page then says it cannot reach the device.
Revisit if: traffic grows near the free limit, or a plan with custom error pages is bought.

## D-038: Log a person at most once per 5 minutes (owner, same rule as the CCTV system)
Options:

1. One event per tracker id (before: the IOU tracker loses a face on a head turn, so one visit gave several events seconds apart)
2. The CCTV system's rule: skip a record when the same person has one in the 5 minutes before it (`SAME_CAM_TIME_ENTRY_THRESHOLD`, `insert_recognition_data`)
3. Merge repeats in the website only

Chosen: 2 (owner: "there is a 5-minute rule, apply same here"). `repeat` in `facepipe.cpp`: a name logged in the last 300 s writes nothing; someone who stays in view is logged once every 5 minutes. Unknown faces have no name, so one counts as a repeat of an unknown logged in the window whose embedding it would match under the active metric (the CCTV system saves no unknowns). The window is rebuilt from today's events file at start; unknowns are not (the file keeps no embeddings). The key is the name alone while there is one camera; with two cameras it becomes name and camera, as in the CCTV system.
Why: the dashboard and the disk filled with copies of one visit; 3 rejects it at the source too late (snapshots and clips already written).
Trade-off: a second visit within 5 minutes is not logged; the first event's time and score stand, not the best one.
Revisit if: attendance needs exact entry and exit times.

## D-039: Two cameras and video uploads in one process (council)
Owner's request: two RTSP cameras at once and video upload, in one pull request. Owner's answers: the risk that a malformed video crashes recognition once (about 20 s restart) is accepted; uploads are kept 7 days.
Measured first (2026-11-07): a second full facepipe on the same stream used 3.35-3.45 of 3.96 GB and cut the live instance from 14.6 to 8.8 fps, so D-033's "separate --job process" failed its capacity test and its fallback applies.
Chosen, agreed with Codex in a council (6 exchanges). Model: Claude Opus 5.5 with Codex GPT-6.1 Sol (council).

- One facepipe, shared SCRFD and ArcFace engines. Two cameras: one `nvurisrcbin` each (TCP, native `rtsp-reconnect-interval` 10 s) into one live nvstreammux (1280x720 canvas with padding, batch 2). Stable camera IDs from `cameras.json` in `cameras.active` ("<uri> <w> <h> <id> <name>"; old one-line files still read) and a per-camera generation raised after a 3 s gap.
- Detection budget: at most one SCRFD run per batch; two cameras both due take turns (about 7.5 detections/s each). Crops before the tiler; `nvmultistreamtiler` 1 row x 2 columns at 1920x540, then OSD and the existing JPEG/H.264 outputs. A camera without frames for 3 s gets "<name>: offline" on its tile.
- Stale results: every job carries camera, generation and track ID (uploads: job number); a result applies only if that track is still pending under the same key.
- Evidence: a frame is saved only if its own batch was certified to show the decided label on a fresh frame of that camera (exact batch, checked as each JPEG arrives; the worker writes the file).
- Repeat rule (D-038) per person per camera, as in the CCTV system; unknown faces stay logged (the CCTV system saves none).
- Errors inside one camera's source are logged and left to nvurisrcbin; any other pipeline error still restarts the process. The watchdog needs both live camera frames and live output frames.
- Uploads: a second pipeline in the same process, one job at a time, own tracker, OSD and encoder; every frame kept. The SCRFD transaction (fill, run, decode) is locked; an upload is admitted only while every live camera is idle and no live detection waits, checked again under the lock; live recognition jobs first. Never touches attendance, evidence, clips or the repeat rule.
- Checked by `gst-discoverer-1.0` in a separate process before queueing (H.264/H.265, at most 1080p and 30 min); limits re-checked while decoding; 3 waiting, 4 GB cap, 2 GB free-space reserve enforced while files grow; cancel and delete; a job found `processing` at start is failed, never retried. Enrollment and uploads share an exclusive `flock` (`data/gpu-batch.lock`).

Rejected: uploads as a third source of the live mux (couples timing, end of stream and outputs to the cameras); per-camera encoders through nvstreamdemux (more memory and NVENC sessions for 2 cameras); evidence by a render counter at OSD or by "the first frame after the decision" (both can pick a frame without the label); purging tracks instead of generation tokens (a result in flight would label a new track); a HOLD-file handshake for enrollment (the lock is atomic and released by the kernel if a holder dies).
Trade-off: one shared process: a decoder crash on an uploaded file restarts live recognition once (owner accepted). Live view, clips and evidence show both cameras at half width. With faces at both cameras each gets about half the detection rate. Uploads wait as long as people are in view.
Revisit if: a third camera is wanted, uploads must finish during busy hours, or a camera needs its own full-size clips.

## D-040: User accounts with two roles (council; owner's choices)
Owner's request: add users from the admin portal, two types, one only for watching attendance (family); added by email, phone number or both. Owner's answers: viewers see everything an admin sees, read-only, including the live view, unknown faces, evidence, clips, and the System and Cameras pages without their controls (2026-11-07); set-password links use `FACEPIPE_PUBLIC_URL=https://attendance.hamzaaziz.dev`.
Chosen, agreed with Codex in a council (3 exchanges). Model: Claude Opus 5.5 with Codex GPT-6.1 Sol (council).

- Roles admin and viewer. The owner (`web_auth`, CLI) is a permanent admin the website cannot demote or delete, so no last-admin rule is needed.
- Accounts in `web.db`: opaque ID, name, role, email (trimmed, case-insensitive) and/or phone (`+` country code, at most 15 digits, no guessing), both unique and not the owner's user name, PBKDF2 like the owner, and an `auth_version` raised on every role or password change. No disable: deleting revokes; adding again makes a new ID.
- No email or SMS: the admin gets a single-use set-password link (128-bit, stored hashed, 48 h, one per account, separate from face-registration invitations) and sends it himself. Opening it only shows the form; submitting sets the password and uses the link up in one transaction; then the user signs in. A new link voids the old one and leaves the current password working until used. Links come from `FACEPIPE_PUBLIC_URL` (a plain https origin) or are shown as a path the page completes with its own address, never from the request's Host.
- Sessions `v2.<account>.<expiry>.<HMAC(purpose, account, version, password hash, expiry)>` for everyone, the owner included (version 0 with the `web_auth` hash); the role is read from the database on every request; database errors deny. Form tokens per account. Old v1 cookies end once.
- One `allowed()` rule on the exact path and method before any body or file is read: 401 without a session, 403 for a viewer on an admin route; the menu hides admin pages for viewers. Downloads and the live view check again every 2 MB or 3 s.
- Guessing: the per-visitor limit as before; per account, after 5 failures a pause doubling up to 30 s (no long lockout); at most 2 password hashes at once; the same work for unknown names. Changes on the Users page need the admin's current password. Passwords at least 15 characters (NIST SP 800-63-4), CLI included.

Rejected: putting the role in the cookie without a version (an old admin cookie would work again after demote and re-promote); a fixed 15-minute account lockout (anyone could keep a family member out); assuming +91 for 10-digit numbers (the repository must not depend on one owner's country); Host-based links (a request can name any host); automatic sign-in after setting a password; self-service password reset (no channel to verify a person).
Trade-off: viewers see strangers' faces too (owner's choice); sign-in names are not verified; a password alone protects internet access; repeated wrong passwords can still delay a person's sign-in by up to 30 s; plain HTTP on the home network.
Revisit if: viewers should see only some people, accounts need a second factor, or the site leaves the owner's family.

## D-041: RTSP for the local network only (owner)
Owner, 2026-11-07, after opening the RTSP stream from the internet: "user can open it on VLC but only if the user is in local network not via the internet"; from the internet the live picture is for the website only.
Chosen: the RTSP server refuses (403) every request from an address that is not private, link-local or loopback; the RTP input listens on `127.0.0.1`; the Live page shows the RTSP address only to local visitors. The website's MJPEG view (signed in, sessions checked every 3 s) is the only way to watch from the internet.
Why: owner's decision on exposure. Binding to the local interface alone would not stop a router port forward (it delivers to the local address); checking the sender's address does.
Trade-off: a viewer on a VPN into the home network counts as local; the RTSP login still travels in plain text on the local network.
Revisit if: an NVR outside the home must record the stream.
