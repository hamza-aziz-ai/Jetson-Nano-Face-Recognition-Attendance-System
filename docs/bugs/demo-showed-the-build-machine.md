# The demo's System page showed the build machine, not a Jetson Nano

**Seen:** 18 Nov 2026, on `https://attendance.hamzaaziz.dev/demo/`, in a review of the published page.

## Symptom

The System page, under a heading that says Jetson Nano edge AI, reported **20 CPU cores, 15.5 GB of memory and a
2 TB "microSD card"** — the laptop that built the demo. Alongside that it said *Recognition is RUNNING* while the
`systemd state` row read `inactive`, the recent log read `no log yet`, and temperature and GPU read `not available`.
The Cameras page and the events named two cameras, Entrance and Corridor, while System named one USB camera at
`/dev/video0`.

Anyone who knows the board would stop reading there, which on a page built to be read by exactly those people is the
worst possible failure.

## Cause

`tools/demo/capture.py` starts the real `web.py` and saves what it renders — which is the point, and why the rest of
the demo is honest. But `page_system` reads the **host**: `device_health()` from `/proc` and `/sys`, `disk()` from
`statvfs`, `service_state()` from `systemctl is-active facepipe`, and `tail()` from the log files. On the capture
machine every one of those answers about the capture machine. The camera mismatch had the same root: the seeded
`cameras.json` configured a USB source while `status.json` and the events described two network cameras.

## Fix

The capture now serves through a small wrapper written beside the seeded data directory, which replaces those four
readers with a board's values and then starts `web.py`'s own `Server`:

```python
web.device_health = lambda: {...}      # six Jetson thermal zones, 4 cores at 1479 MHz, 3.9 GB, fan 42% / 3120 rpm
web.disk = lambda path: ...            # 18.2 GB free of 59.1 GB; the recordings volume separately
web.service_state = lambda: "active"
web.tail = lambda path, n: ...         # log lines in the real format
```

The page is still rendered by `web.py`'s own template from its own code; only what the host would have answered is
replaced. The seeded config is now two enabled network cameras (D-039), so `camera_label()`, `status.json` and the
events agree.

## Lesson

A demo captured from a real server inherits everything that server reads about the machine it is on. The device pages
are exactly the ones worth showing and exactly the ones that leak the build machine, so they need their inputs
supplied rather than read. The check is to open the published page and read it as someone who knows the hardware —
no test in this repository would have caught it, because nothing here was wrong.
