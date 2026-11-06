# Test checklist

A change is done when the relevant checks pass on the device and the real output is recorded here or in the commit. There is no CI: everything below runs on the Nano.

## Checks

| Check | Command | Pass looks like |
| --- | --- | --- |
| Build | `make all` | No errors; `facepipe`, `facepipe-enroll`, `tracker.yml` exist |
| Web self-test | `python3 web.py --selftest` | `selftest ok` |
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
```
