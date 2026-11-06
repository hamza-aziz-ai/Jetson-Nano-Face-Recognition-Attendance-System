# Flow of one face

1. The camera frame is decoded (NVDEC, NVJPG or the ISP). Only every Nth frame continues (`FACEPIPE_DROP`, default 2).
2. nvstreammux batches it; nvvideoconvert makes RGBA in GPU memory.
3. nvinfer runs SCRFD at 640x384 (letterboxed, padded right and bottom) and attaches the 9 raw output tensors.
4. The detection probe decodes scores, boxes and landmarks at strides 8, 16 and 32, keeps scores of 0.6 or more, applies NMS at IoU 0.4, scales back to frame size, adds one object per face with its landmarks packed into `misc_obj_info`, and sets `bInferDone` so the tracker accepts them.
5. nvtracker (IOU) gives each face a track ID; a track is reported after a short probation.
6. The recognition probe draws the label (amber while recognizing) and, if the track needs an attempt (not done, nothing pending, face at least 40 px wide, 8 frames since the last try), maps the frame, aligns the face to 112x112 with a Umeyama similarity transform to the ArcFace template, and queues it.
7. The worker runs ArcFace (RGB, x/127.5-1), L2-normalizes the 512-d embedding and finds the best cosine match in the gallery, which it reloads when `gallery.tsv` changes.
8. A match at 0.5 or more ends the track as known. Below that the track is retried, up to 5 tries, then ends as unknown.
9. On an ended track the worker starts or extends an event clip (3 s before, 5 s after), saves the next annotated frame with the name drawn as full-frame evidence, writes a 112x112 face snapshot and appends one JSON line to `events-YYYY-MM-DD.jsonl`.
10. The label turns green with the name and score, or red Unknown, on the live view and the RTSP stream.
11. The web page reads the day's events: first and last seen per person, sightings, best score, evidence links, absent list from the gallery.
