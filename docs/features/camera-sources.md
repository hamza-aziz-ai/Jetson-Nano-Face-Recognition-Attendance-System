# Feature: camera sources

Added 2026-11-06 at the owner's request ("add support for USB cameras and CSI cameras").

| `FACEPIPE_URI` | Pipeline | Status |
| --- | --- | --- |
| `rtsp://...` | uridecodebin, RTSP forced to TCP, NVDEC | Verified (phone camera, 30 fps) |
| `file:///path.mp4` | uridecodebin, NVDEC | Verified |
| `usb:/dev/v4l/by-id/<camera>` as chosen on the Cameras page (from v1.12.1; the stable udev link by vendor, model and serial), or `usb:/dev/videoN` (the number changes with plug order: the C270 moved from video0 to video1 when a CSI camera was added, and back after the Wi-Fi card was fitted) | v4l2src, `exposure_auto_priority=0`, MJPEG decoded on NVJPG (`FACEPIPE_USB_FORMAT=mjpeg`) or YUY2 converted by nvvideoconvert (`raw`) | Verified (Logitech C270, MJPEG 1280x720) |
| `csi:N` | nvarguscamerasrc (`bufapi-version=1`) sensor-id N, NV12 in GPU memory | Verified with the Raspberry Pi HQ Camera after R8 removal: recognition at cosine 0.62-0.69 |

Cameras capture at the width and height given on the command line and `FACEPIPE_CAM_FPS`. Frame thinning (`FACEPIPE_DROP`) is a pad probe on the nvstreammux sink, so it works for every source type.

Gotcha: in GStreamer 1.14's parser, GPU-memory caps must be written as `capsfilter caps="video/x-raw(memory:NVMM),..."`; a bare or quoted caps string is a syntax error.
