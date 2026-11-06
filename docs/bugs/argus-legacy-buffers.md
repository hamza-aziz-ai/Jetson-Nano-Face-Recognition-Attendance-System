# CSI camera: no frames reach the pipeline

Date: 2026-11-06. Status: fixed.

**Symptom:** With the IMX477 detected, `facepipe csi:0` ran at 0 fps and nvstreammux reported `Input buffer number of surfaces (0) must be equal to mux->num_surfaces_per_frame (1)`, while Argus said it was capturing.

**Cause:** On JetPack 4.6, `nvarguscamerasrc` outputs the legacy NvBuffer format by default; DeepStream's nvstreammux needs NvBufSurface.

**Fix:** The CSI source sets `bufapi-version=1` on `nvarguscamerasrc`.

**Proof:** `facepipe csi:0 1920 1080` then ran at 15.0 fps (509 frames in 45 s, every 2nd frame of 30 fps).
