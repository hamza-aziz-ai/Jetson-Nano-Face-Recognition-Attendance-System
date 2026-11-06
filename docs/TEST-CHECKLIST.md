# Test checklist

A change is done when CI is green and the relevant device checks pass, with the real output recorded here or in the commit.

## CI (GitHub Actions, every push and pull request)

| Job | What it runs | Pass looks like |
| --- | --- | --- |
| Secrets, IPs and biometric data | `sh ci/scan.sh` | `scan ok: N tracked files, no secrets, IPs or biometric data` |
| Docs updated with code | Code changes must touch `docs/` or `README.md` | `docs check ok` |
| Web self-test on Python 3.6 | `ci/python-checks.sh` in `python:3.6-slim` | `selftest ok`, `python checks ok` |
| Mermaid diagrams render | `ci/mermaid-check.sh`: every Mermaid block in tracked Markdown rendered with mermaid-cli 12.0.0 | `mermaid ok: N diagrams rendered` |
| Compile against JetPack 4.6 headers | `ci/compile-check.sh` in `ubuntu:18.04`: `make check` (both binaries, `-Werror`), cppcheck | `compile ok`, `cppcheck ok` |

Local run of the same Python job (2026-11-06): `Python 3.6.15`, `selftest ok`, `python checks ok`. `ci/scan.sh` negative test: a planted `rtsp_auth` file and a 192.168 address both failed the scan (exit 1).

## Release (on a `vX.Y.Z` tag)

The Release workflow reruns all CI jobs, checks tag = `VERSION` and a `CHANGELOG.md` section, then publishes the GitHub Release. See RELEASING.md.

## Device checks

| Check | Command | Pass looks like |
| --- | --- | --- |
| Build | `make all` | No errors; `facepipe`, `facepipe-enroll`, `tracker.yml` exist |
| Web self-test | `python3 web.py --selftest` | `selftest ok` |
| Watchdog | `FACEPIPE_WATCHDOG=10 ./facepipe usb:/dev/video99 640 480` | `watchdog: no frames for 10 s, exiting` (seen 2026-11-06) |
| Match rule | `./facepipe --selftest` | `selftest ok` (cosine and L2 thresholds, same closest photo for both) |
| Version | `./facepipe --version` | the `VERSION` file's content; same in `data/status.json` and the dashboard footer |
| Enrollment | `./facepipe-enroll gallery` | `enrolled N photos of M people ... (0 skipped)` |
| Live run | `FACEPIPE_DIR=. timeout -s INT 60 ./facepipe <source> 1920 1080` | `[facepipe] fps` near 15 with `FACEPIPE_DROP=2` on a 30 fps camera; known faces counted |
| Embeddings vs reference | `python -I check_vs_rest.py <photos> <dir with faces.jsonl>` | Matching photos at cosine 0.96 or more |
| Services | `systemctl is-active facepipe facepipe-web` | `active` twice |
| Login | Request `/` without credentials | HTTP 401 |

## Recorded results (2026-11-06, Jetson Nano 4 GB, MAXN)

```
1080p H.264 file, detector every frame:      [facepipe] fps 19.2 frames 1443
RTSP camera, FACEPIPE_DROP=2:                [facepipe] fps 15.0
RTSP camera, all video outputs on:           [facepipe] fps 15.2
USB C270 1280x720 MJPEG (NVJPG decode):      [facepipe] fps 10.6-11.2, known 1 (try 2, cosine 0.504)
Enrollment, 10 photos:                       enrolled 10 photos of 1 people (0 skipped), real 0m10.130s
Embeddings vs InsightFace-REST, same images: 0.9705-0.9848 on all 10 photos
Live match of the enrolled owner:            cosine 0.757 (first try), 0.68-0.73 in a later run
TensorRT FP16:                               SCRFD 640x384 51.2 ms mean, w600k_r50 batch 1 41.2 ms mean
Web login:                                   no login 401, wrong login 401, path trick 401
On the Nano, build of main (system OpenCV):  facepipe --version 0.9.0; facepipe --selftest: selftest ok; web.py --selftest: selftest ok
CSI HQ Camera (IMX477) 1080p:                [facepipe] fps 15.0 frames 509; as the service with all outputs: fps 12.8, known 6, unknown 1
CSI recognition (events):                    hamza_aziz 0.693 (l2 0.783), 0.615 (l2 0.877); unknown 0.375 (l2 1.118)
Event clip (real event):                     h264 1920x1080, duration 8.785 s, 133 frames; evidence JPEG 174,571 bytes
Evidence label after the fix:                evidence JPEG shows green "hamza aziz 0.62"
RTSP in VLC:                                 rtsp://<nano>:8554/live plays with the RTSP login (owner, 2026-11-06)
```

v1.0.3 checks on the Nano (2026-11-06):

| Check | Command | Result |
| --- | --- | --- |
| Self-tests (log line format, 12-hour clock, daily backup and month folders) | `./facepipe --selftest && ./facepipe-enroll --selftest && python3 web.py --selftest` | ok |
| Log file | test instance on `sample_1080p_h264.mp4` | `logs/2026/November/facepipe.log`, lines like `[06-11-2026] 04:00:59.516 PM, Friday [INFO] fps 6.6 frames 342 ...` |
| Clean stop | `kill -TERM` on the test instance | `stopping (signal)` then `stopped`, process gone in 2.7 s |

v1.1.0 checks on the Nano (2026-11-06):

| Check | Command | Result |
| --- | --- | --- |
| Self-tests (detection policy: every frame within 10 s of a face, every 6th after, back at once, off with 0) | `./facepipe --selftest` | ok (counts collected outside `assert`, so cppcheck sees no side effects in it) |
| Same results as nvinfer | gallery photos as a 17 s 1080p video, run through v1.0.3 and v1.1.0 (`FACEPIPE_IDLE_AFTER=0`), stdout JSON compared | same frame, same box [786, 266, 1172, 744], embedding cosine 1.0000, both `hamza_aziz` 0.979 |
| Empty scene, CSI camera | live service, `tegrastats`, `status.json` | 15.0 fps, `det_fps` 2.4-2.6, GPU 0% in the samples, CPU about 20% per core |
