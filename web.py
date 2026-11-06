#!/usr/bin/env python3
"""Attendance web page for facepipe on the Jetson Nano. Python 3.6 standard library only; works fully offline.

Reads $FACEPIPE_DATA/events-YYYY-MM-DD.jsonl and status.json (written by facepipe) and the enrolled gallery, and serves,
behind HTTP Basic auth:
  /                 dashboard for today (or ?date=YYYY-MM-DD): present, absent, unknown faces, live status
  /csv?date=        attendance report as CSV (present and absent)
  /api/status       live status JSON (polled by the page)
  /snap/<file>      face snapshots;  /photo/<name>  first enrolled photo of a person
  /live.mjpg        live annotated camera view (MJPEG from facepipe's NVJPG branch, only encoded while watched)
  /rec/<file>       event evidence frames and clips from the recordings volume ($FACEPIPE_REC, default /mnt/rec)
Set the login once (prompts, never stored in plain text):  python3 web.py --set-password <user>
RTSP login for facepipe's rtsp://<nano>:8554/live (stored 0600, RTSP needs the password itself):
                                                    python3 web.py --set-rtsp-password <user>
Run:  python3 web.py   (env: FACEPIPE_DATA, FACEPIPE_DIR, FACEPIPE_WEB_PORT=8000, FACEPIPE_SITE="Face Attendance",
                        FACEPIPE_LOGS; log: $FACEPIPE_LOGS/<year>/<Month>/web.log, rotated daily like facepipe.log)
Self-check:  python3 web.py --selftest
"""
import base64, csv, datetime, fcntl, getpass, hashlib, hmac, html, io, json, os, re, socket, socketserver, string, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("FACEPIPE_DATA", os.path.join(HERE, "data"))
GALLERY_DIR = os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "gallery")
GALLERY_TSV = os.environ.get("FACEPIPE_GALLERY", os.path.join(DATA, "gallery.tsv"))
AUTH_FILE = os.environ.get("FACEPIPE_WEB_AUTH", os.path.join(HERE, "web_auth"))  # outside DATA, never served
PORT = int(os.environ.get("FACEPIPE_WEB_PORT", "8000"))
SITE = os.environ.get("FACEPIPE_SITE", "Face Attendance")
try:
    VERSION = open(os.path.join(HERE, "VERSION")).read().strip()
except OSError:
    VERSION = "dev"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SNAP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{6}_\d+\.(jpg|bmp)$")
REC_DIR = os.environ.get("FACEPIPE_REC", "/mnt/rec")
REC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{6}_(\d+\.jpg|clip\.mp4)$")
LIVE_DIR = "/dev/shm/facepipe"
RTSP_AUTH = os.environ.get("FACEPIPE_RTSP_AUTH", os.path.join(HERE, "rtsp_auth"))
RTSP_PORT = os.environ.get("FACEPIPE_RTSP_PORT", "8554")
CTYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".bmp": "image/bmp", ".mp4": "video/mp4"}
STALE_S = 20  # status.json older than this: recognition service is not running
LOG_ROOT = os.environ.get("FACEPIPE_LOGS", os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "logs"))
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]  # fixed English names, not the locale's
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------- logs: same layout, rotation and line format as facepipe.cpp ----------
def log_stamp(t):
    """[DD-MM-YYYY] hh:mm:ss.fff AM/PM, Weekday (owner's format, 12-hour clock)"""
    return "[%s] %02d:%02d:%02d.%03d %s, %s" % (t.strftime("%d-%m-%Y"), t.hour % 12 or 12, t.minute, t.second,
                                                t.microsecond // 1000, "AM" if t.hour < 12 else "PM", WEEKDAYS[t.weekday()])


class DailyLog:
    """<root>/<year>/<Month>/<module>.log; with the first line after midnight it becomes <module>.log.DD-Mon-YYYY in its
    own month folder (at most 31 a month). Lines also go to stderr (the journal)."""

    def __init__(self, module, root=LOG_ROOT):
        self.module, self.root, self.f, self.day, self.lock = module, root, None, None, threading.Lock()

    def path(self, d):
        return os.path.join(self.root, str(d.year), MONTHS[d.month - 1], self.module + ".log")

    @staticmethod
    def backup(path, d):
        dst = "%s.%02d-%s-%04d" % (path, d.day, MONTHS[d.month - 1][:3], d.year)
        if not os.path.exists(dst):
            os.rename(path, dst)
            return
        with open(path, "rb") as src, open(dst, "ab") as out:  # a second run on that day: append
            out.write(src.read())
        os.remove(path)

    def _open(self, today):
        prev = today.replace(day=1) - datetime.timedelta(days=1)
        for d in (today, prev):  # a file left from an earlier day, in this or the previous month's folder
            p = self.path(d)
            if os.path.exists(p):
                mday = datetime.date.fromtimestamp(os.path.getmtime(p))
                if mday != today:
                    self.backup(p, mday)
        os.makedirs(os.path.dirname(self.path(today)), exist_ok=True)
        self.f, self.day = open(self.path(today), "a"), today

    def write(self, level, msg, now=None):
        now = now or datetime.datetime.now()
        sys.stderr.write("[%s] %s\n" % (level, msg))
        with self.lock:
            try:
                if now.date() != self.day:
                    if self.f:
                        self.f.close()
                        self.backup(self.path(self.day), self.day)
                    self._open(now.date())
                self.f.write("%s [%s] %s\n" % (log_stamp(now), level, msg))
                self.f.flush()
            except OSError as e:
                sys.stderr.write("[ERROR] log file: %s\n" % e)


LOG = DailyLog("web")


# ---------- auth ----------
def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200000).hex()


