# Wi-Fi RTSP camera at 4-5 fps

Date: 2026-11-06. Status: fixed.

**Symptom:** The pipeline swung between 1.4 and 14.8 fps on a 30 fps phone camera.

**Investigation:** Decode only, no inference, over UDP: average 4-5 fps in bursts. Ping to the camera: 3-172 ms. Same decode over TCP: steady 29.7 fps.

**Cause:** Packet loss on Wi-Fi with RTP over UDP.

**Fix:** The source forces RTSP over TCP (`protocols=4`, latency 200 ms) through uridecodebin's `source-setup` signal.
