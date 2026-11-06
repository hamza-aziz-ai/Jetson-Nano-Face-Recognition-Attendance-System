# Data

All data lives on the device. None of it is in the repository.

| Data | Where | Format | Contains | Kept |
| --- | --- | --- | --- | --- |
| Enrollment photos | `gallery/<name>/*.jpg\|png` | Images | Faces of enrolled people; `profile.jpg` is also the picture shown on the website | Until removed by hand |
| Wanted service state | `data/desired` | `running` or `stopped` | Whether recognition was stopped on the website | Until changed on the website |
| Web log | `logs/<year>/<Month>/web.log` | Text lines | Failed logins and refused requests with the client address, service actions | Kept (no retention rule set by the owner) |
| Pending registrations | `data/registrations/<id>/n.jpg` (0700) | JPEG, re-encoded, at most 1600 px | Faces of people who registered | Rejected: deleted at once; unreviewed: 7 days; approved: moved into `gallery/` |
| Registration records | `data/web.db` | sqlite3 | Invitation hashes, names, consent version and time, status | Kept (no retention rule set) |
| Display names | `data/people.json` | JSON | Name for each opaque person key | Until the person is removed |
| Camera setup | `data/cameras.json`, `data/cameras.active` (0600), `*.prev` | JSON, one text line | Camera choice, RTSP addresses with logins | Until changed on the Cameras page |
| Tunnel token | `/etc/cloudflared/facepipe-tunnel.env` (root, 0600) | `TUNNEL_TOKEN=` | The secret that lets cloudflared serve the site's hostname | Until the owner replaces or deletes the tunnel |
| Face matching | `data/matching.conf` (0600) | `metric=`, `cosine=`, `l2=` | The match rule chosen on the System page | Until changed |
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