def set_password(user):
    pw = getpass.getpass("New password for %s: " % user)
    if len(pw) < 8 or pw != getpass.getpass("Again: "):
        sys.exit("passwords differ or shorter than 8 characters")
    salt = os.urandom(16)
    fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("%s:%s:%s\n" % (user, salt.hex(), hash_pw(pw, salt)))
    print("saved", AUTH_FILE)


def set_rtsp_password(user):
    """RTSP Basic auth needs the password itself (it is checked by gst-rtsp-server), so it is kept in a 0600 file."""
    pw = getpass.getpass("New RTSP password for %s: " % user)
    if len(pw) < 8 or pw != getpass.getpass("Again: ") or ":" in user:
        sys.exit("passwords differ, shorter than 8 characters, or ':' in the user name")
    fd = os.open(RTSP_AUTH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("%s:%s\n" % (user, pw))
    print("saved", RTSP_AUTH, "- restart facepipe to apply: sudo systemctl restart facepipe")


def load_auth():
    user, salt, digest = open(AUTH_FILE).read().strip().split(":")
    return user, bytes.fromhex(salt), digest


# ---------- data ----------
def read_events(day):
    path = os.path.join(DATA, "events-%s.jsonl" % day)
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass  # a line cut short by a crash
    return out


def attendance(events):
    """known people: name -> {first, last, sightings, best, snap}; unknown: list of events (time order)."""
    people, unknown = {}, []
    for e in sorted(events, key=lambda e: e["ts"]):
        if e["name"] == "unknown":
            unknown.append(e)
            continue
        p = people.setdefault(e["name"], {"first": e["time"], "snap": e["snap"], "sightings": 0, "best": 0.0})
        p["last"] = e["time"]
        p["last_snap"] = e["snap"]
        for k in ("frame", "clip"):  # evidence from the first sighting that has it (arrival)
            if not p.get(k) and e.get(k):
                p[k] = e[k]
        p["sightings"] += 1
        p["best"] = max(p["best"], e["score"])
    return people, unknown


def enrolled():
    """name -> number of enrolled photos, from the gallery the recognizer uses."""
    names = {}
    if os.path.exists(GALLERY_TSV):
        with open(GALLERY_TSV) as f:
            for line in f:
                n = line.split("\t", 1)[0]
                if n:
                    names[n] = names.get(n, 0) + 1
    return names


def status():
    """state: live | no_video | stopped, plus the numbers facepipe reported."""
    try:
        with open(os.path.join(DATA, "status.json")) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return {"state": "stopped", "detail": "Recognition service is not running"}
    age = time.time() - s.get("updated", 0)
    if age > STALE_S:
        s.update(state="stopped", detail="No report from the recognition service for %d s" % age)
    elif s.get("fps", 0) < 0.5:
        s.update(state="no_video", detail="Service running, waiting for the camera")
    else:
        s.update(state="live", detail="Live, %.1f fps" % s["fps"])
    return s


def display_name(n):
    return n.replace("_", " ").strip().title()


def clock(iso, seconds):
    """12-hour time of an ISO timestamp, e.g. 09:41 PM or 09:41:07 PM"""
    if not iso:
        return ""
    h, m, s = int(iso[11:13]), iso[14:16], iso[17:19]
    return "%02d:%s%s %s" % (h % 12 or 12, m, ":" + s if seconds else "", "AM" if h < 12 else "PM")


def hm(iso):
    return clock(iso, False)


def hms(iso):
    return clock(iso, True)


# ---------- pages (D-033) ----------
WEB_DIR = os.path.join(HERE, "web")
STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "logo.svg": "image/svg+xml"}
LAYOUT = string.Template(open(os.path.join(WEB_DIR, "layout.html"), encoding="utf-8").read())
PAGES = [("overview", "/", "Overview"), ("live", "/live", "Live"), ("attendance", "/attendance", "Attendance"),
         ("people", "/people", "People"), ("system", "/system", "System")]
