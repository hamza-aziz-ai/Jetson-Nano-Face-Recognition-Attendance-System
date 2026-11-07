# Feature: Video upload

Added 2026-11-07 at the owner's request (v1.4 of the D-033 plan; built with two cameras in one release, D-039).

- **Page:** Video upload in the sidebar. Choose a video; a progress bar shows the upload; the device then checks it with `gst-discoverer-1.0` in a separate process: H.264 or H.265, at most 1920x1080 and 30 minutes. Up to 500 MB on the home network, 95 MB through the Cloudflare Tunnel (its free plan refuses larger request bodies).
- **Processing:** facepipe runs the video through its own pipeline in the same process (shared detector and recognizer; a second process does not fit in memory). Live cameras always go first: a video is processed only while every live camera has been idle for 10 s; the list says "waiting" meanwhile. Every frame is detected and each face's name is drawn as soon as it is recognized.
- **Result:** Download the annotated MP4 (`<name>-recognized.mp4`); the list shows the names found and how many unknown faces. Uploaded videos never count as attendance and leave no events, snapshots, clips or evidence.
- **Limits:** 3 videos waiting at once; inputs and results under 4 GB together; 2 GB of the disk kept free, checked while files grow; processing longer than 3 x the video's length (not counting waiting) is stopped.
- **Cancel and delete:** Cancel stops a waiting or running video within about a second; Delete removes a finished one. Everything is deleted 7 days after upload (owner); an upload that never finished arriving goes after an hour.
- **Safety:** a video found `processing` when facepipe starts was interrupted by a stop or a crash; it is marked failed and never retried, so a file that crashes the process crashes it only once (owner accepted this risk). Enrollment and video processing never run at the same time.
- **Storage:** `data/uploads/<id>/`: `input`, `meta.json`, `state`, `reason`, `progress.json`, `result.mp4`, `result.json`.

Proof: web self-test (discoverer verdicts, size limits, a broken upload leaves nothing, retention, guarded routes, cancel and delete in a temporary folder); facepipe self-test; on the Nano: a 9 s clip uploaded through a test website and processed in 18 s beside a live USB camera that stayed at 15 fps (memory peak 2.46 GB), download named `my_clip-recognized.mp4`.
