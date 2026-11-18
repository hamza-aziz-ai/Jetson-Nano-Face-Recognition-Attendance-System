"""Render the Nano dashboard's real pages against a seeded data directory and save them."""
import http.cookiejar, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

REPO = Path("/mnt/c/Users/hamza/PROJECTS/PersonalProjects/NVIDIA-Jetson-Edge-Devices/Jetson-Nano-Face-Recognition-Attendance-System")
OUT = Path("/mnt/c/Users/hamza/AppData/Local/Temp/claude/f7760584710cd448/63a89032-f624-4450-a44c-689fb5756b71/scratchpad/nano_pages")
PW = "demo demo demo demo"
PEOPLE = {"aarav": 5, "neha": 5, "ravi": 4, "sofia": 5, "daniel": 3, "mei": 5}
TODAY = time.strftime("%Y-%m-%d")
CAMS = [("cam0", "Entrance"), ("cam1", "Corridor")]
# (hour, minute, camera index, name, cosine, unknown id)
VISITS = [(8, 41, 0, "aarav", 0.71, 0), (8, 44, 0, "neha", 0.68, 0), (8, 52, 1, "unknown", 0.33, 1),
          (9, 3, 0, "ravi", 0.64, 0), (9, 18, 1, "sofia", 0.73, 0), (9, 26, 0, "unknown", 0.29, 2),
          (9, 48, 1, "neha", 0.66, 0), (10, 2, 0, "ravi", 0.61, 0), (10, 15, 1, "unknown", 0.31, 1),
          (10, 39, 0, "sofia", 0.70, 0), (11, 7, 1, "aarav", 0.72, 0), (11, 24, 0, "unknown", 0.27, 3),
          (11, 52, 1, "mei", 0.67, 0), (13, 5, 0, "mei", 0.69, 0), (13, 41, 1, "aarav", 0.70, 0),
          (14, 2, 0, "ravi", 0.63, 0), (14, 55, 1, "unknown", 0.26, 4), (15, 9, 0, "sofia", 0.71, 0),
          (15, 33, 1, "neha", 0.64, 0), (15, 58, 0, "mei", 0.68, 0)]

work = Path(tempfile.mkdtemp())
data, gal, snaps = work / "data", work / "gallery", work / "data" / "snaps" / TODAY
for d in (data, gal, snaps, work / "logs"):
    d.mkdir(parents=True, exist_ok=True)

with open(data / ("events-%s.jsonl" % TODAY), "w") as f:
    for i, (h, m, ci, name, score, uid) in enumerate(VISITS):
        t = time.mktime(time.strptime("%s %02d:%02d:%02d" % (TODAY, h, m, (i * 7) % 60), "%Y-%m-%d %H:%M:%S"))
        hms = time.strftime("%H%M%S", time.localtime(t))
        snap = "%s/%s_%d.bmp" % (TODAY, hms, 100 + i)
        (snaps / ("%s_%d.bmp" % (hms, 100 + i))).write_bytes(b"BM")  # the page only needs the path
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)), "ts": t, "track": 100 + i,
                            "name": name, "unknown_id": uid, "score": score, "metric": "cosine",
                            "l2": round((2 - 2 * score) ** 0.5, 3), "det": 0.91, "snap": snap,
                            "frame": "%s/%s_%d.jpg" % (TODAY, hms, 100 + i),
                            "clip": "%s/%s_clip.mp4" % (TODAY, hms), "camera": CAMS[ci][0],
                            "camera_name": CAMS[ci][1], "q": {"blur": 112.4, "yaw": 6.1, "pitch": 3.4}}) + "\n")

with open(data / "gallery.tsv", "w") as f:
    for name, n in PEOPLE.items():
        for i in range(n):
            f.write("%s\t%s_%d.jpg\t%s\n" % (name, name, i + 1, ",".join(["0.0"] * 8)))
for name in PEOPLE:
    (gal / name).mkdir(exist_ok=True)
    for i in range(PEOPLE[name]):
        (gal / name / ("%s_%d.jpg" % (name, i + 1))).write_bytes(b"\xff\xd8\xff")

(data / "status.json").write_text(json.dumps({
    "version": "1.15.0", "updated": time.time(), "fps": 15.0, "det_fps": 2.6, "frames": 412880, "known": 15,
    "unknown": 5, "step": 2, "cameras": [{"id": "cam0", "name": "Entrance", "fps": 15.0, "up": True},
                                         {"id": "cam1", "name": "Corridor", "fps": 14.8, "up": True}]}))
(data / "cameras.json").write_text(json.dumps(
    {"source": "rtsp", "csi": "0", "usb": "/dev/video0", "size": "1280x720",
     "rtsp": [{"id": "a1b2c3d4", "name": "Entrance", "url": "rtsp://ops:secret@cam-entrance.site:554/stream1",
               "enabled": True},
              {"id": "e5f6a7b8", "name": "Corridor", "url": "rtsp://ops:secret@cam-corridor.site:554/stream1",
               "enabled": True}]}))
