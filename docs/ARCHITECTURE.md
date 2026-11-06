# Architecture

One Jetson Nano 4 GB runs everything. There is no server, no database service and no cloud dependency.

## How to read the diagrams

Colours are the same in every diagram here and in FLOW.md: blue for entry points (people and clients), green for steps this project runs, amber for decisions, purple for outcomes, red for errors, grey for data stores and services this project does not write. Solid arrows are in use; dotted arrows are optional, switched off, or not built yet.

## A1: Runtime topology

```mermaid
flowchart LR
  CAM["`Camera: RTSP, file, USB or CSI`"]:::store --> FP["`facepipe (systemd facepipe.service)`"]:::step
  FP --> DATA[("`data/: gallery.tsv, events-YYYY-MM-DD.jsonl, snaps/, status.json`")]:::store
  FP --> REC[("`/mnt/rec: event clips and evidence frames`")]:::store
  FP --> SHM[("`/dev/shm/facepipe: live.jpg`")]:::store
  FP -->|"`RTSP 8554, login`"| VLC["`VLC or NVR`"]:::entry
  BROWSER["`Browser`"]:::entry -->|"`HTTP 8000, login`"| WEB["`facepipe-web (systemd facepipe-web.service)`"]:::step
  WEB --> DATA
  WEB --> REC
  WEB --> SHM
  ADMIN["`Owner`"]:::entry --> ENROLL["`facepipe-enroll`"]:::step
  PHOTOS[("`gallery/name/ photos`")]:::store --> ENROLL
  ENROLL --> DATA
  MODELS[("`models/: SCRFD and ArcFace engines`")]:::store --> FP
  MODELS --> ENROLL
  classDef entry fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
  classDef step fill:#dcfce7,stroke:#15803d,color:#14532d
  classDef store fill:#f3f4f6,stroke:#6b7280,color:#111827
```

| Component | What it is | Runs as |
| --- | --- | --- |
| `facepipe` | C++ DeepStream 6.0.1 application: capture, detection, tracking, recognition, event log, video outputs, RTSP server | systemd `facepipe.service`, user `hamza` |
| `facepipe-enroll` | Same source built with `-DFACEPIPE_ENROLL`: photos to gallery | Run by hand |
| `web.py` | Python 3.6 standard-library HTTP server: dashboard, CSV, live view, evidence files | systemd `facepipe-web.service` |
| `models/` | SCRFD-10G (640x384) and ArcFace w600k_r50, ONNX plus TensorRT FP16 engines built on the device | Files |
| `data/` | `gallery.tsv`, `events-YYYY-MM-DD.jsonl`, `snaps/`, `status.json` | Files |
| `/mnt/rec` | 64 GB ext4 image-file volume for event clips and full-frame evidence | fstab loop mount |
| `/dev/shm/facepipe` | `live.jpg` and the `want` flag between facepipe and the web page | RAM |

Trust boundaries are the two network ports (8000 and 8554); see THREAT.md.

## A2: How a change reaches the device

```mermaid
flowchart LR
  DEV["`Commit with docs`"]:::entry --> CI["`GitHub Actions CI: scan, docs check, Python 3.6 self-test, JetPack compile, cppcheck, diagrams`"]:::step
  CI --> OK{"`All green?`"}:::decision
  OK -->|"`no`"| FIX["`Fix and push again`"]:::error
  OK -->|"`yes`"| MAIN["`main`"]:::outcome
  MAIN --> TAG["`Tag vX.Y.Z: release workflow reruns CI, publishes GitHub Release`"]:::step
  TAG --> MANUAL["`Copy to the Nano, make all, restart services`"]:::step
  classDef entry fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
  classDef step fill:#dcfce7,stroke:#15803d,color:#14532d
  classDef decision fill:#fef3c7,stroke:#b45309,color:#78350f
  classDef outcome fill:#ede9fe,stroke:#6d28d9,color:#4c1d95
  classDef error fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
```

Deploys are manual today (copy, `make all`, restart); the release workflow and versioning are in docs/RELEASING.md. Planned work is not drawn here; it is listed in HANDOVER.md.

## Hardware blocks used

| Block | Use |
| --- | --- |
| NVDEC | H.264 decode of RTSP and file sources |
| GPU (Maxwell, 128 cores) | SCRFD and ArcFace in TensorRT FP16, colour conversion, overlay |
| NVJPG | MJPEG decode for USB cameras; JPEG encode for the live view and evidence |
| NVENC | H.264 encode for RTSP and event clips |
| ISP (Argus) | CSI cameras |

See FLOW.md for the path of one face and DECISIONS.md for why each piece is the way it is.
