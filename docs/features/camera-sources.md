# Feature: camera sources

Added 2026-11-06 at the owner's request ("add support for USB cameras and CSI cameras").

| `FACEPIPE_URI` | Pipeline | Status |
| --- | --- | --- |
| `rtsp://...` | uridecodebin, RTSP forced to TCP, NVDEC | Verified (phone camera, 30 fps) |
| `file:///path.mp4` | uridecodebin, NVDEC | Verified |
| `usb:/dev/videoN` | v4l2src, `exposure_auto_priority=0`, MJPEG decoded on NVJPG (`FACEPIPE_USB_FORMAT=mjpeg`) or YUY2 converted by nvvideoconvert (`raw`) | Verified (Logitech C270, MJPEG 1280x720) |
| `csi:N` | nvarguscamerasrc sensor-id N, NV12 in GPU memory | Pipeline verified to build; camera blocked (see bugs/csi-hq-camera-not-detected.md) |

Cameras capture at the width and height given on the command line and `FACEPIPE_CAM_FPS`. Frame thinning (`FACEPIPE_DROP`) is a pad probe on the nvstreammux sink, so it works for every source type.

Gotcha: in GStreamer 1.14's parser, GPU-memory caps must be written as `capsfilter caps="video/x-raw(memory:NVMM),..."`; a bare or quoted caps string is a syntax error.
