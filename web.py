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
import base64, cgi, csv, datetime, fcntl, glob, getpass, hashlib, hmac, html, io, json, os, re, secrets, shutil, socket, socketserver, sqlite3, string, subprocess, sys, threading, time
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
PUBLIC = string.Template(open(os.path.join(WEB_DIR, "public.html"), encoding="utf-8").read())
ICONS = {  # 24x24 stroke icons for the sidebar (inline SVG: no files, no fonts)
    "overview": "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
    "live": "M23 7l-7 5 7 5zM3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
    "attendance": "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM16 2v4M8 2v4M3 10h18M8 15l2.5 2.5L16 13",
    "people": "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
    "cameras": "M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    "system": "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"}
PAGES = [("overview", "/", "Overview"), ("live", "/live", "Live"), ("attendance", "/attendance", "Attendance"),
         ("people", "/people", "People"), ("cameras", "/cameras", "Cameras"), ("system", "/system", "System")]
CSP = ("default-src 'none'; img-src 'self' data: blob:; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
FLASH = {  # fixed messages only: the query string never reflects text into the page
    "started": ("ok", "Recognition service started."), "stopped": ("ok", "Recognition service stopped. It stays stopped, also across updates, until you start it."),
    "restarted": ("ok", "Recognition service restarted."), "busy": ("bad", "An update or another action is running; try again in a minute."),
    "failed": ("bad", "The action failed; see the log below."),
    "approving": ("ok", "Enrolling the approved photos; this takes about a minute. Refresh to see the result."),
    "rejected": ("ok", "Registration rejected; its photos were deleted."), "removed": ("ok", "Removed from recognition; past attendance is kept."),
    "remove_failed": ("bad", "Could not remove the person; nothing was changed (see the web log)."),
    "cam_saved": ("ok", "Camera setup saved; recognition restarted with it."), "cam_reverted": ("ok", "Previous camera setup restored; recognition restarted."),
    "cam_saved_stopped": ("ok", "Camera setup saved. Recognition is stopped; start it on the System page to use it."),
    "cam_saved_inactive": ("ok", "Saved. Network cameras are not in use now; choose Network cameras above to use them."),
    "cam_none": ("ok", "No camera is enabled, so recognition was stopped."), "cam_none_enabled": ("bad", "Enable a network camera first (in the list below)."),
    "cam_nousb": ("bad", "That USB camera is not connected."), "cam_bad": ("bad", "Check the name and the address (it must start with rtsp://)."),
    "cam_added": ("ok", "Camera added."), "cam_removed": ("ok", "Camera removed."), "cam_noprev": ("bad", "There is no earlier camera setup to restore."),
    "cam_test_ok": ("ok", "The camera answered."), "cam_test_bad": ("bad", "The camera test failed; see the line under its name."), "denied": ("bad", "The request was refused (expired form or another site).")}
esc = html.escape


def layout(page_id, title, body, flash="", refresh=False):
    st = status()
    nav = "".join("<a href='%s'%s><svg viewBox='0 0 24 24' aria-hidden='true'><path d='%s'/></svg>%s</a>"
                  % (href, " aria-current='page'" if pid == page_id else "", ICONS[pid], label) for pid, href, label in PAGES)
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
<dl class="kv"><dt>Software</dt><dd>facepipe %s (web %s)</dd><dt>microSD card</dt><dd>%s <span class="muted">(system, app, data and logs)</span></dd>
<dt>Recordings</dt><dd>%s <span class="muted">(clips and evidence; a volume on the microSD card)</span></dd>
<dt>Last update check</dt><dd>%s</dd></dl></section>
<section aria-labelledby="h-log" class="noprint"><div class="sh"><h2 id="h-log">Recent log</h2><span class="count">facepipe.log</span></div>
<pre class="log">%s</pre></section>""" % (
        st["state"], esc(st["detail"]), esc(active), "running" if desired != "stopped" else "stopped (kept across updates)",
        "%.1f fps" % st["fps"] if "fps" in st else "&ndash;", "%.1f" % st["det_fps"] if "det_fps" in st else "&ndash;",
        st.get("frames", "&ndash;"), esc(camera_label()), buttons, esc(str(st.get("version", "?"))), esc(VERSION), esc(disk(HERE)),
        esc(disk(REC_DIR)), esc(upd[-1]) if upd else "no update has run yet", esc("\n".join(log_lines)) or "no log yet")
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
    profile = [f for f in files if os.path.splitext(f)[0].lower() == "profile"]  # the person's chosen picture, if any
    return os.path.join(folder, (profile or files)[0]) if files else None


# ---------- registration by invitation (D-033, D-034) ----------
# The admin makes a single-use link (48 h). The person submits a name, 2-5 photos and consent; each photo is decoded and
# re-encoded on the CPU by `facepipe-enroll --check` and kept under data/registrations/<id>/ (never served publicly, never
# in gallery/). Nothing is enrolled until the admin approves: approval builds a candidate gallery in a staging folder,
# enrolls it there, checks it, and only then publishes (photos into gallery/, the new gallery.tsv by rename).
DB_PATH = os.path.join(DATA, "web.db")
PEOPLE_JSON = os.path.join(DATA, "people.json")       # person key -> display name (new keys are opaque: p_xxxxxxxx)
REG_DIR = os.path.join(DATA, "registrations")
STAGE_DIR = os.path.join(DATA, "stage")
ENROLL_BIN = os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "facepipe-enroll")
INVITE_HOURS, PENDING_DAYS = 48, 7                    # owner: link valid 48 h; unreviewed photos deleted after 7 days
MIN_PHOTOS, MAX_PHOTOS, MAX_PHOTO = 2, 5, 4 * 1024 * 1024
MAX_UPLOAD = MAX_PHOTOS * MAX_PHOTO + 64 * 1024
MIN_FREE_MB = 600                                     # enrollment loads TensorRT beside the live pipeline
CONSENT_VERSION = "2026-11-07"
CONSENT_TEXT = ("I agree that this device stores these photos and a numeric face template made from them, to recognize me "
                "and record my attendance. Everything stays on this device. I can ask the administrator to remove me at any time.")
NAME_RE = re.compile(r"^[^\x00-\x1f<>\"'`]{2,60}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_db_lock = threading.Lock()


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS invites (hash TEXT PRIMARY KEY, created REAL, expires REAL, used REAL, person TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, invite TEXT, name TEXT, person TEXT, created REAL,"
              " consent TEXT, photos INTEGER, status TEXT, note TEXT, decided REAL)")
    return c


def people_names():
    try:
        with open(PEOPLE_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_people_names(names):
    tmp = PEOPLE_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PEOPLE_JSON)


def display_name(n):
    shown = people_names().get(n)
    return shown if shown else n.replace("_", " ").strip().title()


def token_hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def create_invite(person=None):
    t = secrets.token_urlsafe(16)  # 128 bits
    now = time.time()
    with _db_lock, db() as c:
        c.execute("INSERT INTO invites VALUES (?,?,?,?,?)", (token_hash(t), now, now + INVITE_HOURS * 3600, None, person))
    LOG.write("INFO", "invitation created%s" % (" to update %s" % person if person else ""))
    return t


def valid_invite(t):
    if not t or len(t) > 64:
        return None
    with db() as c:
        r = c.execute("SELECT * FROM invites WHERE hash=?", (token_hash(t),)).fetchone()
    return r if r and r["used"] is None and r["expires"] > time.time() else None


def cleanup_registrations():
    """owner's retention (D-034): unreviewed submissions after 7 days; expired, unused invitations"""
    now = time.time()
    with _db_lock, db() as c:
        old = [r["id"] for r in c.execute("SELECT id FROM submissions WHERE status IN ('pending','failed') AND created<?",
                                          (now - PENDING_DAYS * 86400,))]
        for sid in old:
            shutil.rmtree(os.path.join(REG_DIR, sid), ignore_errors=True)
            c.execute("UPDATE submissions SET status='expired', note='photos deleted after 7 days unreviewed', decided=? WHERE id=?",
                      (now, sid))
        c.execute("DELETE FROM invites WHERE used IS NULL AND expires<?", (now,))
    for sid in old:
        LOG.write("INFO", "registration %s: photos deleted after %d days unreviewed" % (sid, PENDING_DAYS))


def check_photo(raw, out):
    """CPU-only decode and canonical re-encode by the enroll binary; returns an error text or None"""
    tmp = out + ".upload"
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        r = subprocess.run([ENROLL_BIN, "--check", tmp, out], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                           env=dict(os.environ, CV_IO_MAX_IMAGE_PIXELS="40000000"))
    except (OSError, subprocess.SubprocessError) as e:
        return "could not check the photo (%s)" % e.__class__.__name__
    finally:
        os.remove(tmp)
    return None if r.returncode == 0 else (r.stderr.decode(errors="replace").strip() or "not a usable image")


def submit_registration(invite, name, photos, consent):
    """photos: list of bytes. Returns (submission id, None) or (None, error message)."""
    if not consent:
        return None, "Please read and accept the consent statement."
    if not invite["person"] and not NAME_RE.match(name.strip()):
        return None, "Please enter your full name (2 to 60 characters)."
    if not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
        return None, "Please choose %d to %d photos." % (MIN_PHOTOS, MAX_PHOTOS)
    if any(len(p) > MAX_PHOTO for p in photos):
        return None, "Each photo must be under %d MB." % (MAX_PHOTO // 1048576)
    sid = secrets.token_hex(6)
    folder = os.path.join(REG_DIR, sid)
    os.makedirs(folder, mode=0o700)
    for i, raw in enumerate(photos, 1):
        err = check_photo(raw, os.path.join(folder, "%d.jpg" % i))
        if err:
            shutil.rmtree(folder, ignore_errors=True)
            return None, "Photo %d: %s." % (i, err)
    now = time.time()
    with _db_lock, db() as c:
        # single use, also under a race: only the request that marks the invitation used may store the submission
        if c.execute("UPDATE invites SET used=? WHERE hash=? AND used IS NULL", (now, invite["hash"])).rowcount != 1:
            shutil.rmtree(folder, ignore_errors=True)
            return None, "This invitation link has already been used."
        c.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (sid, invite["hash"], name.strip() or display_name(invite["person"]), invite["person"], now, CONSENT_VERSION,
                   len(photos), "pending", "", None))
    LOG.write("INFO", "registration %s submitted (%d photos)%s" % (sid, len(photos),
                                                                    " to update %s" % invite["person"] if invite["person"] else ""))
    return sid, None


def mem_available_mb():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def gallery_keys(tsv):
    keys = set()
    try:
        with open(tsv, encoding="utf-8") as f:
            for line in f:
                k = line.split("\t", 1)[0]
                if k:
                    keys.add(k)
    except OSError:
        pass
    return keys


def stage_gallery(work, skip=None):
    """a copy of gallery/ in work/gallery made of hard links (no extra space), without person `skip`"""
    g = os.path.join(work, "gallery")
    os.makedirs(g)
    for person in sorted(os.listdir(GALLERY_DIR)) if os.path.isdir(GALLERY_DIR) else []:
        src = os.path.join(GALLERY_DIR, person)
        if person == skip or not os.path.isdir(src):
            continue
        os.makedirs(os.path.join(g, person))
        for f in os.listdir(src):
            try:
                os.link(os.path.join(src, f), os.path.join(g, person, f))
            except OSError:
                shutil.copy2(os.path.join(src, f), os.path.join(g, person, f))
    return g


def run_enroll(work):
    """enroll the staged gallery into work/gallery.tsv; returns an error text or None"""
    if mem_available_mb() < MIN_FREE_MB:
        return "not enough free memory to enroll now (%d MB); try again later" % mem_available_mb()
    try:
        r = subprocess.run([ENROLL_BIN, os.path.join(work, "gallery")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=900, env=dict(os.environ, FACEPIPE_GALLERY=os.path.join(work, "gallery.tsv")))
    except (OSError, subprocess.SubprocessError) as e:
        return "enrollment could not run (%s)" % e.__class__.__name__
    if r.returncode != 0:
        last = r.stderr.decode(errors="replace").strip().splitlines()
        return "enrollment failed: %s" % (last[-1] if last else "exit %d" % r.returncode)
    return None


def publishing():
    """the update lock: deploys, service actions and gallery changes never overlap (D-033)"""
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    lk = open(LOCK, "a")
    for _ in range(120):
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lk
        except OSError:
            time.sleep(1)
    lk.close()
    return None


_enroll_lock = threading.Lock()  # one enrollment job at a time


def approve(sid):
    """runs in a worker thread; stages, enrolls, checks and publishes, or leaves the active gallery untouched"""
    with _enroll_lock:
        with db() as c:
            s = c.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        if not s or s["status"] not in ("enrolling",):
            return
        work = os.path.join(STAGE_DIR, sid)
        shutil.rmtree(work, ignore_errors=True)
        before = gallery_keys(GALLERY_TSV)
        key = s["person"] or "p_" + secrets.token_hex(4)
        note, ok = "", False
        try:
            g = stage_gallery(work)
            os.makedirs(os.path.join(g, key), exist_ok=True)
            new_files = []
            for f in sorted(os.listdir(os.path.join(REG_DIR, sid))):
                name = "reg_%s_%s" % (sid, f)
                shutil.copy2(os.path.join(REG_DIR, sid, f), os.path.join(g, key, name))
                new_files.append(name)
            err = run_enroll(work)
            after = gallery_keys(os.path.join(work, "gallery.tsv"))
            if not err and key not in after:
                err = "no face was found in any of the photos"
            if not err and not before <= after:
                err = "enrolling would drop %s; nothing was changed" % ", ".join(sorted(before - after))
            if err:
                note = err
            else:
                lk = publishing()
                if not lk:
                    note = "an update kept the gallery busy; nothing was changed, approve again"
                else:
                    with lk:
                        dest = os.path.join(GALLERY_DIR, key)
                        os.makedirs(dest, exist_ok=True)
                        for name in new_files:
                            os.replace(os.path.join(g, key, name), os.path.join(dest, name))
                        os.replace(os.path.join(work, "gallery.tsv"), GALLERY_TSV)  # live recognition reloads it
                        if not s["person"]:
                            names = people_names()
                            names[key] = s["name"]
                            save_people_names(names)
                    ok = True
        except OSError as e:
            note = "could not stage the gallery (%s)" % e
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with _db_lock, db() as c:
            c.execute("UPDATE submissions SET status=?, note=?, person=?, decided=? WHERE id=?",
                      ("approved" if ok else "failed", note, key, time.time(), sid))
        if ok:
            shutil.rmtree(os.path.join(REG_DIR, sid), ignore_errors=True)
        LOG.write("INFO" if ok else "ERROR", "registration %s %s%s" % (sid, "approved: enrolled as " + key if ok else "failed: ",
                                                                         "" if ok else note))


def remove_person(key):
    """remove from recognition: a gallery without the person, enrolled and published; past attendance stays"""
    with _enroll_lock:
        work = os.path.join(STAGE_DIR, "remove-" + secrets.token_hex(4))
        try:
            stage_gallery(work, skip=key)
            err = run_enroll(work)
            remaining = gallery_keys(GALLERY_TSV) - {key}
            if not err and not remaining <= gallery_keys(os.path.join(work, "gallery.tsv")):
                err = "the new gallery would lose other people; nothing was changed"
            if err:
                LOG.write("ERROR", "removing %s failed: %s" % (key, err))
                return False
            lk = publishing()
            if not lk:
                return False
            with lk:
                os.replace(os.path.join(work, "gallery.tsv"), GALLERY_TSV)
                shutil.rmtree(os.path.join(GALLERY_DIR, key), ignore_errors=True)
                names = people_names()
                names.pop(key, None)
                save_people_names(names)
            LOG.write("INFO", "person %s removed from recognition (photos deleted; past attendance kept)" % key)
            return True
        finally:
            shutil.rmtree(work, ignore_errors=True)


def reject(sid):
    with _db_lock, db() as c:
        c.execute("UPDATE submissions SET status='rejected', note='photos deleted', decided=? WHERE id=? AND status IN ('pending','failed')",
                  (time.time(), sid))
    shutil.rmtree(os.path.join(REG_DIR, sid), ignore_errors=True)  # owner: rejected photos are deleted at once
    LOG.write("INFO", "registration %s rejected; photos deleted" % sid)


SHOWN = {}  # one-time display of a new invitation link: nonce -> (link, expires)


def public_page(title, body):
    """pages for people without the admin login (registration): no navigation, no device status"""
    return PUBLIC.substitute(title=esc(title), site=esc(SITE), body=body, version=esc(VERSION))


def page_register(invite, token="", error="", name=""):
    if not invite:
        return public_page("Registration", "<h1>Registration</h1><section><p class='body'>This invitation link is not valid any "
                           "more: it was used already or it is older than %d hours. Please ask the administrator for a new "
                           "one.</p></section>" % INVITE_HOURS)
    update = invite["person"]
    who = ("<p class='sub'>New photos for <b>%s</b>. They replace nothing until the administrator approves them.</p>"
           % esc(display_name(update))) if update else \
        "<p class='sub'>Register your face for attendance. The administrator reviews every registration before it is used.</p>"
    name_field = "" if update else (
        "<label class='field'><span>Full name</span><input name='name' required maxlength='60' autocomplete='name' value='%s'></label>"
        % esc(name))
    body = """<h1>%s</h1>%s
<div id="regerr" class="flash bad"%s role="alert">%s</div>
<section><form id="regform" class="body form" method="post" action="/register" enctype="multipart/form-data">
<input type="hidden" name="t" value="%s">%s
<div class="field"><span>Photos (%d to %d)</span>
<label class="drop" id="drop"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>
<span><b>Choose photos</b> or drop them here</span><small class="muted">JPEG or PNG; shrunk on your device before sending</small>
<input name="photos" type="file" accept="image/jpeg,image/png" multiple required></label>
<div class="previews" id="previews"></div>
<small class="muted">Clear photos of your face only, in good light, without sunglasses: one from the front, one turned slightly left, one slightly right.</small></div>
<label class="check"><input type="checkbox" name="consent" value="yes" required><span>%s</span></label>
<button class="btn primary" type="submit">Send registration</button>
</form></section>""" % ("Update your photos" if update else "Registration", who, "" if error else " hidden", esc(error),
                        esc(token), name_field, MIN_PHOTOS, MAX_PHOTOS, esc(CONSENT_TEXT))
    return public_page("Registration", body)


def page_register_done():
    return public_page("Registration sent", "<h1>Thank you</h1><section><p class='body'>Your registration was sent. The "
                       "administrator will review it; once approved, the device recognizes you. You can close this page.</p></section>")


def page_people(csrf, host, show="", flash=""):
    cleanup_registrations()
    gal = enrolled()
    today = datetime.date.today().isoformat()
    people, _ = attendance(read_events(today))

    def form(action, label, cls, fields, confirm=None, title=None):
        conf = (" data-confirm='%s' data-title='%s' data-ok='%s' data-kind='%s'" % (esc(confirm), esc(title), esc(label),
                "danger" if "danger" in cls else "primary")) if confirm else ""
        hidden = "".join("<input type='hidden' name='%s' value='%s'>" % (k, esc(v)) for k, v in fields.items())
        return ("<form method='post' action='/people/%s'%s><input type='hidden' name='csrf' value='%s'>%s"
                "<button class='btn %s' type='submit'>%s</button></form>" % (action, conf, csrf, hidden, cls, label))

    link = ""
    if show in SHOWN and SHOWN[show][1] > time.time():
        link = """<section class="invite"><div class="sh"><h2>Invitation link</h2><span class="count">single use, valid %d hours, shown only now</span></div>
<div class="body"><div class="copy"><input id="invlink" readonly value="%s" aria-label="Invitation link"><button class="btn primary" type="button" id="copylink">Copy</button></div>
<p class="muted">Send it to the person. They open it on any phone or computer on this network, add their photos and consent; you approve it below.</p></div></section>""" % (
            INVITE_HOURS, esc("http://%s/register?t=%s" % (host, SHOWN[show][0])))
    with db() as c:
        pending = c.execute("SELECT * FROM submissions WHERE status IN ('pending','enrolling','failed') ORDER BY created").fetchall()
        recent = c.execute("SELECT * FROM submissions WHERE status IN ('approved','rejected','expired') ORDER BY decided DESC LIMIT 8").fetchall()
    cards = []
    for s in pending:
        thumbs = "".join("<img src='/pending/%s/%d.jpg' alt='Photo %d' loading='lazy'>" % (s["id"], i, i)
                         for i in range(1, s["photos"] + 1))
        state = {"pending": "<span class='badge out'>Waiting for you</span>", "enrolling": "<span class='badge in'>Enrolling&hellip;</span>",
                 "failed": "<span class='badge bad'>Failed</span>"}[s["status"]]
        acts = "" if s["status"] == "enrolling" else (
            form("approve", "Approve", "primary", {"id": s["id"]}) +
            form("reject", "Reject", "danger", {"id": s["id"]}, "The photos are deleted at once.", "Reject this registration?"))
        cards.append("""<div class="sub-card"><div class="sub-head"><div><div class="n">%s</div><div class="m">%s &middot; sent %s %s</div>%s%s</div>
<div class="sub-acts">%s</div></div><div class="thumbs">%s</div></div>""" % (
            esc(s["name"]), "update of %s" % esc(display_name(s["person"])) if s["person"] else "new person",
            datetime.datetime.fromtimestamp(s["created"]).strftime("%d %b"), hm(datetime.datetime.fromtimestamp(s["created"]).isoformat()),
            state, "<div class='m bad-text'>%s</div>" % esc(s["note"]) if s["note"] else "", acts, thumbs))
    pend_html = "<div class='subs'>%s</div>" % "".join(cards) if cards else "<p class='empty'>No registrations waiting.</p>"
    hist = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td class='muted'>%s</td></tr>" % (
        esc(r["name"]), esc(r["status"]), hms(datetime.datetime.fromtimestamp(r["decided"] or r["created"]).isoformat()),
        esc(r["note"] or "")) for r in recent)
    person_cards = "".join(
        "<div class='person' data-name='%s'>%s<div class='grow'><div class='n'>%s</div><div class='m'>%d photo%s enrolled</div>%s</div>"
        "<div class='person-acts'>%s%s</div></div>"
        % (esc(display_name(n).lower()), avatar(n, gal), esc(display_name(n)), c, "" if c == 1 else "s",
           "<span class='badge in'>In today, %s</span>" % hm(people[n]["first"]) if n in people else "<span class='badge out'>Not seen today</span>",
           form("invite", "New photos", "", {"person": n}),
           form("remove", "Remove", "danger", {"person": n},
                "Their photos and face templates are deleted; past attendance records stay.", "Remove %s?" % display_name(n)))
        for n, c in sorted(gal.items(), key=lambda kv: display_name(kv[0]).lower()))
    body = """<h1>People</h1><p class="sub">Everyone the device recognizes, and registrations waiting for your approval.</p>
<div class="toolbar"><span class="grow"></span>%s</div>%s
<section aria-labelledby="h-pend"><div class="sh"><h2 id="h-pend">Waiting for approval</h2><span class="count">%d</span></div>%s</section>
<section aria-labelledby="h-people"><div class="sh"><h2 id="h-people">Enrolled</h2><span class="count">%d</span>
 <span class="end"><label class="sr" for="q">Search names</label><input id="q" type="search" placeholder="Search names" autocomplete="off"></span></div>%s</section>
%s""" % (form("invite", "Invite someone", "primary", {}), link, len(pending), pend_html, len(gal),
         "<div class='people'>%s</div>" % person_cards if person_cards else "<p class='empty'>No one is enrolled yet.</p>",
         "<section aria-labelledby='h-hist'><div class='sh'><h2 id='h-hist'>Recent decisions</h2></div><table><thead><tr><th scope='col'>Name</th>"
         "<th scope='col'>Result</th><th scope='col'>When</th><th scope='col'>Note</th></tr></thead><tbody>%s</tbody></table></section>" % hist if hist else "")
    return layout("people", "People", body, flash=flash)


# ---------- cameras (D-034): how the device is connected; several network cameras, one USB or one CSI camera ----------
# data/cameras.json (0600) holds the whole setup, including RTSP logins; data/cameras.active (0600) is what facepipe reads:
# "<uri> <width> <height>". The previous pair is kept as *.prev for Revert. The website never shows an RTSP password.
CAM_JSON = os.path.join(DATA, "cameras.json")
CAM_ACTIVE = os.path.join(DATA, "cameras.active")
MAX_ACTIVE_RTSP = 1   # two at once comes with the next release
SIZES = {"1920x1080": (1920, 1080), "1280x720": (1280, 720)}
RTSP_RE = re.compile(r"^rtsps?://[^\s'\"<>\\]+$")
CAM_NAME_RE = re.compile(r"^[^\x00-\x1f<>\"'`]{1,40}$")


def env_uri():
    try:
        for line in open(os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "facepipe.env"), encoding="utf-8"):
            if line.startswith("FACEPIPE_URI="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def load_cameras():
    """the saved setup, or one derived from FACEPIPE_URI the first time"""
    try:
        with open(CAM_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    uri = env_uri()
    cfg = {"source": "csi", "csi": "0", "usb": "/dev/video0", "size": "1920x1080", "rtsp": []}
    if uri.startswith("csi:"):
        cfg.update(source="csi", csi=uri[4:] or "0")
    elif uri.startswith("usb:"):
        cfg.update(source="usb", usb=uri[4:], size="1280x720")
    elif RTSP_RE.match(uri):
        cfg.update(source="rtsp", rtsp=[{"id": secrets.token_hex(4), "name": "Camera 1", "url": uri, "enabled": True}])
    return cfg


def masked(url):
    """rtsp://user:•••@host:port/path: the password never reaches a page"""
    u = urlparse(url)
    if u.password is None:
        return url
    return url.replace("%s:%s@" % (u.username, u.password), "%s:\u2022\u2022\u2022@" % u.username, 1)


def with_login(url, user, pw):
    """put a username and password into an RTSP address (percent-encoded)"""
    u = urlparse(url)
    if not user:
        return url
    host = u.netloc.rsplit("@", 1)[-1]
    return url.replace(u.netloc, "%s:%s@%s" % (quote(user, safe=""), quote(pw, safe=""), host), 1)


def video_devices():
    """USB cameras only: CSI sensors also appear as /dev/videoN, named vi-output by the Tegra driver"""
    out = []
    for d in sorted(glob.glob("/sys/class/video4linux/video*")):
        try:
            name = open(os.path.join(d, "name")).read().strip()
        except OSError:
            continue
        if not name.startswith("vi-output"):
            out.append(("/dev/" + os.path.basename(d), name))
    return out


def active_line(cfg):
    w, h = SIZES.get(cfg.get("size"), (1920, 1080))
    if cfg["source"] == "csi":
        return "csi:%s %d %d" % (cfg["csi"], w, h)
    if cfg["source"] == "usb":
        return "usb:%s %d %d" % (cfg["usb"], w, h)
    on = [c for c in cfg["rtsp"] if c.get("enabled")]
    return "%s %d %d" % (on[0]["url"], w, h) if on else ""


def write_private(path, text):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def apply_cameras(cfg):
    """save, keep the previous setup for Revert, restart recognition if it is meant to run; returns a FLASH key"""
    line = active_line(cfg)
    for p in (CAM_JSON, CAM_ACTIVE):
        if os.path.exists(p):
            shutil.copy2(p, p + ".prev")
    write_private(CAM_JSON, json.dumps(cfg, indent=1))
    if not line:  # no camera enabled: recognition stops and stays stopped until one is enabled
        write_private(CAM_ACTIVE, "")
        return "cam_none" if service_action("stop") == "stopped" else "failed"
    write_private(CAM_ACTIVE, line + "\n")
    LOG.write("INFO", "camera setup changed: %s" % (cfg["source"] if cfg["source"] != "rtsp" else "network camera"))
    if read_desired() == "stopped":
        return "cam_saved_stopped"
    return "cam_saved" if service_action("restart") == "restarted" else "failed"


def revert_cameras():
    if not os.path.exists(CAM_JSON + ".prev"):
        return "cam_noprev"
    for p in (CAM_JSON, CAM_ACTIVE):
        if os.path.exists(p + ".prev"):
            os.replace(p + ".prev", p)
    if read_desired() == "stopped":
        return "cam_saved_stopped"
    return "cam_reverted" if service_action("restart") == "restarted" else "failed"


def probe_rtsp(url):
    """can the device open this stream? (gst-discoverer, 10 s); returns (ok, short text without the address)"""
    try:
        r = subprocess.run(["gst-discoverer-1.0", "-t", "10", url], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "could not test (%s)" % e.__class__.__name__
    text = r.stdout.decode(errors="replace")
    v = re.search(r"video(?:\(\d+\))?: ([^\n]+)", text)
    if r.returncode == 0 and v:
        return True, v.group(1).strip()[:80]
    if "nauthorized" in text or "401" in text:
        return False, "the camera refused the login"
    if "timeout" in text.lower() or "Timeout" in text:
        return False, "no answer from the camera within 10 s"
    return False, "the stream could not be opened"


def page_cameras(csrf, flash="", test=None):
    cfg = load_cameras()
    st = status()

    def form(action, label, cls, fields, confirm=None, title=None):
        conf = (" data-confirm='%s' data-title='%s' data-ok='%s' data-kind='%s'" % (esc(confirm), esc(title), esc(label),
                "danger" if "danger" in cls else "primary")) if confirm else ""
        hidden = "".join("<input type='hidden' name='%s' value='%s'>" % (k, esc(v)) for k, v in fields.items())
        return ("<form method='post' action='/cameras/%s'%s><input type='hidden' name='csrf' value='%s'>%s"
                "<button class='btn %s' type='submit'>%s</button></form>" % (action, conf, csrf, hidden, cls, label))

    usb_opts = "".join("<option value='%s'%s>%s (%s)</option>" % (esc(dev), " selected" if dev == cfg["usb"] else "", esc(dev), esc(name))
                       for dev, name in video_devices()) or "<option value='%s'>%s (not detected)</option>" % (esc(cfg["usb"]), esc(cfg["usb"]))
    size_opts = "".join("<option value='%s'%s>%s</option>" % (k, " selected" if k == cfg.get("size") else "", k.replace("x", " x "))
                        for k in SIZES)

    def radio(v, title, text):
        return ("<label class='choice'><input type='radio' name='source' value='%s'%s><span><b>%s</b><small>%s</small></span></label>"
                % (v, " checked" if cfg["source"] == v else "", title, text))
    rows = []
    for c in cfg["rtsp"]:
        res = ""
        if test and test[0] == c["id"]:
            res = "<div class='m %s'>%s</div>" % ("ok-text" if test[1] else "bad-text", esc(test[2]))
        rows.append("<tr><td><b>%s</b>%s</td><td class='mono'>%s</td><td>%s</td><td class='acts'>%s%s%s</td></tr>" % (
            esc(c["name"]), res, esc(masked(c["url"])),
            "<span class='badge in'>Enabled</span>" if c.get("enabled") else "<span class='badge out'>Disabled</span>",
            form("toggle", "Disable" if c.get("enabled") else "Enable", "", {"id": c["id"]}),
            form("test", "Test", "", {"id": c["id"]}),
            form("remove", "Remove", "danger", {"id": c["id"]}, "Its address and login are deleted from the device.",
                 "Remove %s?" % c["name"])))
    table = ("<table><thead><tr><th scope='col'>Name</th><th scope='col'>Address</th><th scope='col'>State</th>"
             "<th scope='col'>Actions</th></tr></thead><tbody>%s</tbody></table>" % "".join(rows)) if rows else \
        "<p class='empty'>No network cameras yet; add one below.</p>"
    current = {"csi": "CSI camera, sensor %s" % cfg["csi"], "usb": "USB camera %s" % cfg["usb"]}.get(cfg["source"]) or \
        ", ".join(c["name"] for c in cfg["rtsp"] if c.get("enabled")) or "no network camera enabled"
    body = """<h1>Cameras</h1><p class="sub">Choose how this device gets its video. Network cameras can be in different places, so several can be set up; a USB or CSI camera is attached to the device itself.</p>
<div class="cards"><div class="card"><div class="k">In use now</div><div class="v small">%s</div><div class="s">%s</div></div></div>
<section aria-labelledby="h-src"><div class="sh"><h2 id="h-src">Connection</h2></div>
<form method="post" action="/cameras/source" class="body form wide" data-confirm="Recognition restarts with the new camera; video stops for a few seconds." data-title="Use this camera setup?" data-ok="Save" data-kind="primary">
<input type="hidden" name="csrf" value="%s">
<div class="choices">%s%s%s</div>
<div class="row"><label class="field"><span>USB camera</span><select name="usb">%s</select></label>
<label class="field"><span>CSI sensor</span><select name="csi"><option value="0"%s>Sensor 0</option><option value="1"%s>Sensor 1</option></select></label>
<label class="field"><span>Resolution</span><select name="size">%s</select></label></div>
<div><button class="btn primary" type="submit">Save and apply</button></div></form>
<div class="actions">%s</div></section>
<section aria-labelledby="h-rtsp"><div class="sh"><h2 id="h-rtsp">Network cameras (RTSP)</h2><span class="count">%d set up, %d enabled at a time</span></div>%s
<form method="post" action="/cameras/add" class="body form wide" autocomplete="off"><input type="hidden" name="csrf" value="%s">
<h3>Add a network camera</h3>
<div class="row"><label class="field"><span>Name</span><input name="name" required maxlength="40" placeholder="Main gate"></label>
<label class="field grow2"><span>Stream address</span><input name="url" required placeholder="rtsp://192.0.2.10:554/stream1"></label></div>
<div class="row"><label class="field"><span>Username (optional)</span><input name="user" autocomplete="off"></label>
<label class="field"><span>Password (optional)</span><input name="pw" type="password" autocomplete="new-password"></label></div>
<small class="muted">The login is stored only on this device (readable by the service account only) and is never shown again.</small>
<div><button class="btn primary" type="submit">Add camera</button></div></form></section>""" % (
        esc(current), esc(st["detail"]), csrf,
        radio("rtsp", "Network cameras", "RTSP/IP cameras on the network"), radio("usb", "USB camera", "a webcam plugged into the device"),
        radio("csi", "CSI camera", "a ribbon-cable camera, e.g. Raspberry Pi HQ"),
        usb_opts, " selected" if cfg["csi"] == "0" else "", " selected" if cfg["csi"] == "1" else "", size_opts,
        form("revert", "Revert to the previous setup", "", {}, "The camera setup before the last change is restored and recognition restarts.",
             "Revert the camera setup?") if os.path.exists(CAM_JSON + ".prev") else "",
        len(cfg["rtsp"]), MAX_ACTIVE_RTSP, table, csrf)
    return layout("cameras", "Cameras", body, flash=flash)


def camera_post(path, form):
    """handles /cameras/<action>; returns (FLASH key, test result or None)"""
    cfg = load_cameras()
    get = lambda k: form.get(k, [""])[0].strip()
    cams = {c["id"]: c for c in cfg["rtsp"]}
    action = path.rsplit("/", 1)[-1]
    if action == "source":
        src = get("source")
        if src not in ("rtsp", "usb", "csi") or get("csi") not in ("0", "1") or get("size") not in SIZES:
            return "denied", None
        if src == "usb" and get("usb") not in [d for d, _ in video_devices()]:
            return "cam_nousb", None
        if src == "rtsp" and not any(c.get("enabled") for c in cfg["rtsp"]):
            return "cam_none_enabled", None
        cfg.update(source=src, csi=get("csi"), size=get("size"), usb=get("usb") or cfg["usb"])
        return apply_cameras(cfg), None
    if action == "add":
        url, name = get("url"), get("name")
        if not RTSP_RE.match(url) or not CAM_NAME_RE.match(name):
            return "cam_bad", None
        if len(cfg["rtsp"]) >= 20:
            return "cam_bad", None
        enabled = not any(c.get("enabled") for c in cfg["rtsp"])
        cfg["rtsp"].append({"id": secrets.token_hex(4), "name": name, "url": with_login(url, get("user"), get("pw")),
                            "enabled": enabled})
        if cfg["source"] == "rtsp" and enabled:
            return apply_cameras(cfg), None
        write_private(CAM_JSON, json.dumps(cfg, indent=1))
        return "cam_added", None
    c = cams.get(get("id"))
    if not c:
        return "denied", None
    if action == "test":
        ok, text = probe_rtsp(c["url"])
        LOG.write("INFO", "camera test %s: %s" % (c["name"], "ok" if ok else text))
        return ("cam_test_ok" if ok else "cam_test_bad"), (c["id"], ok, text)
    if action == "toggle":
        if c.get("enabled"):
            c["enabled"] = False
        else:
            for other in cfg["rtsp"]:  # one network camera at a time in this release: enabling one disables the others
                other["enabled"] = False
            c["enabled"] = True
        if cfg["source"] == "rtsp":
            return apply_cameras(cfg), None
        write_private(CAM_JSON, json.dumps(cfg, indent=1))
        return "cam_saved_inactive", None
    if action == "remove":
        was_on = c.get("enabled")
        cfg["rtsp"] = [x for x in cfg["rtsp"] if x["id"] != c["id"]]
        if cfg["source"] == "rtsp" and was_on:
            return apply_cameras(cfg), None
        write_private(CAM_JSON, json.dumps(cfg, indent=1))
        return "cam_removed", None
    return "denied", None


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
        q = parse_qs(url.query)
        if url.path == "/register":  # public: the invitation token is the permission
            t = q.get("t", [""])[0]
            return self.html_page(page_register(valid_invite(t), t))
        if url.path == "/register/done":
            return self.html_page(page_register_done())
        user = self.user()
        if not user:
            return self.login_required()
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
            host = self.headers.get("Host") or "this-device"
            return self.html_page(page_people(csrf_token(user), host, q.get("show", [""])[0], q.get("msg", [""])[0]))
        m = re.match(r"^/pending/([0-9a-f]{12})/([1-5])\.jpg$", url.path)
        if m and os.path.exists(os.path.join(REG_DIR, m.group(1), m.group(2) + ".jpg")):
            return self.file(os.path.join(REG_DIR, m.group(1), m.group(2) + ".jpg"), cache="no-store")
        if url.path == "/cameras":
            return self.html_page(page_cameras(csrf_token(user), q.get("msg", [""])[0]))
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

    def register_post(self):
        """public: name, photos and consent for a valid invitation"""
        fetch = self.headers.get("X-Requested-With") == "fetch"
        ip, now = self.client_address[0], time.time()
        hits = [t for t in self.server.reg_hits.get(ip, []) if now - t < 3600]
        n = int(self.headers.get("Content-Length") or 0)

        def fail(msg, code=400):
            if fetch:
                return self.send(code, json.dumps({"ok": False, "error": msg}), "application/json")
            return self.send(code, page_register(inv, token, msg, name), extra={"Content-Security-Policy": CSP})
        inv, token, name = None, "", ""
        if len(hits) >= 10:
            return fail("Too many registrations from this device; try again later.", 429)
        if n > MAX_UPLOAD:
            return fail("The photos are too large together; use %d photos under %d MB each." % (MAX_PHOTOS, MAX_PHOTO // 1048576), 413)
        if not self.same_origin():
            return fail("The form was refused; open the invitation link again.", 403)
        self.server.reg_hits[ip] = hits + [now]
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, limit=n,
                                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                                         "CONTENT_LENGTH": str(n)})
        token = form.getfirst("t", "")
        name = form.getfirst("name", "")
        inv = valid_invite(token)
        if not inv:
            return fail("This invitation link is not valid any more; please ask for a new one.", 403)
        items = form["photos"] if "photos" in form else []
        items = items if isinstance(items, list) else [items]
        photos = [it.file.read(MAX_PHOTO + 1) for it in items if getattr(it, "filename", None)]
        sid, err = submit_registration(inv, name, photos, form.getfirst("consent") == "yes")
        if err:
            return fail(err)
        if fetch:
            return self.send(200, json.dumps({"ok": True, "next": "/register/done"}), "application/json")
        return self.redirect("/register/done")

    def do_POST(self):
        if urlparse(self.path).path == "/register":
            return self.register_post()
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
            back = "/" + (self.path.split("/")[1] if self.path.split("/")[1] in ("people", "cameras") else "system")
            return self.redirect(back + "?msg=denied")
        path = urlparse(self.path).path
        if path == "/system/service":
            return self.redirect("/system?msg=" + service_action(form.get("action", [""])[0]))
        if path == "/people/invite":
            person = form.get("person", [""])[0]
            if person and person not in enrolled():
                return self.redirect("/people?msg=denied")
            nonce = secrets.token_hex(8)
            SHOWN.clear()  # only the newest link is shown, once
            SHOWN[nonce] = (create_invite(person or None), time.time() + 600)
            return self.redirect("/people?show=" + nonce)
        sid = form.get("id", [""])[0]
        if path in ("/people/approve", "/people/reject") and re.match(r"^[0-9a-f]{12}$", sid):
            if path == "/people/reject":
                reject(sid)
                return self.redirect("/people?msg=rejected")
            with _db_lock, db() as c:
                started = c.execute("UPDATE submissions SET status='enrolling', note='' WHERE id=? AND status IN ('pending','failed')",
                                    (sid,)).rowcount == 1
            if started:
                threading.Thread(target=approve, args=(sid,), daemon=True).start()
            return self.redirect("/people?msg=approving")
        if path.startswith("/cameras/"):
            with _enroll_lock:  # one change at a time
                msg, test = camera_post(path, form)
            if test:  # the test result is shown on the page itself, without a redirect
                return self.html_page(page_cameras(csrf_token(user), msg, test))
            return self.redirect("/cameras?msg=" + msg)
        if path == "/people/remove":
            person = form.get("person", [""])[0]
            if person not in enrolled() or not KEY_RE.match(person):
                return self.redirect("/people?msg=denied")
            return self.redirect("/people?msg=" + ("removed" if remove_person(person) else "remove_failed"))
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
        self.reg_hits = {}

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
    import shutil, tempfile
    log_root, LOG.root = LOG.root, tempfile.mkdtemp()  # expected refusals and failed logins stay out of the real web.log
    try:
        selftest_checks()
    finally:
        shutil.rmtree(LOG.root, ignore_errors=True)
        LOG.root = log_root


def selftest_checks():
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
    selftest_registration(get, host)
    selftest_cameras(get, host)
    srv.shutdown()


def selftest_cameras(get, host):
    """passwords never reach a page; add, enable, remove a network camera while a CSI camera is in use (no restarts)"""
    assert masked("rtsp://admin:s3cret@cam:554/s") == "rtsp://admin:\u2022\u2022\u2022@cam:554/s" and masked("rtsp://cam/s") == "rtsp://cam/s"
    assert with_login("rtsp://cam:554/s", "u", "p@ss:1") == "rtsp://u:p%40ss%3A1@cam:554/s"
    assert active_line({"source": "csi", "csi": "0", "usb": "", "size": "1920x1080", "rtsp": []}) == "csi:0 1920 1080"
    for p in (CAM_JSON, CAM_ACTIVE):
        if os.path.exists(p):
            os.remove(p)
    write_private(CAM_JSON, json.dumps({"source": "csi", "csi": "0", "usb": "/dev/video1", "size": "1920x1080", "rtsp": []}))
    tok = csrf_token("admin")
    hd = {"Origin": "http://" + host}
    code, h, _ = get("/cameras/add", headers=hd, data=("csrf=%s&name=Gate&url=rtsp%%3A%%2F%%2Fcam%%3A554%%2Fs&user=admin&pw=topsecret" % tok).encode())
    assert code == 303 and h["Location"] == "/cameras?msg=cam_added", (code, h.get("Location"))
    cfg = load_cameras()
    assert cfg["rtsp"][0]["url"] == "rtsp://admin:topsecret@cam:554/s" and cfg["rtsp"][0]["enabled"]
    assert oct(os.stat(CAM_JSON).st_mode & 0o777) == "0o600"
    page = get("/cameras")[2]
    assert b"topsecret" not in page and b"Gate" in page and "\u2022".encode() in page
    cid = cfg["rtsp"][0]["id"]
    assert get("/cameras/toggle", headers=hd, data=("csrf=%s&id=%s" % (tok, cid)).encode())[1]["Location"] == "/cameras?msg=cam_saved_inactive"
    assert not load_cameras()["rtsp"][0]["enabled"]
    assert get("/cameras/add", headers=hd, data=("csrf=%s&name=X&url=http%%3A%%2F%%2Fx" % tok).encode())[1]["Location"] == "/cameras?msg=cam_bad"
    assert get("/cameras/source", headers=hd, data=("csrf=%s&source=rtsp&csi=0&size=1920x1080" % tok).encode())[1]["Location"] == "/cameras?msg=cam_none_enabled"
    assert get("/cameras/remove", headers=hd, data=("csrf=%s&id=%s" % (tok, cid)).encode())[1]["Location"] == "/cameras?msg=cam_removed"
    assert load_cameras()["rtsp"] == [] and not os.path.exists(CAM_ACTIVE)


def selftest_registration(get, host):
    """invitation, public form, single use, approval into the gallery, rejection, removal; a stand-in replaces the enroll
    binary (CI has no GPU): --check copies the photo, enrolling writes one zero template per person folder"""
    global ENROLL_BIN, GALLERY_DIR, GALLERY_TSV
    saved = ENROLL_BIN, GALLERY_DIR, GALLERY_TSV
    tmp = os.path.join(DATA, "selftest-reg")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "gallery"))
    ENROLL_BIN, GALLERY_DIR, GALLERY_TSV = os.path.join(tmp, "enroll.sh"), os.path.join(tmp, "gallery"), os.path.join(tmp, "g.tsv")
    with open(ENROLL_BIN, "w") as f:
        f.write('#!/bin/sh\nif [ "$1" = --check ]; then cp "$2" "$3"; echo 1x1; exit 0; fi\n'
                'z=$(python3 -c \'print(",".join(["0"] * 512))\')\n'
                'for d in "$1"/*/; do [ -d "$d" ] && printf "%s\\t%s\\n" "$(basename "$d")" "$z"; done > "$FACEPIPE_GALLERY"\nexit 0\n')
    os.chmod(ENROLL_BIN, 0o755)
    try:
        def post_reg(token, photos, consent=True, origin=True):
            b = "----selftest"
            parts = [("t", token), ("name", "Asha Rao")] + ([("consent", "yes")] if consent else [])
            body = b"".join(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (b, k, v)).encode()
                            for k, v in parts)
            for i, p in enumerate(photos):
                body += ("--%s\r\nContent-Disposition: form-data; name=\"photos\"; filename=\"p%d.jpg\"\r\n"
                         "Content-Type: image/jpeg\r\n\r\n" % (b, i)).encode() + p + b"\r\n"
            body += ("--%s--\r\n" % b).encode()
            hd = {"Content-Type": "multipart/form-data; boundary=" + b, "X-Requested-With": "fetch"}
            if origin:
                hd["Origin"] = "http://" + host
            code, _, out = get("/register", auth=None, headers=hd, data=body)
            return code, json.loads(out.decode())
        t = create_invite()
        assert b"regform" in get("/register?t=" + t, auth=None)[2] and b"not valid" in get("/register?t=nope", auth=None)[2]
        assert post_reg(t, [b"a" * 100, b"b" * 100], origin=False)[0] == 403
        assert post_reg(t, [b"a" * 100])[1]["ok"] is False                      # one photo is too few
        assert post_reg(t, [b"a" * 100, b"b" * 100], consent=False)[1]["ok"] is False
        code, j = post_reg(t, [b"a" * 100, b"b" * 100])
        assert code == 200 and j["ok"], j
        assert post_reg(t, [b"a" * 100, b"b" * 100])[0] == 403                 # single use
        with db() as c:
            sid = c.execute("SELECT id FROM submissions WHERE status='pending' ORDER BY created DESC").fetchone()["id"]
            c.execute("UPDATE submissions SET status='enrolling' WHERE id=?", (sid,))
        assert get("/pending/%s/1.jpg" % sid, auth=None)[0] == 401 and get("/pending/%s/1.jpg" % sid)[0] == 200
        approve(sid)
        with db() as c:
            row = c.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        key = row["person"]
        assert row["status"] == "approved" and key in gallery_keys(GALLERY_TSV) and display_name(key) == "Asha Rao", dict(row)
        assert len(os.listdir(os.path.join(GALLERY_DIR, key))) == 2 and not os.path.exists(os.path.join(REG_DIR, sid))
        t2 = create_invite()
        assert post_reg(t2, [b"c" * 100, b"d" * 100])[1]["ok"]
        with db() as c:
            sid2 = c.execute("SELECT id FROM submissions WHERE status='pending'").fetchone()["id"]
        reject(sid2)
        assert not os.path.exists(os.path.join(REG_DIR, sid2))                  # owner: rejected photos deleted at once
        removed = remove_person(key)
        assert removed and key not in gallery_keys(GALLERY_TSV) and not os.path.exists(os.path.join(GALLERY_DIR, key)), (removed, gallery_keys(GALLERY_TSV), os.listdir(GALLERY_DIR))
        assert b"Waiting for approval" in get("/people")[2]
    finally:
        ENROLL_BIN, GALLERY_DIR, GALLERY_TSV = saved
        shutil.rmtree(tmp, ignore_errors=True)


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
