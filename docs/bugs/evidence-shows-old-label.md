# Evidence image showed "Recognizing..." instead of the name

Date: 2026-11-06. Status: fixed and verified (an evidence image at 20:10:50 shows the green name label with score 0.62).

**Symptom:** The first real full-frame evidence image (event at 17:29:03, recognized at cosine 0.659) showed the amber "Recognizing..." label, not the name.

**Cause:** The worker waited for 2 more JPEGs after the decision, but frames already past the labelling probe (queues, conversion, NVJPG) still carried the old label.

**Fix:** The labelling probe records each frame's timestamp under the same lock the worker uses to mark a track done; the worker reads that timestamp at the decision and saves the first JPEG with a later timestamp (`jpeg_after`, up to 1.5 s).

**Also verified on that event:** the clip is valid H.264 1920x1080, 8.8 s, 133 frames (3 s before plus 5 s after at 15 fps); the evidence JPEG is a valid 1080p image.
