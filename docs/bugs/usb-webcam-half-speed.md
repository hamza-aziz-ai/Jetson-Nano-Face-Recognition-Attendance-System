# USB webcam at half its frame rate

Date: 2026-11-06. Status: fixed.

**Symptom:** A Logitech C270 at 1280x960 MJPEG processed only 3 fps; raw decode measured 15 fps although the camera advertises 30.

**Cause:** `exposure_auto_priority=1`: in dim light the camera lengthens exposure and drops its frame rate.

**Fix:** The v4l2src source sets `extra-controls="c,exposure_auto_priority=0"`.

**Proof:** 1280x720 run at 10.6-11.2 fps processed (with a compile loading the CPU), owner recognized on the second try.
