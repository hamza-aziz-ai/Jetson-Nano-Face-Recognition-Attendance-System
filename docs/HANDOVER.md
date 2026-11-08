# HANDOVER

Read first: docs/ARCHITECTURE.md, docs/CONSTRAINTS.md, docs/DECISIONS.md. Newest entry on top.

## Measurement, handover and two open gaps: 2026-11-08 (model: Claude Opus 5)

No release. A day of measuring what was already shipped, and of handing the Orin to another project.

Done:

- **The thermal question answered properly.** Sampled every 15 s for 31 min: an empty scene plateaus at AO 55.2 °C / CPU 48.3 °C after about 20 min, from AO 44.0 / CPU 38.5 at idle, with two time constants (about 2.5 min die-to-heatsink, then about 8 min heatsink-to-air). Heat tracks the **detection duty cycle**, not uptime: with faces present the detector runs every frame instead of every sixth and adds an embedding per track, and the board sits at AO 87 / CPU 81 °C. Both are normal; the CPU throttles at 97 °C. `README.md` carries both rows now.
- **`D-045`**: the threat model stays public and the demo link keeps the sign-in page as its only lock. The dashboard address was published on the portfolio site; Cloudflare Access and a synthetic-face demo instance were both recommended and declined. Recorded as an accepted risk, with the controls that are actually in front of the data named.
- **The Orin was measured and handed over.** Engines built on the Orin from this repo's ONNX: SCRFD-10G 384x640 FP16 batch 1 at **3.47 ms** (Nano 55 ms) and ArcFace w600k_r50 FP16 at **1.69 ms per face from batch 8** (Nano 48 ms at batch 1). Per-face cost stops improving past batch 4, so batch 16 buys nothing on that board. The owner decided the CCTV session leads the Orin system in its own repository; this repo stays the JetPack 4 product, and the full design brief went across.
- **The Orin's CSI camera was half fixed** while helping that session. `/boot/extlinux/extlinux.conf` had no `OVERLAYS` line, so no sensor existed in the device tree and no `/dev/video` node could appear; `config-by-hardware.py -n 2="Camera IMX477 Dual"` plus a reboot gave `/dev/video2` as `vi-output, imx477 10-001a` on connector C, with Argus answering to `sensor-id=0`. **Capture still delivers zero frames**, and the cause is below the software: `nvargus-daemon` logs `SCF: Error 0x00000008: Timeout!! ... waitCsiFrameEnd()` and `(fusa) Error: Timeout [PIPELINE 0]`, and raw `v4l2-ctl --stream-mmap` blocks in every mode, which bypasses Argus and the ISP entirely. The sensor answers on I2C at probe (the driver binds, the empty connector on bus 9 fails with -121 as expected), so power and I2C are good and no MIPI frame start ever arrives. That diagnosis held: the owner moved the camera from CAM0 to CAM1 and raw `v4l2-ctl --stream-mmap` then delivered frames at both modes (10 buffers at 1080p, 5 at 4K), so the CSI data path is good and CAM0 or its cable is the fault. Argus could not be retested, because that same boot brought up a GPU that never initialised: `nvgpu ... ga10b_acr_patch_wpr_info_to_ucode: invalid mem acr_falcon2_sysmem_desc` then `ACR bootstrap failed` and `nvgpu_finalize_poweron: Failed initialization for: g->ops.acr.acr_construct_execute`. Every CUDA client then dies — `NvRmGpuLibOpen failed, error=4`, `Cuda failure: status=100`, SIGSEGV — including `trtexec`, `nvarguscamerasrc` and the other session's service. No package or firmware changed that day (apt untouched since 2026-11-07, `/lib/firmware/tegra23x` since August), so it is a transient secure-boot failure of the GPU firmware on that boot, and the remedy is a cold power cycle rather than a warm reboot, because the carveout is set up before the kernel runs.

Lessons worth keeping:

