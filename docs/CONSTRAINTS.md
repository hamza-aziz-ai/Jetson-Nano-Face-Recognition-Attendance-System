# Constraints

## Hardware

- Jetson Nano 4 GB: 4 Cortex-A57 cores, 128-core Maxwell GPU (sm_53), 4 GB shared memory. The GPU caps detection at about 19 fps at 640x384.
- Power: the board crashed once at MAXN with no fan on the original supply. A 5V 4A barrel supply and a fan are needed for continuous load.
- Storage: microSD only (the external SSD is the owner's Windows backup and is not used).
- CSI cameras: IMX219 and IMX477 drivers only; the Raspberry Pi HQ Camera needs resistor R8 removed.

## Software

- JetPack 4.6.6 (L4T 32.7.6), Ubuntu 18.04, Python 3.6, GStreamer 1.14, CUDA 10.2, TensorRT 8.2, DeepStream 6.0.1. Nothing newer runs on this board.
- No pyds, no libsqlite3-dev, no gst-rtsp-server headers on the device; the design avoids needing them.
- `nvjpegenc` crashes on DeepStream GPU buffers; OpenCV `imgcodecs` cannot share a process with it.

## Owner rules

- Build from source on the Nano; no Docker on the Nano.
- Ask before persistence changes (boot config, fstab, systemd units, shell profiles), with the exact commands.
- No passwords typed by the AI; the owner sets logins.
- Mask IP addresses in anything shared; never commit secrets, face photos or attendance data.
- Never touch the NTFS backup partition on the external SSD from Linux.
