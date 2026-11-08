# Project Memory: Read This First

Snapshot: **2026-11-08 8:41 AM IST**. Volatile state; verify before acting (see "Start here" in `CLAUDE.md`). Finished work lives in `CHANGELOG.md`, the reasoning in `docs/DECISIONS.md`, past sessions in `docs/HANDOVER.md`.

## Versions

- `main`: **v1.13.7**, deployed and healthy on the Nano (`facepipe 1.13.7 at 199 frames after 22 s`). Releases are made by the workflow when a merged change raises `VERSION`; the device's update timer (D-031) deploys each one within 10 minutes.
- This version: pages follow a start or stop of recognition without a manual refresh.
- Earlier in v1.13: token messages no longer assert the repository's visibility (.6); a deploy no longer leaves the source in `/tmp` (.5, `docs/bugs/deploy-left-source-in-tmp.md`); the health warning starts at 90 °C, above the 81 °C this board reaches while simply running (.4); each temperature sensor is judged against its own limits, AO is not critical until 110 °C (.3); the warning names heat or memory and what to do (.2); a private repository is read with a token from `gh_token` (.1, D-044); the website ships compiled as `webapp.so` and a missing file answers 404 (.0, D-043, `docs/bugs/favicon-missing-500.md`).
- Before that: open pages switch to the maintenance page by themselves (v1.12.3); USB cameras saved by their by-id path (v1.12.1); unknown faces only from a clear view (v1.12.0, D-042); the recognition banner (v1.11.0); RTSP on the local network only (v1.10.1, D-041); user accounts (v1.10.0, D-040); two cameras and video upload (v1.9.0, D-039); the sign-in page (v1.8.0, D-036).

## What runs on the device now

- Jetson Nano 4 GB, JetPack 4.6.6, app on the microSD at `~/facepipe`. Both services active; recognition at about 14.9 fps on the **USB camera** (selected on the Cameras page on 2026-11-08). Face matching L2 ≤ 0.90. The owner's phone over RTSP and the CSI camera are the other options; a USB camera is saved by its `/dev/v4l/by-id/...` path.
- If the selected camera is the phone over RTSP and the phone is not streaming, `facepipe` fails with `Could not open resource for reading and writing ... gst_rtspsrc_retrieve_sdp` and systemd restarts it every ~21 s (restart counter reached 171 on 2026-11-08). `systemctl is-active` still answers `active` between restarts, so check `data/status.json` freshness, not just the unit state.
- The website runs as `webapp.so` with a two-line `web.py` that starts it (D-043). There is **no source on the device**: no `.cpp`, no `.py` beyond that stub, no `__pycache__`, and `.prev` holds compiled files. A deploy compiles on the device, which needs Nuitka 4.3.4 and takes 5 m 40 s idle, about 7 m 30 s while recognition runs.
- Credentials: `web_auth`, `rtsp_auth` and `data/cameras.json` (all 0600), plus `gh_token` (0600, fine-grained, one repository, Contents: read) for updates while the repository is private. The owner's SSH key, the `gh` login and the `NVIDIA-SDK-Manager` key were removed on 2026-11-07; `authorized_keys` keeps two keys.
- Heat tracks the **detection duty cycle**, not uptime (measured 2026-11-08, 15 s samples over 31 min). Empty scene, detection every 6th frame: plateau at AO 55.2 °C / CPU 48.3 °C after about 20 min, from AO 44.0 / CPU 38.5 idle; the rise has two time constants, about 2.5 min die-to-heatsink then about 8 min heatsink-to-air. Faces present, detection every frame plus an ArcFace embedding per track: AO about 87 °C / CPU about 81 °C. Both are normal for this board (CPU throttles at 97 °C, AO critical 110 °C). `soctherm: OC ALARM` appears because it runs from a phone charger over micro-USB. The faces-present curve has not been sampled yet; it needs someone in front of the camera for 20 min.
- The installed systemd units predate `deploy/install.sh`; re-run the installer only with the owner's approval.

## Remote access (D-035, D-036, D-037)

- Tunnel `attendance.hamzaaziz.dev` is live (`cloudflared-facepipe`; token in `/etc/cloudflared/facepipe-tunnel.env`, never read or print it). Route: `http://localhost:8000`.
- Cloudflare Access was removed at the owner's request: the site's own sign-in page is the only lock, so the password must be strong.
- Maintenance page: Worker `facepipe-maintenance` on the page routes (`sh deploy/maintenance/deploy.sh attendance.hamzaaziz.dev`), re-deployed on 2026-11-07 to cover Uploads, Users and Account.
- The repository is **public at the moment**; the owner switches it private when they choose. Updates work either way: public needs no token, private uses `gh_token`.

## Next (owner)

1. **Hardware on the way, all expected by 10 November 2026:** a 5 V 4 A barrel supply with the J48 jumper, a fan, and a case. The supply fixes the `OC ALARM` entries and the clock drops, not the temperature; the fan is what lowers the temperature (40 mm 5 V on the J15 PWM header, then `sudo jetson_clocks --fan` or an `nvfancontrol` profile). The final two-camera numbers (D-039) wait for the supply. Assembling the case is also when the Wi-Fi radio can be turned on.
2. **Two cameras:** reconnect test, switching the phone stream off and on.
3. **RTSP from mobile data:** expect 403 (D-041); still untested.

Done on 2026-11-08: the SSH key `SHA256:liIG…6M0` ("Hamza-Jetson-Nano") and the `gh` OAuth token were revoked on GitHub (`gh ssh-key list` shows only the laptop key); the fine-grained token in `gh_token` was confirmed Contents: Read-only; the social preview image was uploaded.

## Still open

- **D-042 calibration:** the limits for a "clear view" of an unknown face need tuning with the owner in front of the camera.
- Self-hosted runner: the owner registers it (token never typed by an agent); restricted user and sudo rule need exact-command approval.
- MLOps model manifest; LFW evaluation (download needs owner permission); **licence choice — the InsightFace models are non-commercial, so a paid client install is not allowed as things stand.**
- Client installs: a client device needs its own token, not a copy of the owner's, and an image that never carried the SDK-Manager key.
- **`tracker.yml` is missing from a fresh install:** `.gitignore` line 12 ignores it, but `deploy/deploy.sh` line 54 copies it from the clone. Only a device that already holds the file survives a deploy — this Nano does, since 2026-11-06. A clean client install leaves `nvtracker` with `ll-config-file=<app_dir>/tracker.yml` pointing at nothing. The file is NVIDIA's `config_tracker_IOU.yml` verbatim (MIT header), so committing it with the notice kept is allowed. Code change: needs a branch and a pull request.
- **No `LICENSE` file:** the public repository is all rights reserved by default. The CCTV session asked for this pipeline's code on 2026-11-08 and was told to put the question to the owner before copying any of it.
