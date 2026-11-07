# Feature: video outputs and evidence

Added 2026-11-06 at the owner's request ("live inference video, use NVENC"; "record only around events"; "full-frame evidence").

- **Overlay:** nvdsosd draws each face's box and label: green name and score, red Unknown, amber while recognizing.
- **Live view (NVJPG):** the dashboard's live panel streams `/live.mjpg`. The web server touches `/dev/shm/facepipe/want`; facepipe writes `live.jpg` only while that flag is fresh (under 10 s), so nothing is written when no one watches.
- **RTSP (NVENC):** H.264 at 4 Mbps, keyframe every 15 frames, served at `rtsp://<device>:8554/live` with Basic auth from `rtsp_auth`, to viewers on the local network only (D-041; others get 403). Without that file the RTSP server does not start.
- **Event clips (NVENC):** a ring buffer keeps about 3 s of encoded video from a keyframe; an event starts an MP4 clip from the ring and continues 5 s past the last event. Clips go to `$FACEPIPE_REC/YYYY-MM-DD/HHMMSS_clip.mp4`.
- **Evidence frame (NVJPG):** the worker waits for the next annotated frame (name drawn) and saves it as `HHMMSS_track.jpg` next to the clip.
- **Rotation:** at 90% full the oldest week of day folders is deleted, never today.

Status: overlay, live frames and encoders verified running; clip, evidence and RTSP playback not yet seen end to end (see ASSUMPTIONS.md).
