# HANDOVER

Read first: docs/ARCHITECTURE.md, docs/CONSTRAINTS.md, docs/DECISIONS.md. Newest entry on top.

## Session summary: 2026-11-06 (model: Claude Opus 5.5)

The system was built from scratch in one day on the owner's Jetson Nano 4 GB (JetPack 4.6.6) and runs unattended as two systemd services from `/home/hamza/facepipe` on the microSD.

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
