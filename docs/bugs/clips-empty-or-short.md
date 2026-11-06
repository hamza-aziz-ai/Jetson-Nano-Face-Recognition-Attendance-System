# Event clips came out empty or short

Date: 2026-11-06. Status: fixed in v1.1.2. Found by: the owner (empty clips for unknown faces), then a clip-length check on the CSI camera.

**Symptom:** Some `*_clip.mp4` files were 0 bytes (five on 2026-11-06, known and unknown events alike), and clips that did play were 5.4-6.2 s long instead of about 8 s, missing most of the 3 s before the event. Nothing was logged.

**Investigation:** v1.0.3 added a log line for how each clip ends ("saved", an error, or not finalized), and this fix one for the pre-roll held in memory. On a test video the memory buffer held 3.2 s (98 frames) at the event, yet the saved clip had 4 frames (0.23 s). `gst_app_src_push_buffer` returned OK for every frame; `GST_DEBUG=h264parse:3` showed `broken/invalid nal Type: 1 Slice ... will be dropped` for each of them.

**Cause:** `nvv4l2h264enc` was set to an I-frame every 15 frames (`iframeinterval=15`) but kept its default IDR interval of 256 frames. SPS/PPS come only with an IDR. A clip starts at the newest keyframe in the buffer, which was usually a plain I-frame, so the clip's parser had no SPS/PPS and dropped every frame until the next IDR, up to 17 s away. No IDR inside the clip gave an empty file; a late one gave a short clip. The RTSP camera's 8.8 s clip in the earlier test happened to contain an IDR near its start.

**Fix:** `idrinterval=15`, so every keyframe is an IDR with SPS/PPS in front (1 s apart). The clip writer was also made a plain file writer (`appsrc is-live=false`, `filesink sync=false async=false`); that alone did not fix it.

**Proof:** the same test video: clip 8.2 s, 2.6 MB, 3.2 s before the event, 0 parser warnings (before: 0.23 s, 7.6 KB, one warning per frame).

**Side effect:** keyframes that are IDRs let RTSP viewers start within 1 s; the bitrate setting is unchanged.

**Follow-up (v1.6.1):** two events on 2026-11-07, recognized about 3 s after the owner switched cameras (a restart), had an evidence frame but `"clip": ""`. A new log line showed "the video encoder has not started yet": recognition was faster than the encoder's first frame. The event now reserves the clip name and the clip opens at the first keyframe. Test: a video that starts with a face gave "waiting for the video encoder's first keyframe", then a 5.4 s clip linked to the event.
