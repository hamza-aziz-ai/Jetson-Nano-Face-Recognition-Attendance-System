# Project Memory: Read This First

Snapshot: **2026-11-07 03:10 IST**. Volatile state; verify before acting (see "Start here" in `CLAUDE.md`).

## Versions

- `main`: v1.11.0 (recognition state banner; v1.10.1 RTSP local network only, D-041; v1.10.0 user accounts, D-040; v1.9.0 two cameras and video upload, D-039; v1.8.1 5-minute repeat rule, D-038; v1.8.0 sign-in page, D-036) released by the workflow; the device's update timer (D-031) deploys each release within 10 minutes. v1.7.2 fixed the camera label on the System page.
- Camera in use: the owner's phone over RTSP (chosen on the Cameras page); face matching L2 <= 0.90 (System page).

## Device

- Jetson Nano 4 GB, JetPack 4.6.6, app on the microSD, camera `csi:0` (Raspberry Pi HQ camera, IMX477, R8 removed). USB webcam on `/dev/video1`; a phone RTSP camera is also available.
- Measured with no face in view: GPU 89-99 %, CPU about 33 % per core, RAM 2.7 of 3.96 GB, 13-15 fps processed (every 2nd frame).
- The installed systemd units predate `deploy/install.sh`; re-run the installer only with the owner's approval.

## In progress

1. **v1.0.3 released and deployed (healthy after 14 s, 14 fps).** Contents: daily log files in the owner's layout (D-030), clean bounded SIGTERM shutdown and watchdog fix (D-029), deploy health on the current `MainPID` with advancing frames, clip finalization result logged, 12-hour times on the web page. Verified on the Nano in a test instance: self-tests, log file format, clean stop in 2.7 s. CSI stall cause found: a killed camera process wedges nvargus-daemon (`SCF: Error Timeout`, then `InvalidState` for every client) until the daemon is restarted (docs/bugs/csi-start-stall.md). Still to do: restart after 20 min and after hours of CSI capture.
2. **Empty/short clips: cause found and fixed in v1.1.2** (IDR only every 256 frames; idrinterval=15). PR fix/clip-preroll.
3. **v1.1.0: adaptive detection (D-028), this commit.** Own SCRFD probe replacing nvinfer; same results as nvinfer on identical frames; empty scene 15 fps, 2.4 detections/s, GPU about 0%. Still to measure with a face present (owner): fps and delivery gaps during recognition.

4. **v1.1.1: automatic deployment (D-031)**, owner chose a pull timer over the GitHub runner: `facepipe-update.timer` every 10 min runs `deploy/update.sh`. Units must be installed on the owner's Nano with approval of the exact files.
5. **Website plan agreed with Codex (D-033):** v1.2 site + service control (built in PR feat/website-v1.2; sudoers rule installed on the owner's Nano with approval), v1.3 registration with safe gallery publication, v1.4 uploads after a capacity test, v1.5 two cameras. First: empty-clip bug and active-face cadence (owner in front of the camera).
6. **PR practice (D-032):** code via branch + PR with auto-merge on the 5 CI checks; docs straight to main with [skip ci]; releases by the workflow when a merged change raises VERSION. PR #1 merged that way.

## Owner decisions 2026-11-07 (D-034)

- Next work, in this order: registration by invitation (v1.3, built in PR feat/registration), then the Cameras page (v1.4, built in PR feat/cameras), then (owner moved it forward) the website redesign v1.5.0; still to do: two RTSP cameras at once, then video upload.
- Registration: invitation link only (single use, 48 h), admin approval; rejected photos deleted at once, unreviewed after 7 days; client IP only in the web log.

## Redesign (owner, 2026-11-07), done in v1.5.0 (PR feat/web-redesign); the backlog below was addressed

- Registration page: the form uses half the card width; the file input is the browser's plain "Choose Files"; the consent checkbox and text look unaligned; no photo previews before sending.
- People page: cards were cramped (fixed in v1.4.1); generally tighten spacing, typography and button styles across pages.
- Footer says "no data leaves this device": must change if remote access through Cloudflare is added.

## Remote access (D-035, D-036, D-037), 2026-11-07

- Tunnel `attendance.hamzaaziz.dev` is live (cloudflared-facepipe enabled by the owner; token in `/etc/cloudflared/facepipe-tunnel.env`, never read or print it). Route: `http://localhost:8000`.
- Owner removed Cloudflare Access: the site's own sign-in page is the only lock. Owner told the password must be strong.
- Maintenance page: Worker `facepipe-maintenance` deployed with the owner's `wrangler login` on the page routes (`sh deploy/maintenance/deploy.sh attendance.hamzaaziz.dev`).

## Next (owner)

1. After v1.10.0 deploys: add `FACEPIPE_PUBLIC_URL=https://attendance.hamzaaziz.dev` to the Nano's `facepipe.env` (owner approved 2026-11-07) and restart facepipe-web; owner uploads the social preview PNG on GitHub (Settings, Social preview).
2. Reconnect test of two cameras: the owner switches the phone stream off and on (needs the owner); then the hardware gate of D-039 on a 5 V 4 A barrel supply.

## Still open

- Self-hosted runner: the owner registers it (token never typed by an agent); restricted user and sudo rule need exact-command approval.
- MLOps model manifest; LFW evaluation (download needs owner permission); license choice (InsightFace models are non-commercial).
- Power: the Nano runs from a phone charger over micro-USB (owner, 2026-11-07); `soctherm: OC ALARM` every few seconds and CPU clocks drop to 1224 MHz. Advised 5 V 4 A barrel jack with J48; final two-camera numbers only after that.
