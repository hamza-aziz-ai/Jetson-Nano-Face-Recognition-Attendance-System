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
Trade-off: More ID switches in crowds or occlusion, which cost extra recognitions, not wrong ones.
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
