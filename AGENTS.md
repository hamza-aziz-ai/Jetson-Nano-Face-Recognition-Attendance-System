# Repository Instructions

Keep this file and `CLAUDE.md` synchronized (CI fails if they differ). They hold the stable operating rules; `MEMORY.md` holds the volatile state: current version, what runs on the device, open work and decisions in progress.

## Start here

1. Read `MEMORY.md` completely.
2. Verify its snapshot: `git log -1`, `git status`, the latest CI run for that exact commit, and on the device `cat <app_dir>/data/status.json` and `systemctl is-active facepipe facepipe-web`.
3. Update `MEMORY.md` in the same commit whenever the state it describes changes (release, deploy, open item closed or added).

## What this is

A standalone face attendance system for one Jetson Nano 4 GB (JetPack 4.6.x, L4T 32.7, DeepStream 6.0.1, TensorRT 8.2, CUDA 10.2, GStreamer 1.14, Python 3.6). Fully offline. One C++ process (`facepipe`) runs the video pipeline; one Python stdlib process (`web.py`) serves the dashboard. Both run as systemd services rendered by `deploy/install.sh`.

```text
camera (RTSP / file / USB / CSI) -> frame drop probe -> nvstreammux -> SCRFD-10G (own TensorRT engine in a probe, VIC letterbox; adaptive rate, D-028)
  -> nvtracker (IOU) -> ArcFace r50 worker thread (own TensorRT engine, Umeyama alignment) -> gallery match (cosine or L2)
  -> nvdsosd -> tee -> NVJPG (live MJPEG + evidence JPEGs)
                   -> NVENC H.264 -> RTSP server :8554/live + ring buffer for event clips
attendance: data/events-YYYY-MM-DD.jsonl, status.json; web.py reads them
logs: logs/<year>/<Month>/{facepipe,web,enroll}.log, daily backups <module>.log.DD-Mon-YYYY (D-030)
```

Key files:

- `facepipe.cpp`: the whole pipeline, enrollment (`-DFACEPIPE_ENROLL` build), `--selftest`, `--version`.
- `web.py` + `web/` (layout, CSS, JS): website with Overview, Live, Attendance, People, System (service control via an exact sudoers rule), login (PBKDF2 hash in `web_auth`), `--selftest` (starts a real server), `--set-password`, `--set-rtsp-password`; registration by invitation with staged gallery publication (`docs/features/registration.md`).
- `Makefile`, `VERSION`, `CHANGELOG.md`, `facepipe.env.example`.
- `deploy/`: `install.sh` (portable install), `deploy.sh` (build, test, package, install, health check, rollback), unit templates `*.service.in`.
- `ci/` and `.github/workflows/`: CI (scan, docs check, Python 3.6 self-test, compile against pinned JetPack headers, cppcheck, Mermaid), release on `v*.*.*` tags, deploy on the self-hosted runner.
- `docs/`: Defend What You Ship set (ARCHITECTURE, FLOW, DECISIONS, ALTERNATIVES, THREAT, FAILURE, DATA, ASSUMPTIONS, TEST-CHECKLIST, ROLLBACK, HANDOVER, ...), `docs/bugs/`, `docs/features/`.

## Commands

On the Nano (from the app folder):

```sh
make                       # facepipe and facepipe-enroll
./facepipe --selftest && python3 web.py --selftest
./facepipe --version
sh deploy/deploy.sh vX.Y.Z <app_dir>   # release deploy with health check and automatic rollback
sudo systemctl start facepipe-update    # automatic updates run this every 10 min (D-031)
```

On the workstation (repo root):

```sh
sh ci/scan.sh              # run before every commit
```

## Rules

- **Defend What You Ship:** code and its docs change in the same commit. New decision: a `D-0NN` entry in `docs/DECISIONS.md`. New bug: a write-up in `docs/bugs/`. User-visible change: a line in `CHANGELOG.md` under `[Unreleased]`. Keep the "Checked against the code of vX.Y.Z" stamp in `docs/FLOW.md` current. The README validation table says only what was really tested on hardware.
- **Branches and pull requests (D-032):** anything that changes behaviour or that CI, a release or a device consumes (code, workflows, deploy scripts, configs, Makefile, tests, hotfixes) goes through a branch `feat/`, `fix/`, `ci/` or `docs/<topic>` and a pull request; one concern each. Title: Conventional Commit, sentence-style, what and why (`fix(clips): ... so ...`). Body: `## What`, `## Proof` (real commands and output, what was not run), optional `## Docs`; end with the attribution lines. Enable auto-merge (`gh pr merge --auto --squash`); it merges on green CI. Docs and Markdown only: straight to main with `[skip ci]` in the title. Stage named files, never `git commit -a`. Prove locally first; no probe pushes.
- **Releases:** never by hand. Raise `VERSION` and add the CHANGELOG section in the pull request; after it merges, the release workflow runs CI, tags `vX.Y.Z` and publishes; the device's update timer deploys it within 10 minutes (D-031).
- **Never commit:** `web_auth`, `rtsp_auth`, `facepipe.env`, `data/`, `gallery/`, models, engines, images, videos, event logs. `ci/scan.sh` enforces this, plus no private IPs and no `/home/<user>/` paths.
- **Portable:** nothing in the repo may depend on one user's name, home folder, IP or camera. Use placeholders (`<app_dir>`, `CAMERA_IP`).
- **Device safety:** never type or store passwords or tokens (the owner sets `web_auth`, `rtsp_auth` and runner tokens). Ask the owner before changing persistent system state (systemd units, sudoers, fstab, partitions, reboots). No Docker on the Nano. Mask IPs in anything shared.
- **Simplest correct design:** no speculative features; reuse what is in the file before adding code; leave one runnable check (`--selftest`) for non-trivial logic.
- **Second opinion:** decisions with real trade-offs (design, security, data loss, hard to reverse) go to a council with Codex in the owner's fixed Codex session; record "with Codex (council)" in the decision. Ownership calls (exposure, retention, money, legal) go to the owner.
- **Editing:** files use LF (`.gitattributes`). Chain edit, scan and commit on one line with `&&`, check `git log -1 --stat` before reporting, and read CI for the exact commit SHA.

## Platform pitfalls (each cost a debugging session; see `docs/bugs/`)

- `nvjpegenc` crashes on NVMM input: feed it system-memory I420.
- OpenCV imgcodecs and `nvjpegenc` cannot share a process (libjpeg ABI clash): enrollment is a separate binary.
- `nvarguscamerasrc` needs `bufapi-version=1`; caps inside `gst_parse` strings must be `capsfilter caps="..."`.
- The detection probe must set `bInferDone` on frames it ran, or nvtracker drops every object.
- RTSP input must use TCP; USB webcams need `exposure_auto_priority=0` for full frame rate.
- `cp` over a running binary fails with "Text file busy": copy beside it and rename.
- nvinfer `interval` is changeable only in NULL or READY state (why detection is our own probe, D-028).
- Do not call the log macros from inside `log_line` (recursion); library prints go only to the journal.