CSP = ("default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
FLASH = {  # fixed messages only: the query string never reflects text into the page
    "started": ("ok", "Recognition service started."), "stopped": ("ok", "Recognition service stopped. It stays stopped, also across updates, until you start it."),
    "restarted": ("ok", "Recognition service restarted."), "busy": ("bad", "An update or another action is running; try again in a minute."),
    "failed": ("bad", "The action failed; see the log below."), "denied": ("bad", "The request was refused (expired form or another site).")}
esc = html.escape


def layout(page_id, title, body, flash="", refresh=False):
    st = status()
    nav = "".join("<a href='%s'%s>%s</a>" % (href, " aria-current='page'" if pid == page_id else "", label)
                  for pid, href, label in PAGES)
    msg = ""
    if flash in FLASH:
        kind, text = FLASH[flash]
        msg = "<div class='flash %s' role='status'>%s</div>" % (kind, esc(text))
    return LAYOUT.substitute(title=esc(title), site=esc(SITE), page=page_id, refresh="1" if refresh else "0", nav=nav,
                             state=st["state"], detail=esc(st["detail"]), flash=msg, body=body, version=esc(VERSION),
                             now=hms(datetime.datetime.now().isoformat()))


def evidence(e):
    """links to the full-frame image and the event clip (recordings volume), if they exist"""
    out = []
    if e.get("frame") and REC_RE.match(e["frame"]):
        out.append("<a href='/rec/%s' target='_blank' rel='noopener'>Frame</a>" % esc(e["frame"]))
    if e.get("clip") and REC_RE.match(e["clip"]):
        out.append("<a href='/rec/%s' target='_blank' rel='noopener'>Clip</a>" % esc(e["clip"]))
    return " &middot; ".join(out) or "<span class='muted'>&ndash;</span>"


def avatar(n, gal):
    return "<img class='av' src='/photo/%s' alt='' loading='lazy'>" % quote(n) if n in gal else "<span class='av'></span>"


def summary_cards(people, unknown, gal, st):
    present = len([n for n in people if n in gal])
    rate = "%d%% of %d enrolled" % (round(100.0 * present / len(gal)), len(gal)) if gal else "no one enrolled"
    last = max([p["last"] for p in people.values()] + [e["time"] for e in unknown], default="")
    return """<div class="cards">
 <div class="card"><div class="k">Present</div><div class="v">%d</div><div class="s">%s</div></div>
 <div class="card"><div class="k">Absent</div><div class="v">%d</div><div class="s">of the enrolled list</div></div>
 <div class="card"><div class="k">Unknown faces</div><div class="v">%d</div><div class="s">not matched to anyone</div></div>
 <div class="card"><div class="k">Last activity</div><div class="v">%s</div><div class="s">%s</div></div>
</div>""" % (len(people), rate, len([n for n in gal if n not in people]), len(unknown), hm(last) or "&ndash;",
             "last face seen" if last else "nothing seen yet")


def page_overview():
    today = datetime.date.today().isoformat()
    events = read_events(today)
    people, unknown = attendance(events)
    gal = enrolled()
    st = status()
    recent = sorted(events, key=lambda e: e["ts"], reverse=True)[:8]
    rows = "".join(
        "<tr><td><div class='who'><img class='av' src='/snap/%s' alt=''>%s</div></td><td class='num'>%s</td>"
        "<td class='num'>%.2f</td><td class='ev'>%s</td></tr>"
        % (esc(e["snap"]), "Unknown" if e["name"] == "unknown" else esc(display_name(e["name"])), hms(e["time"]),
           e["score"], evidence(e)) for e in recent if SNAP_RE.match(e.get("snap", "")))
    act = ("<table><thead><tr><th scope='col'>Who</th><th scope='col'>Time</th><th scope='col'>Match</th>"
           "<th scope='col'>Evidence</th></tr></thead><tbody>%s</tbody></table>" % rows) if rows else \
        "<p class='empty'>No faces seen yet today.</p>"
    svc = ("<div class='card'><div class='k'>Recognition</div><div class='v'><span class='pill %s'>%s</span></div>"
           "<div class='s'>%s</div></div>" % (st["state"], {"live": "Running", "no_video": "No video", "stopped": "Stopped"}[st["state"]],
                                               esc(st["detail"])))
    body = """<h1>Overview</h1><p class="sub">%s</p>%s
<div class="cards">%s
 <div class="card"><div class="k">Detection</div><div class="v">%s</div><div class="s">detections per second (lower when no one is in view)</div></div>
 <div class="card"><div class="k">Enrolled</div><div class="v">%d</div><div class="s"><a href="/people">see people</a></div></div>
</div>
<section aria-labelledby="h-act"><div class="sh"><h2 id="h-act">Recent activity</h2><span class="count">today</span>
 <span class="end"><a class="btn" href="/live">Live view</a><a class="btn primary" href="/attendance">Full attendance</a></span></div>%s</section>""" % (
        datetime.date.today().strftime("%A, %d %B %Y"), summary_cards(people, unknown, gal, st), svc,
        "%.1f" % st["det_fps"] if "det_fps" in st else "&ndash;", len(gal), act)
    return layout("overview", "Overview", body, refresh=True)


def page_live(host):
    rtsp = ("<p class='body muted'>Also as a video stream for VLC or an NVR: <code>rtsp://%s:%s/live</code> (separate RTSP login).</p>"
            % (esc(host), RTSP_PORT)) if os.path.exists(RTSP_AUTH) else ""
    body = """<h1>Live</h1><p class="sub">The camera with recognition overlay: amber while recognizing, green with the name, red for unknown.</p>
<section aria-labelledby="h-live"><div class="sh"><h2 id="h-live">Camera</h2><span class="count">annotated on the device</span>
 <span class="end"><button class="btn" id="livebtn" type="button">Pause</button></span></div>
<div class="stage" role="img" aria-label="Live camera view with face boxes and names"><img id="liveimg" class="video" alt=""><canvas id="livefrozen" class="video" hidden></canvas><span id="livetag" class="tag" hidden>Paused</span></div>%s</section>""" % rtsp
    return layout("live", "Live", body)


def page_attendance(day, today):
    people, unknown = attendance(read_events(day))
    gal = enrolled()
    st = status()
    absent = sorted(n for n in gal if n not in people)
    d = datetime.date(*map(int, day.split("-")))
    prev_d, next_d = (d - datetime.timedelta(days=1)).isoformat(), (d + datetime.timedelta(days=1)).isoformat()
    is_today = day == today
    rows = []
    for n, p in sorted(people.items(), key=lambda kv: kv[1]["first"]):
        rows.append(
            "<tr data-name='%s'><td data-v='%s'><div class='who'><img class='av' src='/snap/%s' alt='Face seen at %s'>%s%s</div></td>"
            "<td class='num' data-v='%s'>%s</td><td class='num' data-v='%s'>%s</td><td class='num' data-v='%d'>%d</td>"
            "<td class='num' data-v='%.3f'><meter min='0' max='1' value='%.3f'></meter>%.2f</td><td class='ev'>%s</td></tr>"
            % (esc(display_name(n).lower()), esc(display_name(n).lower()), esc(p["snap"]), hms(p["first"]), esc(display_name(n)),
               "" if n in gal else " <small>(no longer enrolled)</small>", p["first"], hms(p["first"]), p["last"], hms(p["last"]),
               p["sightings"], p["sightings"], p["best"], max(0.0, min(1.0, p["best"])), p["best"], evidence(p)))
    present = (
        "<table data-sort><thead><tr><th data-k scope='col'><button>Name</button></th><th data-k scope='col'><button>First seen</button></th>"
        "<th data-k scope='col'><button>Last seen</button></th><th data-k scope='col'><button>Sightings</button></th>"
        "<th data-k scope='col'><button>Best match</button></th><th scope='col'>Evidence</th></tr></thead><tbody>%s</tbody></table>"
        % "".join(rows)) if rows else "<p class='empty'>No one recognized %s.</p>" % ("yet today" if is_today else "on this day")
    absent_html = ("<div class='chips'>%s</div>" % "".join(
        "<span class='chip' data-name='%s'>%s%s</span>" % (esc(display_name(n).lower()), avatar(n, gal), esc(display_name(n)))
        for n in absent)) if absent else "<p class='empty'>%s</p>" % ("Everyone enrolled is present." if gal else "No one is enrolled yet.")
    unknown_html = ("<div class='grid'>%s</div>" % "".join(
        "<figure><img src='/snap/%s' alt='Unknown face at %s' loading='lazy'><figcaption>%s &middot; %.2f<br>%s</figcaption></figure>"
        % (esc(e["snap"]), hms(e["time"]), hms(e["time"]), e["score"], evidence(e)) for e in reversed(unknown))
                    ) if unknown else "<p class='empty'>No unknown faces.</p>"
    dis = 'aria-disabled="true" tabindex="-1"'
    body = """<h1>Attendance</h1><p class="sub">%s</p>
<div class="toolbar" role="group" aria-label="Choose date">
 <a class="btn" href="/attendance?date=%s" aria-label="Previous day">&lsaquo;</a>
 <form method="get" action="/attendance"><label class="sr" for="date">Date</label><input id="date" type="date" name="date" value="%s" max="%s"></form>
 <a class="btn" href="/attendance?date=%s" aria-label="Next day" %s>&rsaquo;</a>
 <a class="btn" href="/attendance" %s>Today</a><span class="grow"></span>
 <a class="btn primary" href="/csv?date=%s">Export CSV</a><button class="btn" type="button" id="print">Print</button>
</div>%s
<section aria-labelledby="h-present"><div class="sh"><h2 id="h-present">Present</h2><span class="count">%d</span>
 <span class="end"><label class="sr" for="q">Search names</label><input id="q" type="search" placeholder="Search names" autocomplete="off"></span></div>%s</section>
<section aria-labelledby="h-absent"><div class="sh"><h2 id="h-absent">Absent</h2><span class="count">%d</span></div>%s</section>
<section aria-labelledby="h-unknown" class="noprint"><div class="sh"><h2 id="h-unknown">Unknown faces</h2><span class="count">%d</span></div>%s</section>""" % (
        d.strftime("%A, %d %B %Y"), prev_d, day, today, next_d, dis if day >= today else "", dis if is_today else "", day,
        summary_cards(people, unknown, gal, st), len(people), present, len(absent), absent_html, len(unknown), unknown_html)
    return layout("attendance", "Attendance", body, refresh=is_today)


def page_people():
    gal = enrolled()
    today = datetime.date.today().isoformat()
    people, _ = attendance(read_events(today))
    cards = "".join(
        "<div class='person' data-name='%s'>%s<div><div class='n'>%s</div><div class='m'>%d photo%s enrolled</div>%s</div></div>"
        % (esc(display_name(n).lower()), avatar(n, gal), esc(display_name(n)), c, "" if c == 1 else "s",
           "<span class='badge in'>In today, %s</span>" % hm(people[n]["first"]) if n in people else "<span class='badge out'>Not seen today</span>")
        for n, c in sorted(gal.items(), key=lambda kv: display_name(kv[0])))
    body = """<h1>People</h1><p class="sub">Everyone the device recognizes. To add someone, put a few clear photos in <code>gallery/&lt;name&gt;/</code> and run <code>./facepipe-enroll gallery</code>; self-registration with approval is planned.</p>
<section aria-labelledby="h-people"><div class="sh"><h2 id="h-people">Enrolled</h2><span class="count">%d</span>
 <span class="end"><label class="sr" for="q">Search names</label><input id="q" type="search" placeholder="Search names" autocomplete="off"></span></div>%s</section>""" % (
        len(gal), "<div class='people'>%s</div>" % cards if cards else "<p class='empty'>No one is enrolled yet.</p>")
    return layout("people", "People", body)


def camera_label():
    """the camera from facepipe.env, without credentials"""
    uri = ""
    try:
        for line in open(os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "facepipe.env"), encoding="utf-8"):
            if line.startswith("FACEPIPE_URI="):
                uri = line.split("=", 1)[1].strip()
    except OSError:
        pass
    if uri.startswith("csi:"):
        return "CSI camera, sensor %s" % uri[4:]
    if uri.startswith("usb:"):
        return "USB camera %s" % uri[4:]
    if uri.startswith(("rtsp://", "http://", "https://")):
        u = urlparse(uri)
        return "%s camera at %s" % (u.scheme.upper(), u.hostname or "?")
    return "video file" if uri.startswith("file://") else (uri and "other source") or "not set"


