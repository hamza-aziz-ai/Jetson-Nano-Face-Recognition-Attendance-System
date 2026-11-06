# Data

All data lives on the device. None of it is in the repository.

| Data | Where | Format | Contains | Kept |
| --- | --- | --- | --- | --- |
| Enrollment photos | `gallery/<name>/*.jpg\|png` | Images | Faces of enrolled people | Until removed by hand |
| Wanted service state | `data/desired` | `running` or `stopped` | Whether recognition was stopped on the website | Until changed on the website |
| Web log | `logs/<year>/<Month>/web.log` | Text lines | Failed logins and refused requests with the client address, service actions | Kept (no retention rule set by the owner) |
| Gallery | `data/gallery.tsv` | One line per photo: name TAB 512 floats | Face embeddings | Rebuilt by `facepipe-enroll` |
| Attendance events | `data/events-YYYY-MM-DD.jsonl` | JSON lines: time, ts, track, name, score (cosine), metric, l2, det, snap, frame, clip | Who was seen when | Forever (small) |
| Face snapshots | `data/snaps/YYYY-MM-DD/HHMMSS_track.bmp` (older: .jpg) | 112x112 aligned faces | Faces of everyone seen, including unknowns | Forever (about 37 KB each) |
| Evidence frames | `/mnt/rec/YYYY-MM-DD/HHMMSS_track.jpg` | 1080p JPEG with box and name | Whole scene | Until rotation |
| Event clips | `/mnt/rec/YYYY-MM-DD/HHMMSS_clip.mp4` | H.264 MP4, about 8 s | Whole scene | Until rotation (oldest week deleted at 90% full) |
| Live frame | `/dev/shm/facepipe/live.jpg` | JPEG | Current camera view | RAM, overwritten |
| Status | `data/status.json` | JSON | fps and counters | Overwritten every 5 s |
| Logins | `web_auth` (PBKDF2 hash), `rtsp_auth` (plain, 0600) | Text | Credentials | Until changed |

## Removing a person

1. Delete `gallery/<name>/` and run `./facepipe-enroll gallery` (the service reloads the gallery).
2. Delete their lines from `data/events-*.jsonl` and the snapshots those lines name.
3. Delete matching evidence frames and clips under `/mnt/rec`.
