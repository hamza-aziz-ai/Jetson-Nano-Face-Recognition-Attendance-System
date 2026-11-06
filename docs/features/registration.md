# Feature: registration by invitation, with approval

Added 2026-11-07 at the owner's request ("A registration portal where new people can register or existing ones can update manually if needed"). Design agreed with Codex in a council (D-033); the owner chose invitation links, the retention rules and where IP addresses are kept (D-034).

**Flow**

1. On the People page the admin presses **Invite someone** (or **New photos** next to an enrolled person). The site makes a random single-use link (128-bit token, valid 48 hours) and shows it once; only a SHA-256 hash of the token is stored.
2. The person opens the link on any phone or computer on the network, enters a name (not asked for a photo update), chooses 2-5 photos and accepts the consent statement. The browser shrinks each photo to at most 1600 px JPEG before upload.
3. The server checks the invitation is unused and unexpired, the browser's origin, size limits (4 MB per photo, 5 photos) and a rate limit (10 per device per hour). Each photo is decoded and re-encoded on the CPU by `facepipe-enroll --check` (no TensorRT, decoding bounded by `CV_IO_MAX_IMAGE_PIXELS`); anything that is not a real image of 200-8000 px per side is refused. The cleaned photos go to `data/registrations/<id>/` (mode 0700), never into `gallery/` and never to a public URL. The invitation is marked used in the same database step.
4. The People page lists the registration with its photos (admin only). **Reject** deletes the photos at once. **Approve** starts an enrollment job in the background.
5. The job builds a candidate gallery in `data/stage/<id>/` from hard links of `gallery/` plus the new photos, runs `facepipe-enroll` on it into a candidate `gallery.tsv`, and checks that the new person has at least one usable face and that no one already enrolled would be lost. Only then, under the update lock, it moves the photos into `gallery/<person>/` and replaces `data/gallery.tsv` by rename. Live recognition sees the new file (by inode, not by second-resolution time) and drops names cached on tracks. If anything fails, the active gallery is untouched and the registration shows the reason; it can be approved again or rejected.
6. New people get an opaque key (`p_` and 8 hex digits) used as folder and gallery name; their display name is in `data/people.json`. Existing names stay valid.

**Removal:** **Remove** next to an enrolled person enrolls a gallery without them, publishes it the same way, then deletes their photos. Past attendance records stay (removal from recognition is separate from erasing history, D-033).

**Retention (owner, D-034):** rejected photos are deleted at once; registrations never reviewed are deleted after 7 days; expired, unused invitations are dropped. The submitting device's IP address is not stored with a registration, only in the web log.

**State:** `data/web.db` (sqlite3: invitations by hash, registrations with name, consent version, time, status and note).

**Proof:** `python3 web.py --selftest` runs the whole flow against a real server with a stand-in for the enroll binary (CI has no GPU): invitation, public form, origin check, too few photos, missing consent, single use, admin-only photo URLs, approval into the gallery, rejection deleting photos, removal. On the Nano, with the real binaries against a copy of the gallery, beside the live pipeline: 3 photos passed `--check`, approval took 16.9 s from 1.3 GB free memory and enrolled 14 photos of 2 people, removal re-enrolled 11 photos of 1 person.