- A figure with no condition attached invites a wrong conclusion. "AO 87 °C while recognition runs" was true and still misleading, because it is the faces-present case and the same binary sits 33 °C lower in an empty room. Record the condition with the number.
- `systemctl is-active` says `active` between restarts of a crash-looping unit. When the phone's RTSP source is absent, `facepipe` dies and restarts every ~21 s (the counter reached 171) while both units still report `active`. Check `data/status.json` freshness, not unit state.
- `ci/scan.sh` reads a four-part package version that starts with ten as an address in the private class-A range and refuses the commit. TensorRT's Debian versions have exactly that shape, so write them with three parts in prose. The scan is right to be strict; the wording is what has to change. Writing the range itself in a document trips it too.
- Another session's report is a lead, not a finding. The p3767/p3768 camera overlays were on that Orin all along; the survey that said otherwise had looked at the p3737 (AGX) ones.
- That cuts both ways, and it cut me. I read a service's 16 fps and a `max_fps_dur` log line as proof that its CSI camera was streaming, and told the other session its capture failure was contention over the sensor. Both were wrong: the service was on its USB camera and that line comes from `nvstreammux`. A plausible story that explains the symptom is not evidence; the device node a process actually holds is. `fuser -v /dev/videoN` and the service's own camera list answer it in one command.

Next: remove `~/facepipe-orin-spike` from the Orin (191 MB of ONNX plus two engines) once the other session has taken what it wants; commit `tracker.yml` so a fresh install has one; sample the faces-present thermal curve, which needs someone in front of the camera and so pairs with the D-042 calibration; the licence decision, and whether the AI co-author trailers in the history stay.

Broken:

- A fresh install has no `tracker.yml`: `.gitignore` ignores it while `deploy/deploy.sh` copies it from the clone, so `nvtracker` would start with `ll-config-file` pointing at a missing file. Devices that already hold the file, including this Nano, are unaffected. Fix is to commit NVIDIA's `config_tracker_IOU.yml` (MIT, notice kept) through a pull request.

## Releases v1.12.3 to v1.13.7: 2026-11-07 (model: Claude Opus 5.5)

The day the system was prepared to be installed somewhere other than the owner's desk, after the owner asked: "What if I need to deploy this at a client machine. Currently it uses bare Python files, right?"

Done:

- **The website ships compiled** (D-043). `deploy/compile-web.sh` runs Nuitka on every deploy, before the self-tests, so the tests exercise what ships: `web.py` becomes `webapp.so` and a two-line starter. The commands, the systemd unit and CI are unchanged because the stub keeps the name. Nuitka 4.3.4 supports Python 3.4-3.14, so the Nano's 3.6.9 is in range; the compile takes 5 m 40 s idle and about 7 m 30 s while recognition runs.
- **A private repository is read with a token** (D-044) from `<app_dir>/gh_token`, passed through the environment, never in a command line or a URL. Without it the scripts behave as before and say what is missing.
- **Three bugs found by doing the above**, each written up: a static file that no release ever shipped, where the server dropped the connection instead of answering 404 (`bugs/favicon-missing-500.md`); a deploy that left the whole source in `/tmp`, five copies, because `mktemp -d` had no `trap` (`bugs/deploy-left-source-in-tmp.md`); and the health warning, which compared every sensor with the CPU's limits and so warned about AO at 85 °C, then warned at 80 °C, which is where this board sits while simply running.
- **Pages follow the service.** Starting recognition left every page showing "Recognition is NOT RUNNING" until a manual refresh, because only the corner pill was updated by the status poll. Each page now carries the state it was drawn with and reloads when the real state differs.
- **The device was emptied of what a client should not have**: `facepipe.cpp`, the `Makefile`, a `.pyc` of the website, source copies under `.prev`, the owner's SSH key, the `gh` login with `repo` and `admin:public_key` scopes, and the `NVIDIA-SDK-Manager` key that flashing leaves in `authorized_keys` (removed from the Orin too).

Lessons worth keeping:

- Compiling one file protects nothing if the build drops the rest beside it. "No readable source" is a property of the whole device: check `/tmp`, `__pycache__` and rollback copies before saying a box is clean.
- Testing the package instead of the checkout is what found the missing favicon; the self-test had been green for three versions because it ran inside the source tree, where the file exists.
- A warning that fires during normal operation is one nobody reads. Both health thresholds were wrong in that direction.
- Editing `authorized_keys` is the one change that can lock everyone out, and each command opens a new connection: check which key actually authenticates in `/var/log/auth.log`, filter by fingerprint, assert the live key survives before writing, then test a fresh connection.