def tail(path, n):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * 1024))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def disk(path):
    try:
        v = os.statvfs(path)
    except OSError:
        return "not available"
    total, free = v.f_blocks * v.f_frsize, v.f_bavail * v.f_frsize
    return "%.1f GB free of %.1f GB" % (free / 1e9, total / 1e9) if total else "not available"


def page_system(csrf, flash):
    st = status()
    active = service_state()
    desired = read_desired()
    now = datetime.datetime.now()
    logdir = os.path.join(LOG_ROOT, str(now.year), MONTHS[now.month - 1])
    log_lines = [l for l in tail(os.path.join(logdir, "facepipe.log"), 40) if "TensorRT" not in l][-15:]
    upd = tail(os.path.join(logdir, "update.log"), 3)

    def button(action, label, cls, confirm):
        return ("<form method='post' action='/system/service' data-confirm='%s' data-title='%s the recognition service?' "
                "data-ok='%s' data-kind='%s'><input type='hidden' name='csrf' value='%s'>"
                "<input type='hidden' name='action' value='%s'><button class='btn %s' type='submit'>%s</button></form>"
                % (esc(confirm), label, label, "danger" if cls == "danger" else "primary", csrf, action, cls, label))
    buttons = "".join([
        button("start", "Start", "primary", "Recognition and attendance recording begin again.") if active != "active" else "",
        button("restart", "Restart", "", "Video and recognition stop for a few seconds, then start again.") if active == "active" else "",
        button("stop", "Stop", "danger", "No attendance is recorded until it is started again. It stays stopped across updates.")
        if active == "active" else ""])
    body = """<h1>System</h1><p class="sub">Service control, device status and recent log lines.</p>
<section aria-labelledby="h-svc"><div class="sh"><h2 id="h-svc">Recognition service</h2><span class="pill %s">%s</span></div>
<dl class="kv"><dt>systemd state</dt><dd>%s</dd><dt>Wanted state</dt><dd>%s</dd><dt>Frame rate</dt><dd>%s</dd>
<dt>Detections per second</dt><dd>%s</dd><dt>Frames processed</dt><dd>%s</dd><dt>Camera</dt><dd>%s</dd></dl>
<div class="actions">%s</div></section>
<section aria-labelledby="h-dev"><div class="sh"><h2 id="h-dev">Device</h2></div>
<dl class="kv"><dt>Software</dt><dd>facepipe %s (web %s)</dd><dt>Recordings volume</dt><dd>%s</dd><dt>App storage</dt><dd>%s</dd>
<dt>Last update check</dt><dd>%s</dd></dl></section>
<section aria-labelledby="h-log" class="noprint"><div class="sh"><h2 id="h-log">Recent log</h2><span class="count">facepipe.log</span></div>
<pre class="log">%s</pre></section>""" % (
        st["state"], esc(st["detail"]), esc(active), "running" if desired != "stopped" else "stopped (kept across updates)",
        "%.1f fps" % st["fps"] if "fps" in st else "&ndash;", "%.1f" % st["det_fps"] if "det_fps" in st else "&ndash;",
        st.get("frames", "&ndash;"), esc(camera_label()), buttons, esc(str(st.get("version", "?"))), esc(VERSION), esc(disk(REC_DIR)),
        esc(disk(HERE)), esc(upd[-1]) if upd else "no update has run yet", esc("\n".join(log_lines)) or "no log yet")
    return layout("system", "System", body, flash=flash)


