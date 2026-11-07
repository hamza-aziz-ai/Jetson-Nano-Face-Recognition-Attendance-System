# RTSP stream reachable from the internet

Date: 2026-11-07. Status: fixed in v1.10.1.

**Symptom:** The owner opened `rtsp://attendance.hamzaaziz.dev:8554/live` in VLC from the internet (with the RTSP login). Wanted: VLC or an NVR only on the local network; from the internet, only the website. The owner stopped recognition from the website at once (11:30), which also stopped the RTSP server.

**Cause:** facepipe's RTSP server (gst-rtsp-server) listened on `0.0.0.0:8554` and served anyone with the login, whatever their address; its RTP input (`udpsrc` port 5400) also listened on every interface. The Cloudflare Tunnel forwards only the website (port 8000), and from the home network the hostname's Cloudflare addresses did not answer on 8554, so the exact outside path (most likely a router port forward or a direct route to the home address) was not reproduced. The Live page also printed the RTSP address to every visitor.

**Fix:** On `client-connected`, facepipe reads the viewer's address (`gst_rtsp_connection_get_ip`); unless it is private (RFC 1918, fc00::/7), link-local or loopback (`lan_ip`), the client's OPTIONS, DESCRIBE, SETUP and PLAY requests are answered 403 Forbidden and a warning is logged. A port forward keeps the internet sender's address, so it is refused as well; carrier-grade NAT addresses (100.64.0.0/10) count as the internet. The RTP input binds `127.0.0.1`. The Live page shows the RTSP address only when the visitor is on the local network and not coming through the tunnel (`Handler.on_lan`, `lan_address`).

**Test:** `./facepipe --selftest` (local examples accepted; public, carrier-grade NAT, malformed and IPv4-mapped public addresses refused) and the web self-test (same rule); on the Nano, a test instance with a throwaway RTSP login: a DESCRIBE from a laptop on the home network got `RTSP/1.0 200 OK` with the stream description; the log says `RTSP on rtsp://<this device>:8556/live (login required; local network only)`. Not yet run: a viewer on mobile data (expected: 403).

**Lesson:** a login is not a network boundary: a service that streams faces must decide who may reach it, not only who knows the password.