Next: the 5 V 4 A barrel supply (expected 10 November) and then the two-camera numbers; D-042 calibration with the owner in front of the camera; the licence decision, since the InsightFace models are non-commercial and a paid client install is not allowed as things stand.

## Release v1.0.0: 2026-11-06 (model: Claude Opus 5.5)

Done: every README validation item verified on the Nano (RTSP, USB, CSI cameras; clips and evidence; RTSP in VLC). v1.0.0 released.
Next: register the self-hosted runner for automated deploys and release assets; MLOps model manifest; LFW evaluation (download needs owner permission); open items in ASSUMPTIONS.md (threshold tuning, rotation, power).

## Update: 2026-11-06 late night (model: Claude Opus 5.5)

Done: matching metric toggle (D-025); release assets and Deploy to Nano workflow with automatic rollback (D-026), switched off until the runner exists; README System Design and Solution Architecture (D-024).
Next (needs the Nano powered on and the owner): run `./facepipe --selftest` on the device; register the self-hosted runner (the owner runs `config.sh` with the registration token), create its restricted user and sudo rule (exact commands to be approved), set `NANO_RUNNER=true`; deploy the next release through it.

## Update: 2026-11-06 night (model: Claude Opus 5.5)

Done: Mermaid diagrams (ARCHITECTURE A1-A2, FLOW 1-4) rendered in CI, PR template (D-022). Versioning and releases: VERSION 0.9.0, CHANGELOG, Release workflow, docs/RELEASING.md (D-023). First release v0.9.0.
Next: deploy v0.9.0 to the Nano when it is powered on (it was off for camera work); MLOps manifest; self-hosted runner deploy; LFW evaluation.

## Update: 2026-11-06 evening (model: Claude Opus 5.5)

Done: CI on GitHub Actions (scan, docs check, Python 3.6 self-test, compile against JetPack 4.6 headers with cppcheck), D-020, D-021. Engineering docs added (Defend What You Ship).
In progress: MLOps model manifest and checks; deploy through a self-hosted runner on the Nano (owner choice, tags only, outside pull requests blocked); LFW evaluation (download needs owner permission).
Next: as above.

## Session summary: 2026-11-06 (model: Claude Opus 5.5)

The system was built from scratch in one day on the owner's Jetson Nano 4 GB (JetPack 4.6.6) and runs unattended as two systemd services from the app folder on the microSD.

### Done

- DeepStream 6.0.1 pipeline: SCRFD-10G detection with landmarks, IOU tracking, ArcFace w600k_r50 recognition with alignment, gallery matching at cosine 0.5, retries for unknown tracks.
- Enrollment binary (`facepipe-enroll`), daily JSONL attendance log with face snapshots, web dashboard with login, CSV export, live MJPEG view.
- NVENC RTSP server (login required) and event clips with NVJPG full-frame evidence on a 64 GB image-file volume at `/mnt/rec`, rotated weekly when 90% full.
- Sources: RTSP (verified), USB (verified with a Logitech C270), CSI (implemented, hardware blocked, see bugs/csi-hq-camera-not-detected.md).
- systemd units installed, `/mnt/rec` in fstab, IMX477 overlay applied (boot entry `JetsonIO`).
- The external SSD was removed from the Nano's configuration; everything runs from the microSD.

### In progress

- CSI camera: Raspberry Pi HQ Camera needs resistor R8 removed (owner is buying a soldering iron).

### Broken

- Nothing known in the running system. Event clips, full-frame evidence and RTSP playback are implemented but have not been seen end to end with a face in front of the camera.

### Next

1. End-to-end test of an event clip, full-frame evidence and RTSP playback in VLC (needs the RTSP login: `python3 web.py --set-rtsp-password <user>`).
2. CSI test after R8 removal.
3. A face quality score beyond the size gate.
4. License decision for the repository (owner).

### Do not touch

- `/boot/extlinux/extlinux.conf.bak-imx477` and `/etc/fstab.bak-ssd-removed` (rollback copies).
- `web_auth`, `rtsp_auth`, `facepipe.env`, `data/`, `gallery/` on the device: secrets and biometric data, never in git.
- The owner's Windows backup partition on the external SSD: never from Linux.