# ---------- service control (D-033): exact sudoers rule for start/stop/restart of facepipe only ----------
DESIRED = os.path.join(DATA, "desired")              # "stopped" keeps facepipe stopped across updates (deploy.sh reads it)
LOCK = os.path.join(DATA, "update", "lock")          # shared with deploy/update.sh: no action during an update
SYSTEMCTL = "/bin/systemctl"
ACTIONS = ("start", "stop", "restart")


def service_state():
    try:
        return subprocess.run([SYSTEMCTL, "is-active", "facepipe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5).stdout.decode().strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def read_desired():
    try:
        return open(DESIRED).read().strip()
    except OSError:
        return "running"


def service_action(action):
    """runs `sudo -n systemctl <action> facepipe` under the update lock; returns a FLASH key"""
    if action not in ACTIONS:
        return "denied"
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    with open(LOCK, "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "busy"
        with open(DESIRED + ".tmp", "w") as f:
            f.write("stopped\n" if action == "stop" else "running\n")
        os.replace(DESIRED + ".tmp", DESIRED)
        try:
            r = subprocess.run(["sudo", "-n", SYSTEMCTL, action, "facepipe"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            LOG.write("ERROR", "service %s failed: %s" % (action, e))
            return "failed"
    if r.returncode != 0:
        LOG.write("ERROR", "service %s failed: %s" % (action, r.stderr.decode(errors="replace").strip()[:300]))
        return "failed"
    LOG.write("INFO", "service %s from the website; now %s" % (action, service_state()))
    return {"start": "started", "stop": "stopped", "restart": "restarted"}[action]


def csv_text(day):
    people, _ = attendance(read_events(day))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "name", "status", "first_seen", "last_seen", "sightings", "best_match"])
    for n, p in sorted(people.items(), key=lambda kv: kv[1]["first"]):
        w.writerow([day, display_name(n), "present", hms(p["first"]), hms(p["last"]), p["sightings"], "%.3f" % p["best"]])
    for n in sorted(x for x in enrolled() if x not in people):
        w.writerow([day, display_name(n), "absent", "", "", 0, ""])
    return buf.getvalue()


def first_photo(name):
    if name not in enrolled():  # only people the recognizer knows; also blocks path tricks
        return None
    folder = os.path.join(GALLERY_DIR, name)
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    except OSError:
        return None
    return os.path.join(folder, files[0]) if files else None


# ---------- server ----------
MAX_BODY = 4096       # forms here are tiny
MAX_REQUESTS = 24     # concurrent requests; each thread costs memory on a 4 GB device
MAX_LIVE = 4          # concurrent MJPEG viewers
FAIL_LIMIT, FAIL_WINDOW = 10, 900  # failed logins per client before refusing without hashing (PBKDF2 is costly)
SECRET = os.urandom(32)            # per process: CSRF tokens end with a restart, forms then ask to reload


def csrf_token(user):
    return hmac.new(SECRET, ("csrf:" + user).encode(), hashlib.sha256).hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = "facepipe"
    sys_version = ""
    timeout = 30  # a client that stops sending is dropped

    def user(self):
        """the logged-in user name, or None. Failed attempts are counted per client and throttled before hashing."""
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return None
        ip, now = self.client_address[0], time.time()
        fails = [t for t in self.server.fails.get(ip, []) if now - t < FAIL_WINDOW]
        if len(fails) >= FAIL_LIMIT:
            return None
        try:
            user, pw = base64.b64decode(h[6:]).decode().split(":", 1)
        except Exception:
            user, pw = "", ""
        u, salt, digest = self.server.auth
        if hmac.compare_digest(user, u) & hmac.compare_digest(hash_pw(pw, salt), digest):
            return u
        fails.append(now)
        self.server.fails[ip] = fails
        LOG.write("WARNING", "login failed from %s for %s" % (ip, self.path[:80]))
        return None

    def headers_common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # same-origin, not no-referrer: under no-referrer Chrome sends "Origin: null" on form posts and the same-origin
        # check refused the owner's own clicks; same-origin still sends nothing to other sites
        self.send_header("Referrer-Policy", "same-origin")

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None, cache="no-store"):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.headers_common()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html_page(self, body):
        self.send(200, body, extra={"Content-Security-Policy": CSP})

    def redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.headers_common()
        self.end_headers()

    def file(self, path, ctype=None, cache="private, max-age=86400"):
        """file with HTTP Range support (browsers need it to seek in MP4 clips), streamed in 64 KB chunks"""
        ctype = ctype or CTYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        size = os.path.getsize(path)
        m = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        start, end, partial = 0, size - 1, bool(m and (m.group(1) or m.group(2)))
        if partial:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                return self.send(416, "", "text/plain", {"Content-Range": "bytes */%d" % size})
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache)
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.headers_common()
        self.end_headers()
        if self.command == "HEAD":
            return
        left = end - start + 1
        with open(path, "rb") as f:
            f.seek(start)
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def live(self):
        """MJPEG: annotated frames from facepipe (NVJPG). Touching 'want' tells facepipe someone is watching."""
        if not self.server.live_slots.acquire(blocking=False):
            return self.send(503, "Too many live viewers", "text/plain; charset=utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.headers_common()
            self.end_headers()
            os.makedirs(LIVE_DIR, exist_ok=True)
            want, live, last, tick = os.path.join(LIVE_DIR, "want"), os.path.join(LIVE_DIR, "live.jpg"), 0, 0
            while True:
                now = time.time()
                if now - tick > 3:
                    open(want, "a").close()
                    os.utime(want)
                    tick = now
                try:
                    st = os.stat(live)
                except OSError:
                    time.sleep(0.2)
                    continue
                if st.st_mtime_ns != last and now - st.st_mtime < 5:  # stale frames are not shown as live
                    last = st.st_mtime_ns
                    with open(live, "rb") as f:
                        data = f.read()
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data))
                    self.wfile.write(data + b"\r\n")
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass  # viewer left
        finally:
            self.server.live_slots.release()

    def same_origin(self):
        """POST guard: the browser's Origin (or Referer) must name this host; other sites cannot post here"""
        host = self.headers.get("Host", "")
        src = self.headers.get("Origin") or self.headers.get("Referer") or ""
        return bool(host) and urlparse(src).netloc == host

    def login_required(self):
        return self.send(401, "Login required", "text/plain; charset=utf-8",
                         {"WWW-Authenticate": 'Basic realm="%s", charset="UTF-8"' % SITE})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/static/") and url.path[8:] in STATIC:  # public assets, no data in them
            return self.file(os.path.join(WEB_DIR, url.path[8:]), STATIC[url.path[8:]], cache="public, max-age=3600")
        user = self.user()
        if not user:
            return self.login_required()
        q = parse_qs(url.query)
        if url.path == "/":
            return self.html_page(page_overview())
        if url.path == "/live":
            return self.html_page(page_live((self.headers.get("Host") or "this-device").rsplit(":", 1)[0]))
        if url.path in ("/attendance", "/csv"):
            today = datetime.date.today().isoformat()
            day = q.get("date", [today])[0]
            try:
                if not DATE_RE.match(day):
                    raise ValueError(day)
                datetime.date(*map(int, day.split("-")))
            except ValueError:
                return self.send(400, "Bad date", "text/plain; charset=utf-8")
            if url.path == "/csv":
                return self.send(200, "﻿" + csv_text(day), "text/csv; charset=utf-8",  # BOM: Excel opens UTF-8 names right
                                 {"Content-Disposition": "attachment; filename=attendance-%s.csv" % day})
            return self.html_page(page_attendance(day, today))
        if url.path == "/people":
            return self.html_page(page_people())
        if url.path == "/system":
            return self.html_page(page_system(csrf_token(user), q.get("msg", [""])[0]))
        if url.path == "/live.mjpg":
            return self.live()
        if url.path == "/api/status":
            return self.send(200, json.dumps(status()), "application/json")
        if url.path.startswith("/rec/") and REC_RE.match(url.path[5:]):
            path = os.path.join(REC_DIR, url.path[5:])
            if os.path.exists(path):
                return self.file(path)
        if url.path.startswith("/snap/") and SNAP_RE.match(url.path[6:]):
            path = os.path.join(DATA, "snaps", url.path[6:])
            if os.path.exists(path):
                return self.file(path)
        if url.path.startswith("/photo/"):
            path = first_photo(unquote(url.path[7:]))
            if path:
                return self.file(path)
        self.send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        user = self.user()
        if not user:
            return self.login_required()
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.send(413, "Too large", "text/plain; charset=utf-8")
        form = parse_qs(self.rfile.read(n).decode(errors="replace"))
        token = form.get("csrf", [""])[0]
        if not self.same_origin() or not hmac.compare_digest(token, csrf_token(user)):
            LOG.write("WARNING", "POST %s refused from %s (origin or form token)" % (self.path[:60], self.client_address[0]))
            return self.redirect("/system?msg=denied")
        if urlparse(self.path).path == "/system/service":
            return self.redirect("/system?msg=" + service_action(form.get("action", [""])[0]))
        self.send(404, "Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # no access log (the page polls every few seconds); failed logins and errors are logged

    def log_error(self, fmt, *args):
        LOG.write("ERROR", "%s: %s" % (self.client_address[0], fmt % args))


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, *a, **kw):
        HTTPServer.__init__(self, *a, **kw)
        self.slots = threading.BoundedSemaphore(MAX_REQUESTS)
        self.live_slots = threading.BoundedSemaphore(MAX_LIVE)
        self.fails = {}

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):  # bounded threads: refuse instead of growing without limit
            self.shutdown_request(request)
            return
        try:
            socketserver.ThreadingMixIn.process_request(self, request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            socketserver.ThreadingMixIn.process_request_thread(self, request, client_address)
        finally:
            self.slots.release()


def selftest():
    ev = [{"time": "2026-11-06T09:00:00", "ts": 1, "name": "a_b", "score": 0.6, "snap": "s1"},
          {"time": "2026-11-06T09:05:00", "ts": 2, "name": "unknown", "score": 0.2, "snap": "s2"},
          {"time": "2026-11-06T17:00:00", "ts": 3, "name": "a_b", "score": 0.7, "snap": "s3"}]
    people, unknown = attendance(ev)
    p = people["a_b"]
    assert p["first"].endswith("09:00:00") and p["last"].endswith("17:00:00"), p
    assert hms("2026-11-06T17:05:09") == "05:05:09 PM" and hm("2026-11-06T00:30:00") == "12:30 AM"
    assert hm("2026-11-06T12:00:00") == "12:00 PM" and hms("") == ""
    t = datetime.datetime(2026, 11, 6, 21, 41, 7, 123456)  # a Friday
    assert log_stamp(t) == "[06-11-2026] 09:41:07.123 PM, Friday", log_stamp(t)
    import shutil, tempfile
    root = tempfile.mkdtemp()
    try:
        lg = DailyLog("web", root)
        lg.write("INFO", "day one", t)
        lg.write("INFO", "next day", t + datetime.timedelta(days=1))  # midnight passed: day one becomes a backup
        nov = os.path.join(root, "2026", "November")
        assert open(os.path.join(nov, "web.log.06-Nov-2026")).read().endswith("[INFO] day one\n")
        assert "07-11-2026" in open(os.path.join(nov, "web.log")).read()
        lg.write("INFO", "December", datetime.datetime(2026, 12, 1, 0, 0, 1))  # new month folder
        assert os.path.exists(os.path.join(nov, "web.log.07-Nov-2026"))
        assert os.path.exists(os.path.join(root, "2026", "December", "web.log"))
        lg.f.close()
    finally:
        shutil.rmtree(root)
    assert p["sightings"] == 2 and p["best"] == 0.7 and p["snap"] == "s1", p
    assert len(unknown) == 1 and display_name("a_b") == "A B"
    assert SNAP_RE.match("2026-11-06/090000_3.jpg") and not SNAP_RE.match("../web_auth")
    assert not DATE_RE.match("2026-11-06/../../x")
    assert first_photo("../../etc") is None  # not an enrolled name
    assert SNAP_RE.match("2026-11-06/090000_3.bmp")
    assert REC_RE.match("2026-11-06/090000_clip.mp4") and REC_RE.match("2026-11-06/090000_7.jpg")
    assert not REC_RE.match("2026-11-06/../../etc/passwd") and not REC_RE.match("x/090000_clip.mp4")
    assert "Frame" in evidence({"frame": "2026-11-06/090000_7.jpg"}) and "Clip" not in evidence({"clip": "../x.mp4"})
    salt = b"x" * 16
    assert hmac.compare_digest(hash_pw("secret12", salt), hash_pw("secret12", salt))
    assert service_action("rm -rf") == "denied" and camera_label() in camera_label()
    assert csrf_token("a") == csrf_token("a") != csrf_token("b")
    selftest_http()
    print("selftest ok")


def selftest_http():
    """a real server on a free port: login, pages, CSP, static files, Range, XSS escaping, POST guards"""
    import urllib.request, urllib.error
    day = datetime.date.today().isoformat()
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "events-%s.jsonl" % day), "a") as f:
        f.write(json.dumps({"time": day + "T13:05:00", "ts": 1, "name": "<script>x</script>", "score": 0.7,
                            "snap": "%s/130500_1.bmp" % day}) + chr(10))
    srv = Server(("127.0.0.1", 0), Handler)
    salt = b"s" * 16
    srv.auth = ("admin", salt, hash_pw("secret123", salt))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    good = "Basic " + base64.b64encode(b"admin:secret123").decode()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None  # report the 303 itself
    opener = urllib.request.build_opener(NoRedirect)

    def get(path, auth=good, headers=None, data=None):
        h = dict(headers or {})
        if auth:
            h["Authorization"] = auth
        req = urllib.request.Request(base + path, data=data, headers=h)
        try:
            r = opener.open(req, timeout=10)
            return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
    assert get("/", auth=None)[0] == 401
    assert get("/", auth="Basic " + base64.b64encode(b"admin:wrong").decode())[0] == 401
    assert get("/static/app.css", auth=None)[0] == 200 and get("/static/../web_auth", auth=None)[0] == 401
    for p in ("/", "/live", "/attendance", "/attendance?date=" + day, "/people", "/system"):
        code, hd, body = get(p)
        assert code == 200 and "script-src 'self'" in hd.get("Content-Security-Policy", ""), (p, code)
        assert b"aria-current='page'" in body and b"<script>x" not in body, p
    assert b"&lt;script&gt;x&lt;/script&gt;" in get("/attendance")[2]
    assert get("/attendance?date=2026-02-30")[0] == 400 and get("/csv?date=../x")[0] == 400
    code, hd, body = get("/static/app.js", headers={"Range": "bytes=0-9"})
    assert code == 206 and len(body) == 10 and hd["Content-Range"].startswith("bytes 0-9/")
    host = "127.0.0.1:%d" % srv.server_address[1]
    form = ("csrf=%s&action=nothing" % csrf_token("admin")).encode()
    # no Origin: refused; wrong token: refused; right token and origin, unknown action: refused by the whitelist
    assert get("/")[1].get("Referrer-Policy") == "same-origin"  # under no-referrer Chrome posts "Origin: null"
    for hd, d in (({}, form), ({"Origin": "http://" + host}, b"csrf=bad&action=start"), ({"Origin": "null"}, form),
                  ({"Origin": "http://evil.example"}, form), ({"Origin": "http://" + host}, form)):
        code, h2, _ = get("/system/service", headers=hd, data=d)
        assert code == 303 and h2["Location"] == "/system?msg=denied", (hd, code, h2.get("Location"))
    assert get("/system/service", data=b"x" * (MAX_BODY + 1), headers={"Origin": "http://" + host})[0] == 413
    srv.shutdown()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--set-password"] and len(sys.argv) == 3:
        set_password(sys.argv[2])
    elif sys.argv[1:2] == ["--set-rtsp-password"] and len(sys.argv) == 3:
        set_rtsp_password(sys.argv[2])
    elif sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        if not os.path.exists(AUTH_FILE):
            sys.exit("no login set: run  python3 %s --set-password <user>" % sys.argv[0])
        srv = Server(("0.0.0.0", PORT), Handler)
        srv.auth = load_auth()
        LOG.write("INFO", "web %s: attendance page on port %d, data %s" % (VERSION, PORT, DATA))
        srv.serve_forever()
