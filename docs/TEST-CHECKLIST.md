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
| Two cameras | two lines in `cameras.active`, run facepipe 2 min | `cameras chosen on the website: A and B`; status.json `cameras` both `up`, about 14.4 fps each (seen 2026-11-07 with two connections to one phone stream; 2.3 GB RAM in use) |
| One camera broken at start | second line `rtsp://127.0.0.1:1/none` | first camera about 14.8 fps, second `up: false`, no exit (seen 2026-11-07) |
| Video upload end to end | upload a short H.264 clip on the Video upload page | state `done`, download `<name>-recognized.mp4`; live fps unchanged (seen 2026-11-07: 9 s clip in 18 s, live USB camera 15 fps) |
| Users and roles | `python3 web.py --selftest` | `selftest ok` (D-040 cases: links, sign-in names, viewer 403 matrix, revocation, cooldown, closed on database failure; seen 2026-11-07 on the Nano) |
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

v1.1.2 check on the Nano (2026-11-06):

| Check | Command | Result |
| --- | --- | --- |
| Clip pre-roll | 26 s test video (8 s empty, 10 s face, 8 s empty) through a test instance with `GST_DEBUG=h264parse:2` | before: buffer 3.2 s, clip 0.23 s, parser drops every frame; after `idrinterval=15`: clip 8.2 s, 2.6 MB, 3.2 s pre-roll, 0 parser warnings |

v1.2.0 checks on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Web self-test on Python 3.6 | `python3 web.py --selftest` | ok: login and failed login, 5 pages with CSP, escaped hostile name, Range 206, POST refused without Origin, wrong token, other site, unknown action, 4 KB+ body |
| Pages with real data | second instance on port 8001 with a temporary test login | Overview, Live, Attendance, People, System all 200; 1 person; camera shown as "CSI camera, sensor 0" |
| Service control | Restart button on the test instance | 303 to `/system?msg=restarted`; facepipe log: stopping, stopped (3 s), started; web log: "service restart from the website; now active" |
| Sudoers rule | `sudo visudo -cf` then install | parsed OK; `/etc/sudoers.d/facepipe-web` mode 0440 |

v1.2.1 check (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Browser-like POST | `python3 web.py --selftest` on the Nano | `Referrer-Policy: same-origin` sent; `Origin: null` refused like other foreign origins; same-origin post with the token passes the guard |
| Buttons, dialog, pause in a browser | owner, after the update deploys | to be confirmed by the owner (v1.2.0 failed this in the owner's browser) |

v1.2.2 checks on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Self-test keeps the real log clean | line count of `logs/2026/November/web.log` around `python3 web.py --selftest` | selftest ok; 0 lines added |
| Profile picture | `first_photo("hamza_aziz")` with the real gallery | `gallery/hamza_aziz/profile.jpg` |
| Stop and Start from the website (v1.2.1) | owner's browser | Stop: facepipe `inactive (dead)`, clean stop in 0.18 s, wanted state `stopped`; Start: active again, 14.8 fps |

v1.3.0 checks on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Photo check (CPU) | `CV_IO_MAX_IMAGE_PIXELS=40000000 ./facepipe-enroll --check <file> out.jpg` | text file: "not a readable image", exit 2; profile photo: 512x512, exit 0 |
| Registration flow, self-test | `python3 web.py --selftest` (stand-in enroll binary) | ok: invitation, form, origin, too few photos, no consent, single use, admin-only photos, approve, reject deletes photos, remove |
| Real approval beside live | real binaries, copy of the gallery, 3 of the owner's photos as "Test Person" | `--check` passed 3 photos; approve 16.9 s from 1321 MB free; "enrolled 14 photos of 2 people"; remove: "enrolled 11 photos of 1 people" |

v1.4.0 checks on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Cameras self-test | `python3 web.py --selftest` | ok: add with login (0600 file, password not on the page), enable, disable, bad address, "enable one first", remove |
| Camera from the file | test instance with `FACEPIPE_CAMERAS` and no camera argument | file source: "camera chosen on the website, 1920x1080"; USB: 1280x720, 9.4 fps |
| RTSP test button | `probe_rtsp` | phone stream: `H.264 (High Profile)`; no camera at the address: "the stream could not be opened" |
| USB list | `video_devices()` | `/dev/video1 UVC Camera (046d:0825)`; CSI sensor (vi-output) left out |

v1.5.0 check (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Redesign in a browser | the site in a local Python 3.6 container with sample data, viewed in Chrome | Overview, People, Cameras, System and the registration page render with the sidebar, cards and forms; one fault found and fixed (the registration background repeated on short pages) |
| Web self-test | `python3 web.py --selftest` on the Nano | ok |

v1.6.0 checks on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Device health readings | `device_health()` on the Nano | AO 77, CPU 71, GPU 69, PLL 70, PMIC 50 °C; cores 34/31/17/21 %; CPU 1224 MHz; GPU 0 % at 921 MHz; fan 63 % (no rpm sensor); 1440 of 3963 MB available; swap 406 MB used; level ok |
| Health API and panel | `python3 web.py --selftest` | ok: 4 panel cards from `/api/health`, 401 without login |

v1.6.1 check on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Event right after start | test instance on a 1280x720 video that starts with a face | before: "event without a clip: the video encoder has not started yet", `"clip": ""`; after: "waiting for the video encoder's first keyframe", clip saved (2.6 MB, 5.4 s) and linked to the event |

v1.7.0 check on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Matching file | test instance with `metric=l2`, `l2=0.90` in `matching.conf` | startup log: "match L2 <= 0.90" |
| Out-of-range value | `python3 web.py --selftest` | L2 2.0 refused, nothing written; selftest ok |

v1.8.0 check on the Nano (2026-11-07):

| Check | Command | Result |
| --- | --- | --- |
| Sign-in, cookie, tampering, expiry, redirects, sign-out, new password, restart | `python3 web.py --selftest` | selftest ok |
| Real pages | test instance on port 8099 with a generated test login | `/` 303 to `/login?next=%2F`; `/login` 200; `/nope` 404 page; sign-in 303 to `/system` with `HttpOnly; SameSite=Lax`; `/system` 200 with Sign out and its form token |
