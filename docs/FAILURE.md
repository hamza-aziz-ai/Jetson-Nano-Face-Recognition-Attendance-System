# Failure map

Marked **seen** where it actually happened on the device, otherwise **from the code**, not tested.

| Dependency | If it is down, slow or wrong | How it shows | Recovery |
| --- | --- | --- | --- |
| Camera (RTSP) | Pipeline error, process exits | Status badge "service stopped" or "waiting for the camera"; `Failed to connect` in the journal (**seen**) | systemd restarts every 10 s until the camera is back (**seen**) |
| Camera IP changes (DHCP) | Never connects | Same as above (**seen**: .20 to .22 and back) | Fix the URI in `facepipe.env`, or a router DHCP reservation |
| Wi-Fi packet loss | 4-5 fps over UDP (**seen**) | Low fps in status | RTSP forced to TCP |
| USB webcam in dim light | Camera halves its frame rate (**seen**) | Low fps | `exposure_auto_priority=0` set by the source |
| CSI camera not detected | No camera | `No cameras available` (**seen**) | Hardware: R8 or cable, see bugs/ |
| GPU overloaded | Frames back up | fps below the source rate | Raise `FACEPIPE_DROP` |
| Recognition queue full | Attempts dropped | `dropped` count in the journal | Track retries later (**from the code**) |
| Recordings volume missing | Recording off | `recordings ...: off` at start (**seen** before it existed) | Mount `/mnt/rec`; events still logged without clips |
| Recordings volume full | Writes fail | Missing clips | Oldest week deleted at 90% (**from the code**, never triggered) |
| Gallery file damaged | Lines skipped | Fewer photos in `gallery ... N photos` | Re-run enrollment |
| Power loss at MAXN | Board resets | Uptime resets (**seen** once) | Services start on boot; fix: 5V 4A supply and fan |
| External SSD missing | Formerly: services could not start (**seen**) | | App moved to the microSD; SSD no longer used |
| Clock wrong | Events on the wrong day | Wrong times on the page | NTP keeps it synced; set `TZ` in `facepipe.env` if the system clock is UTC |
| CSI camera daemon stalls the pipeline at start | No frames, process looks active | Watchdog log line `no frames for 60 s` (**seen** once, before the watchdog existed) | Watchdog exits, systemd restarts after 10 s; `deploy.sh` waits 3 s between stop and start |
| Clip finalization | Clip file empty or short | Log line `clip ...: not finalized within 8 s` or `clip ...: <element>: <error>` (empty and short clips **seen**: missing IDR keyframes, fixed in v1.1.2) | The event and its evidence frame are kept; the next clip is independent |
| Shutdown hangs | Service stop takes long | Log line `shutdown did not finish within 10 s, forcing exit` | Exit 4; systemd restarts the service |
| Camera daemon (nvargus-daemon) wedged | Every CSI start gets end of stream or no frames | `journalctl -u nvargus-daemon`: `SCF: Error Timeout`, then `Error InvalidState` per client (**seen** after a camera process was killed mid-capture) | `systemctl restart nvargus-daemon` with facepipe stopped; prevented by the clean shutdown of v1.0.3 |
