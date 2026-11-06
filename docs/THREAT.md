# Threat model

The system holds biometric data (face photos, embeddings, snapshots, clips) of real people. In India this falls under the DPDP Act 2023. Gaps are listed plainly.

| Actor | Entry point | Worst realistic abuse | In place | Gap |
| --- | --- | --- | --- | --- |
| Someone on the LAN | Web page, port 8000 | View faces and attendance | Login (PBKDF2 hash), CSP with nonces, X-Frame-Options, nosniff | Plain HTTP: the password and pages travel in clear; no lockout after failed logins |
| Someone on the LAN | RTSP, port 8554 | Watch the live camera | Separate login; server off without it | RTSP Basic auth is plain text on the network |
| Someone on the LAN | Path tricks in URLs | Read secrets or other files | Strict regexes for snapshot, evidence and photo paths; photos only for enrolled names | None known |
| Someone who can enroll | Gallery folder names | Script injection on the page | Names are HTML-escaped; tabs and newlines in names are skipped | Filesystem access to the device is already full control |
| Lookalike or photo of a person | Camera | False attendance | Match threshold 0.5 | No liveness check; threshold not tuned |
| Physical access | microSD card | Copy all faces and data | None | No disk encryption |
| Code contributor | Public repository | Ship malicious code | Owner deploys by hand | No CI checks yet |
| The repository itself | GitHub | Leak secrets or faces | `.gitignore`; every push is scanned for IPs and secrets | Relies on the scan being run |

Data subject rights: to remove a person, delete their gallery folder, re-run enrollment, and delete their lines and snapshots from `data/` (see DATA.md).