(data / "cameras.active").write_text("rtsp://ops:secret@cam-entrance.site:554/stream1 1920 1080 a1b2c3d4\n"
                                     "rtsp://ops:secret@cam-corridor.site:554/stream1 1920 1080 e5f6a7b8\n")
(data / "matching.json").write_text(json.dumps({"metric": "cosine", "cosine": 0.50, "l2": 0.90}))

env = dict(os.environ, FACEPIPE_DATA=str(data), FACEPIPE_DIR=str(work), FACEPIPE_GALLERY=str(data / "gallery.tsv"),
           FACEPIPE_WEB_AUTH=str(work / "web_auth"), FACEPIPE_WEB_PORT="8731", FACEPIPE_SITE="Face Attendance",
           FACEPIPE_LOGS=str(work / "logs"), FACEPIPE_REC=str(work / "rec"))
sys.path.insert(0, str(REPO))
import web  # noqa: E402
web.write_auth(str(work / "web_auth"), "demo", PW)

# The System page reads this machine's /proc and /sys, which would print a 20-core, 2 TB "microSD card" on a page
# headed Jetson Nano. The server therefore runs through a wrapper that replaces the four host readers with a board's
# values before serving. The page itself is still rendered by web.py's own code, from its own template.
runner = work / "serve_as_board.py"
runner.write_text("""import sys
sys.path.insert(0, %r)
import web

web.device_health = lambda: {
    "temps": {"AO": 57.5, "CPU": 52.5, "GPU": 49.0, "PLL": 50.0, "PMIC-Die": 48.0, "thermal": 51.5},
    "mem": {"total": 3964, "available": 1402, "swap_total": 1982, "swap_used": 0},
    "cpu": [38, 44, 35, 41], "cpu_mhz": 1479, "gpu": 37, "gpu_mhz": 921,
    "fan": {"pct": 42, "rpm": 3120}, "uptime_h": 6.4, "level": "ok"}
web.disk = lambda path: ("21.4 GB free of 29.8 GB" if "rec" in str(path) else "18.2 GB free of 59.1 GB")
web.service_state = lambda: "active"
_logs = {
    "facepipe.log": ["[18-11-2026] 08:40:58.114 AM, Wednesday [INFO] facepipe 1.15.0 starting, data data",
                     "[18-11-2026] 08:41:03.882 AM, Wednesday [INFO] gallery: 6 people, 27 embeddings",
                     "[18-11-2026] 08:41:07.004 AM, Wednesday [INFO] Entrance and Corridor: 1920x1080 at 15 fps",
                     "[18-11-2026] 08:52:11.620 AM, Wednesday [INFO] unknown #1 logged from Corridor (cosine 0.33)",
                     "[18-11-2026] 03:58:13.441 PM, Wednesday [INFO] mei logged from Entrance (cosine 0.68)"],
    "update.log": ["[18-11-2026] 03:50:02.118 PM, Wednesday [INFO] no newer release than v1.15.0"]}
web.tail = lambda path, n: _logs.get(str(path).split("/")[-1].split(chr(92) + chr(92))[-1], [])[-n:]

srv = web.Server(("127.0.0.1", web.PORT), web.Handler)
srv.serve_forever()
""" % str(REPO))
srv = subprocess.Popen([sys.executable, str(runner)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(3)
base = "http://127.0.0.1:8731"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
try:
    page = op.open(base + "/login", timeout=10).read().decode()
    tok = re.search(r'name="csrf"\s+value="([^"]+)"', page)
    body = urllib.parse.urlencode({"user": "demo", "password": PW, "csrf": tok.group(1) if tok else ""}).encode()
    r = op.open(urllib.request.Request(base + "/login", body, {"Origin": base, "Content-Type": "application/x-www-form-urlencoded"}), timeout=10)
    print("login ->", r.status, r.geturl())
    OUT.mkdir(parents=True, exist_ok=True)
    for pid, href, label in web.PAGES:
        try:
            html = op.open(base + href, timeout=15).read().decode()
            (OUT / (pid + ".html")).write_text(html, encoding="utf-8")
            print("%-10s %-12s %6d bytes" % (pid, href, len(html)))
        except Exception as e:  # noqa: BLE001
            print("!! %-10s %s" % (pid, e))
    for name in ("app.css", "app.js", "logo.svg", "favicon.svg"):
        html = op.open(base + "/static/" + name, timeout=10).read()
        (OUT / name).write_bytes(html)
    for api in ("status", "health"):
        (OUT / ("api_%s.json" % api)).write_bytes(op.open(base + "/api/" + api, timeout=10).read())
    print("saved to", OUT)
finally:
    srv.terminate()
    out = srv.stdout.read().decode(errors="replace")[-600:]
    if out.strip():
        print("--- server said:\n" + out)
    shutil.rmtree(work, ignore_errors=True)
