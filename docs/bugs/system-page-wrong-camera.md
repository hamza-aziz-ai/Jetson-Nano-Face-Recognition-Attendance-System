# System page named the wrong camera

Date: 2026-11-07. Status: fixed in v1.7.2.

**Symptom:** The owner switched to the phone camera over RTSP on the Cameras page; recognition used it, but the System page still said "CSI camera, sensor 0" ("this gives wrong impression as I am using RTSP now").

**Cause:** `camera_label()` read only `FACEPIPE_URI` from `facepipe.env`. Since D-034, facepipe reads `data/cameras.active` (written by the Cameras page) over that value, so the label described the fallback, not the camera in use.

**Fix:** `camera_label()` reads `data/cameras.active` first and falls back to `facepipe.env`, the same order facepipe uses; a network camera is shown by its name and host, never with its login.

**Test:** `python3 web.py --selftest` (an active RTSP camera with a password is labelled "Gate (network camera at cam)"); on the Nano the label for the real setup is "Phone Camera (network camera at <ip>)".
