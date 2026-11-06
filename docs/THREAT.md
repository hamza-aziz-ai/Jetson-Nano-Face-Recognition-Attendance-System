# Threat model

The system holds biometric data (face photos, embeddings, snapshots, clips) of real people. In India this falls under the DPDP Act 2023. Gaps are listed plainly.

| Actor | Entry point | Worst realistic abuse | In place | Gap |
| --- | --- | --- | --- | --- |
| Someone on the LAN | Web page, port 8000 | View faces and attendance | Login (PBKDF2 hash), CSP `script-src 'self'` with no inline code, X-Frame-Options, nosniff; 10 failed logins in 15 min lock the client out without hashing | Plain HTTP: the password and pages travel in clear |
| A web page in the admin's browser | POST to the service controls | Stop recognition (cross-site request) | Per-process CSRF token in each form and a same-origin check on `Origin`/`Referer`; POST only; actions from a whitelist | None known |
| Someone with the web login | System page | Stop recognition, read recent log lines | The login itself; sudoers allows only start, stop and restart of `facepipe`; the web cannot stop itself; every action is logged | A stopped service records no attendance until started again (shown on every page) |
| Many clients at once | Web server | Exhaust memory or threads | At most 24 requests and 4 live viewers at a time; 30 s receive timeout; 4 KB request bodies; streamed downloads | None known |
| Someone on the LAN | RTSP, port 8554 | Watch the live camera | Separate login; server off without it | RTSP Basic auth is plain text on the network |
| Someone on the LAN | Path tricks in URLs | Read secrets or other files | Strict regexes for snapshot, evidence and photo paths; photos only for enrolled names | None known |
| Someone who can enroll | Gallery folder names | Script injection on the page | Names are HTML-escaped; tabs and newlines in names are skipped | Filesystem access to the device is already full control |
| Lookalike or photo of a person | Camera | False attendance | Match threshold 0.5 | No liveness check; threshold not tuned |
| Physical access | microSD card | Copy all faces and data | None | No disk encryption |
| Code contributor | Public repository | Ship malicious code | CI on every pull request; workflows from outside contributors need the maintainer's approval; the self-hosted runner only runs the Deploy workflow for published releases and manual dispatch, in the `production` environment | A pull request could edit a workflow to target the runner; the approval setting is the control, so never approve unreviewed workflow changes |
| Self-hosted runner | The Nano | Runs released code with the runner user's rights | Dedicated user with sudo only for restarting the two services | Not yet registered |
| The repository itself | GitHub | Leak secrets or faces | `.gitignore`; every push is scanned for IPs and secrets | Relies on the scan being run |

Data subject rights: to remove a person, delete their gallery folder, re-run enrollment, and delete their lines and snapshots from `data/` (see DATA.md).
