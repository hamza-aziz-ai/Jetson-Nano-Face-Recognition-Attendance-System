# Raspberry Pi HQ Camera not detected

Date: 2026-11-06. Status: fixed by removing R8 (owner).

**Symptom:** `nvarguscamerasrc`: `No cameras available`. At boot the default IMX219 driver got `no acknowledge from address 0x10` on both CSI ports.

**Steps taken:**

1. Identified the camera as the Raspberry Pi HQ Camera (Sony IMX477).
2. Applied the JetPack overlay "Camera IMX477 Dual" with jetson-io (boot entry `JetsonIO`, backup `extlinux.conf.bak-imx477`).
3. After reboot the imx477 driver probes at 0x1a on I2C buses 7 and 8: `no acknowledge`, error -121.
4. Cable reseated with the board powered off: same result. An I2C scan shows nothing at 0x1a.

**Cause (most likely):** The Jetson drives the camera's reset/enable line at 1.8 V; the HQ board expects 3.3 V and stays in reset. RidgeRun and NVIDIA forum users report that removing resistor R8 on the camera board fixes this.

**Next:** Owner removes R8 (soldering), or uses a Jetson-specific IMX477 board, or streams the HQ camera from a Raspberry Pi over RTSP.

**Resolution:** The owner removed R8 (it sits near the bottom edge of the HQ Camera V1.0, by the round test pad next to the bottom-right mounting hole). After power-on the imx477 driver bound on I2C bus 7 (CAM0) and the camera appeared as `/dev/video0`. Argus capture ran at 30 fps (sensor modes 3840x2160 at 30 fps and 1920x1080 at 60 fps); the pipeline then needed `bufapi-version=1` (see argus-legacy-buffers.md). The first frames were a uniform purple blur: no lens fitted or out of focus, and the purple tint suggests a missing ISP tuning file for the IMX477 on the Nano.
