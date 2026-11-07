#!/usr/bin/env python3
"""Attendance web page for facepipe on the Jetson Nano. Python 3.6 standard library only; works fully offline.

Reads $FACEPIPE_DATA/events-YYYY-MM-DD.jsonl and status.json (written by facepipe) and the enrolled gallery, and serves,
behind a login page (/login, session cookie, D-036):
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
import cgi, csv, datetime, fcntl, glob, getpass, hashlib, hmac, html, io, json, os, re, secrets, shutil, socket, socketserver, sqlite3, string, subprocess, sys, threading, time
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
    if len(pw) < 15 or pw != getpass.getpass("Again: "):
        sys.exit("passwords differ or shorter than 15 characters (D-040; a few words with spaces work well)")
    write_auth(AUTH_FILE, user, pw)
    print("saved", AUTH_FILE, "- every open session ends now; log in again")


def write_auth(path, user, pw):
    """user:salt:hash, 0600, replaced in one step so the running website never reads half a file"""
    salt = os.urandom(16)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("%s:%s:%s\n" % (user, salt.hex(), hash_pw(pw, salt)))
    os.replace(path + ".tmp", path)


def set_rtsp_password(user):
    """RTSP Basic auth needs the password itself (it is checked by gst-rtsp-server), so it is kept in a 0600 file."""
    pw = getpass.getpass("New RTSP password for %s: " % user)
    if len(pw) < 8 or pw != getpass.getpass("Again: ") or ":" in user:
        sys.exit("passwords differ, shorter than 8 characters, or ':' in the user name")
    fd = os.open(RTSP_AUTH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("%s:%s\n" % (user, pw))
    print("saved", RTSP_AUTH, "- restart facepipe to apply: sudo systemctl restart facepipe")


def load_auth(path):
    user, salt, digest = open(path, encoding="utf-8").read().strip().split(":")
    return user, bytes.fromhex(salt), digest


def session_key(path):
    """32 random bytes in data/session.key (0600), made once: sessions and open forms survive restarts and updates.
    Delete the file and restart the website to end every session (D-036)."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
    except FileExistsError:
        pass
    with open(path, "rb") as f:
        key = f.read()
    if len(key) != 32:
        sys.exit("%s is damaged: delete it and start again (everyone logs in again)" % path)
    return key


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
        if e["score"] >= p["best"]:  # the closest sighting; its own rule decides how it is shown (match_text)
            p["best"], p["best_e"] = e["score"], e
    return people, unknown


def match_text(e):
    """the number of the rule that decided, always named: "L2 0.62" (distance, 0 to 2, lower is closer) or "Cosine 0.78"
    (similarity, -1 to 1, higher is closer); events before the metric field are cosine"""
    if e.get("metric") == "l2" and "l2" in e:
        return "L2 %.2f" % e["l2"]
    return "Cosine %.2f" % e["score"]


def rule_text():
    """the rule in use now, for the column headings: "L2 ≤ 0.90" or "Cosine ≥ 0.50" """
    m = matching()
    return "L2 ≤ %.2f" % m["l2"] if m["metric"] == "l2" else "Cosine ≥ %.2f" % m["cosine"]


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
        cams = s.get("cameras") or []
        if len(cams) > 1:  # two cameras: each one's own rate, or that it is offline
            s.update(state="live", detail="Live: " + ", ".join(
                "%s %.1f fps" % (c.get("name", "?"), c.get("fps", 0)) if c.get("up") else "%s offline" % c.get("name", "?") for c in cams))
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
STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "logo.svg": "image/svg+xml",
          "favicon.svg": "image/svg+xml"}
LAYOUT = string.Template(open(os.path.join(WEB_DIR, "layout.html"), encoding="utf-8").read())
PUBLIC = string.Template(open(os.path.join(WEB_DIR, "public.html"), encoding="utf-8").read())
ICONS = {  # 24x24 stroke icons for the sidebar (inline SVG: no files, no fonts)
    "overview": "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
    "live": "M23 7l-7 5 7 5zM3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
    "attendance": "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM16 2v4M8 2v4M3 10h18M8 15l2.5 2.5L16 13",
    "people": "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
    "cameras": "M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    "uploads": "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
    "users": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM19 8v6M22 11h-6",
    "system": "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"}
PAGES = [("overview", "/", "Overview"), ("live", "/live", "Live"), ("attendance", "/attendance", "Attendance"),
         ("people", "/people", "People"), ("cameras", "/cameras", "Cameras"), ("uploads", "/uploads", "Video upload"),
         ("users", "/users", "Users"), ("system", "/system", "System")]
CSRF_MARK = "@@csrf@@"  # the layout's sign-out form; html_page puts the session's form token here
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
    "match_saved": ("ok", "Face matching saved; recognition restarted with it."),
    "match_saved_stopped": ("ok", "Face matching saved. Recognition is stopped; it applies when you start it."),
    "match_bad": ("bad", "Check the values: cosine 0.30 to 0.90, L2 0.50 to 1.20."),
    "cam_added": ("ok", "Camera added."), "cam_removed": ("ok", "Camera removed."), "cam_noprev": ("bad", "There is no earlier camera setup to restore."),
    "cam_two": ("bad", "Two network cameras are enabled already; disable one first."),
    "up_cancelled": ("ok", "Cancelled."), "up_deleted": ("ok", "Video deleted."),
    "u_added": ("ok", "User added."), "u_link": ("ok", "New link made; the previous one no longer works."),
    "u_role": ("ok", "Role changed; that user signs in again."), "u_deleted": ("ok", "User deleted; their sign-in ends at once."),
    "u_bad": ("bad", "Check the name, the role and the email or phone number (phone with + and country code)."),
    "u_taken": ("bad", "That email or phone number is already used."), "u_reauth": ("bad", "Your password was not right; nothing was changed."),
    "pw_changed": ("ok", "Password changed; sign in again."), "pw_wrong": ("bad", "The current password was not right."),
    "pw_mismatch": ("bad", "The new passwords differ."), "pw_weak": ("bad", "Use at least 15 characters, not your name, email or phone."),
    "cam_test_ok": ("ok", "The camera answered."), "cam_test_bad": ("bad", "The camera test failed; see the line under its name."), "denied": ("bad", "The request was refused (expired form or another site).")}
esc = html.escape


def layout(page_id, title, body, flash="", refresh=False):
    st = status()
    p = getattr(REQ, "p", None)  # viewers do not see the admin pages (they are refused anyway, D-040)
    nav = "".join("<a href='%s'%s><svg viewBox='0 0 24 24' aria-hidden='true'><path d='%s'/></svg>%s</a>"
                  % (href, " aria-current='page'" if pid == page_id else "", ICONS[pid], label) for pid, href, label in PAGES
                  if not (p and p["role"] != "admin" and pid in ADMIN_PAGES))
    msg = ""
    if flash in FLASH:
        kind, text = FLASH[flash]
        msg = "<div class='flash %s' role='status'>%s</div>" % (kind, esc(text))
    return LAYOUT.substitute(title=esc(title), site=esc(SITE), page=page_id, refresh="1" if refresh else "0", nav=nav,
                             banner=service_banner(),
                             who=esc(p["name"]) if p else "",
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


def face(e, gal):
    """an enrolled person's own picture (profile photo); the camera's face crop only for unknown or no-longer-enrolled
    faces. The crop of a known person stays one click away in the evidence frame (owner's choice)."""
    if e["name"] in gal:
        return avatar(e["name"], gal)
    return "<img class='av' src='/snap/%s' alt=''>" % esc(e["snap"])


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
        "<tr><td><div class='who'>%s%s</div></td><td class='num'>%s%s</td><td class='num'>%s</td><td class='ev'>%s</td></tr>"
        % (face(e, gal), "Unknown" if e["name"] == "unknown" else esc(display_name(e["name"])), hms(e["time"]),
           "<div class='m muted'>%s</div>" % esc(e["camera_name"]) if e.get("camera_name") else "",
           match_text(e), evidence(e)) for e in recent if SNAP_RE.match(e.get("snap", "")))
    act = ("<table><thead><tr><th scope='col'>Who</th><th scope='col'>Time</th><th scope='col'>Match <small class='muted'>(%s)</small></th>"
           "<th scope='col'>Evidence</th></tr></thead><tbody>%s</tbody></table>" % (esc(rule_text()), rows)) if rows else \
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


def page_live(host, lan):
    """lan: the visitor is on the local network; only then is the RTSP address shown (facepipe refuses others, D-041)"""
    rtsp = ("<p class='body muted'>On the local network also as a video stream for VLC or an NVR: <code>rtsp://%s:%s/live</code> "
            "(separate RTSP login; refused from the internet).</p>" % (esc(host), RTSP_PORT)) if lan and os.path.exists(RTSP_AUTH) else ""
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
            "<tr data-name='%s'><td data-v='%s'><div class='who'>%s%s%s</div></td>"
            "<td class='num' data-v='%s'>%s</td><td class='num' data-v='%s'>%s</td><td class='num' data-v='%d'>%d</td>"
            "<td class='num' data-v='%.3f'><meter min='0' max='1' value='%.3f'></meter>%s</td><td class='ev'>%s</td></tr>"
            % (esc(display_name(n).lower()), esc(display_name(n).lower()), face({"name": n, "snap": p["snap"]}, gal), esc(display_name(n)),
               "" if n in gal else " <small>(no longer enrolled)</small>", p["first"], hms(p["first"]), p["last"], hms(p["last"]),
               p["sightings"], p["sightings"], p["best"], max(0.0, min(1.0, p["best"])), match_text(p.get("best_e", {"score": p["best"]})), evidence(p)))
    present = (
        "<table data-sort><thead><tr><th data-k scope='col'><button>Name</button></th><th data-k scope='col'><button>First seen</button></th>"
        "<th data-k scope='col'><button>Last seen</button></th><th data-k scope='col'><button>Sightings</button></th>"
        "<th data-k scope='col'><button>Best match <small class='muted'>(%s)</small></button></th><th scope='col'>Evidence</th></tr></thead><tbody>%s</tbody></table>"
        % (esc(rule_text()), "".join(rows))) if rows else "<p class='empty'>No one recognized %s.</p>" % ("yet today" if is_today else "on this day")
    absent_html = ("<div class='chips'>%s</div>" % "".join(
        "<span class='chip' data-name='%s'>%s%s</span>" % (esc(display_name(n).lower()), avatar(n, gal), esc(display_name(n)))
        for n in absent)) if absent else "<p class='empty'>%s</p>" % ("Everyone enrolled is present." if gal else "No one is enrolled yet.")
    unknown_html = ("<div class='grid'>%s</div>" % "".join(
        "<figure><img src='/snap/%s' alt='Unknown face at %s' loading='lazy'><figcaption>%s &middot; %s<br>%s</figcaption></figure>"
        % (esc(e["snap"]), hms(e["time"]), hms(e["time"]), match_text(e), evidence(e)) for e in reversed(unknown))
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
    """the camera facepipe uses, without credentials: data/cameras.active (chosen on the Cameras page) over facepipe.env"""
    try:
        lines = [l.split() for l in open(CAM_ACTIVE, encoding="utf-8").read().splitlines() if l.strip()]
        uri = lines[0][0]
    except (OSError, IndexError):
        lines, uri = [], env_uri()
    if len(lines) > 1:  # two network cameras (D-039)
        names = {c["id"]: c["name"] for c in load_cameras()["rtsp"]}
        return " and ".join(names.get(l[3] if len(l) > 3 else "", "network camera") for l in lines) + " (network cameras)"
    for c in load_cameras()["rtsp"] if RTSP_RE.match(uri) else []:
        if c["url"] == uri:
            return "%s (network camera at %s)" % (c["name"], urlparse(uri).hostname or "?")
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


def page_system(csrf, flash, read_only=False):
    """read_only: a viewer sees the same details without the controls (owner, 2026-11-07)"""
    st = status()
    active = service_state()
    desired = read_desired()
    now = datetime.datetime.now()
    logdir = os.path.join(LOG_ROOT, str(now.year), MONTHS[now.month - 1])
    log_lines = [l for l in tail(os.path.join(logdir, "facepipe.log"), 40) if "TensorRT" not in l][-15:]
    upd = tail(os.path.join(logdir, "update.log"), 3)
    sv = service_view()

    def button(action, label, cls, confirm):
        return ("<form method='post' action='/system/service' data-confirm='%s' data-title='%s the recognition service?' "
                "data-ok='%s' data-kind='%s'><input type='hidden' name='csrf' value='%s'>"
                "<input type='hidden' name='action' value='%s'><button class='btn %s' type='submit'>%s</button></form>"
                % (esc(confirm), label, label, "danger" if cls == "danger" else "primary", csrf, action, cls, label))
    buttons = "".join([
        button("start", "Start", "primary", "Recognition and attendance recording begin again.") if active != "active" else "",
        button("restart", "Restart", "", "Video and recognition stop for a few seconds, then start again.") if active == "active" else "",
        button("stop", "Stop", "danger", "No attendance is recorded until it is started again. It stays stopped across updates.")
        if active == "active" else ""]) if not read_only else ""
    body = """<h1>System</h1><p class="sub">Device health, service control and recent log lines.</p>
<section aria-labelledby="h-health"><div class="sh"><h2 id="h-health">Device health</h2><span class="count">refreshes every 5 s</span></div>
<div id="health">%s</div></section>
<section aria-labelledby="h-svc"><div class="sh"><h2 id="h-svc">Recognition service</h2><span class="pill %s">%s</span></div>
<div class="svc-big %s"><b>%s</b><span>%s</span></div>
<dl class="kv"><dt>systemd state</dt><dd>%s</dd><dt>Wanted state</dt><dd>%s</dd><dt>Frame rate</dt><dd>%s</dd>
<dt>Detections per second</dt><dd>%s</dd><dt>Frames processed</dt><dd>%s</dd><dt>Camera</dt><dd>%s</dd></dl>
<div class="actions">%s</div></section>
<section aria-labelledby="h-match"><div class="sh"><h2 id="h-match">Face matching</h2><span class="count">in use: %s</span></div>%s</section>
<section aria-labelledby="h-dev"><div class="sh"><h2 id="h-dev">Device</h2></div>
<dl class="kv"><dt>Software</dt><dd>facepipe %s (web %s)</dd><dt>microSD card</dt><dd>%s <span class="muted">(system, app, data and logs)</span></dd>
<dt>Recordings</dt><dd>%s <span class="muted">(clips and evidence; a volume on the microSD card)</span></dd>
<dt>Last update check</dt><dd>%s</dd></dl></section>
<section aria-labelledby="h-log" class="noprint"><div class="sh"><h2 id="h-log">Recent log</h2><span class="count">facepipe.log</span></div>
<pre class="log">%s</pre></section>""" % (
        health_html(device_health()), st["state"], esc(st["detail"]), sv[0], esc(sv[1]), esc(sv[2]), esc(active), "running" if desired != "stopped" else "stopped (kept across updates)",
        "%.1f fps" % st["fps"] if "fps" in st else "&ndash;", "%.1f" % st["det_fps"] if "det_fps" in st else "&ndash;",
        st.get("frames", "&ndash;"), esc(camera_label()), buttons,
        "cosine &ge; %.2f" % matching()["cosine"] if matching()["metric"] == "cosine" else "L2 &le; %.2f" % matching()["l2"],
        matching_html(csrf) if not read_only else "", esc(str(st.get("version", "?"))), esc(VERSION), esc(disk(HERE)),
        esc(disk(REC_DIR)), esc(upd[-1]) if upd else "no update has run yet", esc("\n".join(log_lines)) or "no log yet")
    return layout("system", "System", body, flash=flash)


# ---------- device health (owner: "temperature, fan, memory, CPU/GPU load, so I can shut the service remotely") ----------
# Read from /sys and /proc without privileges. Jetson Nano paths; on other boards missing values are left out.
WARN_C, HOT_C = 80, 90            # the Nano throttles near 97 C and shuts down a few degrees above
LOW_MEM_MB = 400
_cpu_prev = {}


def _read(path, cast=str):
    try:
        with open(path) as f:
            return cast(f.read().strip())
    except (OSError, ValueError):
        return None


def _cpu_loads():
    """per-core busy % since the previous call (or over 0.3 s on the first)"""
    def snap():
        out = {}
        for line in open("/proc/stat"):
            if line.startswith("cpu") and line[3].isdigit():
                p = line.split()
                v = list(map(int, p[1:8]))
                out[p[0]] = (sum(v), v[3] + v[4])  # total, idle + iowait
        return out
    global _cpu_prev
    if not _cpu_prev:
        _cpu_prev = snap()
        time.sleep(0.3)
    now = snap()
    loads = []
    for k in sorted(now, key=lambda c: int(c[3:])):
        t0, i0 = _cpu_prev.get(k, now[k])
        dt, di = now[k][0] - t0, now[k][1] - i0
        loads.append(round(100.0 * (dt - di) / dt) if dt > 0 else 0)
    _cpu_prev = now
    return loads


def device_health():
    h = {"temps": {}}
    for z in sorted(glob.glob("/sys/devices/virtual/thermal/thermal_zone*")):
        name, t = _read(z + "/type"), _read(z + "/temp", int)
        if name and t is not None and name not in ("thermal-fan-est",):
            h["temps"][name.replace("-therm", "")] = round(t / 1000.0, 1)
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0]) // 1024
    h["mem"] = {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable"), "swap_total": mem.get("SwapTotal"),
                "swap_used": (mem.get("SwapTotal") or 0) - (mem.get("SwapFree") or 0)}
    h["cpu"] = _cpu_loads()
    h["cpu_mhz"] = (_read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", int) or 0) // 1000
    gpu = _read("/sys/devices/gpu.0/load", int)
    h["gpu"] = None if gpu is None else round(gpu / 10.0)
    h["gpu_mhz"] = (_read("/sys/devices/gpu.0/devfreq/57000000.gpu/cur_freq", int) or 0) // 1000000
    pwm = _read("/sys/devices/pwm-fan/target_pwm", int)
    h["fan"] = None if pwm is None else {"pct": round(100.0 * pwm / 255), "rpm": _read("/sys/devices/pwm-fan/rpm_measured", int)}
    up = _read("/proc/uptime")
    h["uptime_h"] = round(float(up.split()[0]) / 3600, 1) if up else None
    hottest = max(h["temps"].values()) if h["temps"] else 0
    h["level"] = "bad" if hottest >= HOT_C else "warn" if hottest >= WARN_C or (h["mem"]["available"] or 1e9) < LOW_MEM_MB else "ok"
    return h


def health_html(h):
    """the Device health panel; app.js refreshes the same markup from /api/health every 5 s"""
    def lvl_t(c):
        return "bad" if c >= HOT_C else "warn" if c >= WARN_C else "ok"

    def meter(pct, lvl):
        return "<span class='bar %s'><i class='w%d'></i></span>" % (lvl, max(0, min(100, int(round(pct / 5.0) * 5))))
    temps = "".join("<div class='hrow'><span>%s</span><b class='%s-text'>%.0f &deg;C</b></div>" % (esc(k), lvl_t(v), v)
                    for k, v in sorted(h["temps"].items(), key=lambda kv: -kv[1]))
    cpu = "".join("<div class='hrow'><span>Core %d</span>%s<b>%d%%</b></div>" % (i, meter(v, "ok" if v < 85 else "warn"), v)
                  for i, v in enumerate(h["cpu"]))
    m = h["mem"]
    used = (m["total"] or 0) - (m["available"] or 0)
    mem_pct = 100.0 * used / m["total"] if m["total"] else 0
    fan = h["fan"]
    fan_txt = ("%d%% power%s" % (fan["pct"], ", %d rpm" % fan["rpm"] if fan.get("rpm") else " (no speed sensor)")) if fan else "no fan control"
    # name the reason: heat and low memory need different answers from whoever reads this, and on a device running only
    # this software low memory is the rare one, so "warm or short of memory" sent the reader looking in the wrong place
    hot_name, hot_c = max(h["temps"].items(), key=lambda kv: kv[1]) if h["temps"] else ("", 0)
    heat = "%s is at %.0f &deg;C" % (esc(hot_name), hot_c)
    # same test as device_health(): a machine that reports no figure is never called short of memory
    low_mem = m["available"] is not None and m["total"] and m["available"] < LOW_MEM_MB
    short = "%d MB of %d MB free" % (m["available"], m["total"]) if low_mem else ""
    if h["level"] == "bad":
        banner = ("<div class='flash bad'>Too hot: %s. The Nano is slowing itself down to cool off. Check the fan and that "
                  "air can move around the case; stop recognition on this page if it stays like this.</div>" % heat)
    elif h["level"] == "warn":
        if hot_c >= WARN_C and low_mem:
            why = "running hot (%s) and low on memory (%s)" % (heat, short)
        elif hot_c >= WARN_C:
            why = "running hot: %s" % heat
        else:
            why = "low on memory: %s" % short
        banner = ("<div class='flash warn'>The device is %s. Recognition keeps working; %s</div>"
                  % (why, "check the fan and airflow before it reaches %d &deg;C." % HOT_C if hot_c >= WARN_C
                     else "restarting the services on this page frees memory if it stays low."))
    else:
        banner = ""
    return """%s<div class="health">
<div class="hcard"><div class="k">Temperature</div>%s</div>
<div class="hcard"><div class="k">CPU %s MHz</div>%s</div>
<div class="hcard"><div class="k">GPU %s MHz</div><div class='hrow'><span>Load</span>%s<b>%s</b></div>
 <div class="k gap">Fan</div><div class='hrow'><span>%s</span></div></div>
<div class="hcard"><div class="k">Memory</div><div class='hrow'><span>Used</span>%s<b>%.1f / %.1f GB</b></div>
 <div class='hrow'><span>Available</span><b class='%s-text'>%d MB</b></div><div class='hrow'><span>Swap used</span><b>%d / %d MB</b></div>
 <div class="k gap">Up for</div><div class='hrow'><span>%s hours</span></div></div>
</div>""" % (banner, temps or "<div class='hrow'><span>not available</span></div>", h["cpu_mhz"] or "?", cpu,
             h["gpu_mhz"] or "?", meter(h["gpu"] or 0, "ok" if (h["gpu"] or 0) < 90 else "warn"),
             "%d%%" % h["gpu"] if h["gpu"] is not None else "?", fan_txt,
             meter(mem_pct, "ok" if (m["available"] or 0) >= LOW_MEM_MB else "warn"), used / 1024.0, (m["total"] or 0) / 1024.0,
             "ok" if (m["available"] or 0) >= LOW_MEM_MB else "warn", m["available"] or 0, m["swap_used"], m["swap_total"] or 0,
             h["uptime_h"] if h["uptime_h"] is not None else "?")


# ---------- face matching (D-025): cosine or L2, chosen on the System page ----------
MATCH_CONF = os.path.join(DATA, "matching.conf")
COS_RANGE, L2_RANGE = (0.30, 0.90), (0.50, 1.20)


def env_value(key, default):
    try:
        for line in open(os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "facepipe.env"), encoding="utf-8"):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def matching():
    """the settings facepipe uses: data/matching.conf over facepipe.env over the defaults"""
    m = {"metric": env_value("FACEPIPE_METRIC", "cosine").lower(), "cosine": float(env_value("FACEPIPE_MATCH", "0.5")),
         "l2": float(env_value("FACEPIPE_L2_MATCH", "0.95"))}
    try:
        for line in open(MATCH_CONF):
            k, _, v = line.strip().partition("=")
            if k == "metric" and v in ("cosine", "l2"):
                m["metric"] = v
            elif k in ("cosine", "l2"):
                m[k] = float(v)
    except (OSError, ValueError):
        pass
    return m


def save_matching(form):
    try:
        metric = form.get("metric", [""])[0]
        cos, l2 = float(form.get("cosine", [""])[0]), float(form.get("l2", [""])[0])
    except ValueError:
        return "match_bad"
    if metric not in ("cosine", "l2") or not COS_RANGE[0] <= cos <= COS_RANGE[1] or not L2_RANGE[0] <= l2 <= L2_RANGE[1]:
        return "match_bad"
    write_private(MATCH_CONF, "metric=%s\ncosine=%.2f\nl2=%.2f\n" % (metric, cos, l2))
    LOG.write("INFO", "face matching set to %s (cosine %.2f, L2 %.2f)" % (metric, cos, l2))
    if read_desired() == "stopped":
        return "match_saved_stopped"
    return "match_saved" if service_action("restart") == "restarted" else "failed"


def matching_html(csrf):
    m = matching()

    def opt(v, title, text):
        return ("<label class='choice'><input type='radio' name='metric' value='%s'%s><span><b>%s</b><small>%s</small></span></label>"
                % (v, " checked" if m["metric"] == v else "", title, text))
    return """<form method="post" action="/system/matching" class="body form wide" data-confirm="Recognition restarts with the new rule; video stops for a few seconds." data-title="Change face matching?" data-ok="Save" data-kind="primary">
<input type="hidden" name="csrf" value="%s">
<div class="choices">%s%s</div>
<div class="row"><label class="field"><span>Cosine: match when similarity is at least</span><input name="cosine" type="number" step="0.01" min="%.2f" max="%.2f" value="%.2f" required></label>
<label class="field"><span>L2: match when distance is at most</span><input name="l2" type="number" step="0.01" min="%.2f" max="%.2f" value="%.2f" required></label></div>
<small class="muted">Both compare the same face templates and always pick the same closest person; only the pass rule differs. They relate as L2 = &radic;(2 &minus; 2 &times; cosine): cosine 0.50 is L2 1.00, L2 0.95 is cosine 0.55. Higher cosine or lower L2 means stricter: fewer false matches, more faces left Unknown.</small>
<div><button class="btn primary" type="submit">Save and apply</button></div></form>""" % (
        csrf, opt("cosine", "Cosine similarity", "default; the same rule as the CCTV system"),
        opt("l2", "L2 distance", "Euclidean distance between templates"),
        COS_RANGE[0], COS_RANGE[1], m["cosine"], L2_RANGE[0], L2_RANGE[1], m["l2"])


# ---------- service control (D-033): exact sudoers rule for start/stop/restart of facepipe only ----------
SERVICE_LAST = os.path.join(DATA, "service-last.json")  # the last start, stop or restart: action, who, when
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


def service_action(action, who="the website"):
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
    LOG.write("INFO", "service %s from the website by %s; now %s" % (action, who, service_state()))
    write_private(SERVICE_LAST, json.dumps({"action": action, "by": who, "at": time.time()}))
    return {"start": "started", "stop": "stopped", "restart": "restarted"}[action]


def last_action():
    """e.g. "Stopped by Hamza at 11:30 AM", or "" """
    try:
        with open(SERVICE_LAST, encoding="utf-8") as f:
            a = json.load(f)
        t = datetime.datetime.fromtimestamp(a["at"])
        day = "" if t.date() == datetime.date.today() else t.strftime(" on %d %b")
        return "%s by %s at %s%s" % ({"start": "Started", "stop": "Stopped", "restart": "Restarted"}[a["action"]], a["by"],
                                     hm(t.isoformat()), day)
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def service_view():
    """(css state, headline, explanation) of recognition, from the wanted state and facepipe's own reports"""
    st, desired, last = status(), read_desired(), last_action()
    if desired == "stopped":
        return "stopped", "Recognition is STOPPED", "No attendance is recorded and the live view is off. %s." % (last or "Stopped from the website")
    if st["state"] == "stopped":
        return "stopped", "Recognition is NOT RUNNING", "It should be running: it may be starting or restarting; if this stays for a minute, see the System page."
    if st["state"] == "no_video":
        return "no_video", "Recognition is RUNNING but has NO CAMERA PICTURE", "No attendance is recorded until the camera answers."
    return "live", "Recognition is RUNNING", st["detail"] + (". " + last + "." if last else ".")


def service_banner():
    """on every page while recognition is not running normally, so a stop is never missed (owner, 2026-11-07)"""
    state, head, text = service_view()
    if state == "live":
        return ""
    return ("<div class='svc-banner %s' role='alert'><b>%s.</b> %s <a href='/system'>System page</a></div>" % (state, esc(head), esc(text)))


def csv_text(day):
    people, _ = attendance(read_events(day))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "name", "status", "first_seen", "last_seen", "sightings", "match_rule", "best_match"])
    for n, p in sorted(people.items(), key=lambda kv: kv[1]["first"]):
        e = p.get("best_e", {})
        l2 = e.get("metric") == "l2" and "l2" in e  # the rule that decided: L2 distance (lower is closer) or cosine
        w.writerow([day, display_name(n), "present", hms(p["first"]), hms(p["last"]), p["sightings"], "l2" if l2 else "cosine",
                    "%.3f" % (e["l2"] if l2 else p["best"])])
    for n in sorted(x for x in enrolled() if x not in people):
        w.writerow([day, display_name(n), "absent", "", "", 0, "", ""])
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
    # accounts (D-040): unique sign-in names, at least one; version rises on every role or password change
    c.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE, phone TEXT UNIQUE,"
              " role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')), salt TEXT, hash TEXT, version INTEGER NOT NULL DEFAULT 0,"
              " created REAL NOT NULL, last_login REAL, CHECK (email IS NOT NULL OR phone IS NOT NULL))")
    c.execute("CREATE TABLE IF NOT EXISTS account_links (user TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL, expires REAL NOT NULL)")
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
    gl = open(GPU_LOCK, "a")  # D-039: never beside a video upload (both load TensorRT engines next to live recognition)
    try:
        fcntl.flock(gl, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        gl.close()
        return "a video upload is being processed; approve again when it is done"
    try:
        r = subprocess.run([ENROLL_BIN, os.path.join(work, "gallery")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=900, env=dict(os.environ, FACEPIPE_GALLERY=os.path.join(work, "gallery.tsv")))
    except (OSError, subprocess.SubprocessError) as e:
        return "enrollment could not run (%s)" % e.__class__.__name__
    finally:
        gl.close()  # releases the lock
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


def public_page(title, body, page="register", tag="Registration",
                foot="Your photos stay on this device and are reviewed by the administrator."):
    """pages without the admin session (registration, sign-in): no navigation, no device status"""
    return PUBLIC.substitute(title=esc(title), site=esc(SITE), body=body, version=esc(VERSION), page=page, tag=esc(tag),
                             foot=esc(foot))


def page_not_found():
    body = """<div class="nf"><p class="nf-code" aria-hidden="true">404</p><h1>Page not found</h1>
<p class="sub">The address may be mistyped, or the page, evidence frame or clip was removed (recordings are kept for a limited time).</p>
<p><a class="btn primary" href="/">Go to the dashboard</a></p></div>"""
    return public_page("Page not found", body, "notfound", "Jetson Nano edge AI", "Recognition runs on this device; nothing leaves it.")


def page_login(nxt, error="", info=""):
    body = """<h1>Sign in</h1><p class="sub">Attendance, live view and, for administrators, device control.</p>
<div class="flash ok"%s role="status">%s</div><div class="flash bad"%s role="alert">%s</div>
<section><form class="body form" method="post" action="/login">
<input type="hidden" name="next" value="%s">
<label class="field"><span>Email, phone number or user name</span><input name="user" required maxlength="254" autocomplete="username" autofocus></label>
<label class="field"><span>Password</span><input name="password" type="password" required maxlength="256" autocomplete="current-password"></label>
<button class="btn primary submit" type="submit">Sign in</button>
</form></section>""" % ("" if info else " hidden", esc(info), "" if error else " hidden", esc(error), esc(nxt))
    return public_page("Sign in", body, "login", "Sign in", "Recognition runs on this device; nothing leaves it.")


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
<button class="btn primary submit" type="submit">Send Registration</button>
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
MAX_ACTIVE_RTSP = 2   # D-039: two network cameras at once, in one pipeline
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


def video_devices(by_id="/dev/v4l/by-id"):
    """USB cameras only: CSI sensors also appear as /dev/videoN, named vi-output by the Tegra driver. Each camera is named
    by its udev by-id link (vendor, model, serial), which survives replugging and reboots; /dev/videoN can change
    (ponytail: two identical cameras without a serial share one by-id link; the second falls back to /dev/videoN)"""
    ids = {}
    for link in sorted(glob.glob(os.path.join(by_id, "*-video-index0"))):
        ids.setdefault(os.path.realpath(link), link)
    out = []
    for d in sorted(glob.glob("/sys/class/video4linux/video*")):
        try:
            name = open(os.path.join(d, "name")).read().strip()
        except OSError:
            continue
        if not name.startswith("vi-output"):
            dev = "/dev/" + os.path.basename(d)
            out.append((ids.get(dev, dev), name))
    return out


def active_line(cfg):
    w, h = SIZES.get(cfg.get("size"), (1920, 1080))
    if cfg["source"] == "csi":
        return "csi:%s %d %d" % (cfg["csi"], w, h)
    if cfg["source"] == "usb":
        return "usb:%s %d %d" % (cfg["usb"], w, h)
    on = [c for c in cfg["rtsp"] if c.get("enabled")][:MAX_ACTIVE_RTSP]
    return "\n".join("%s %d %d %s %s" % (c["url"], w, h, c["id"], c["name"]) for c in on)


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


def page_cameras(csrf, flash="", test=None, read_only=False):
    """read_only: a viewer sees the setup (passwords masked as for admins) without any form (owner, 2026-11-07)"""
    cfg = load_cameras()
    st = status()

    def form(action, label, cls, fields, confirm=None, title=None):
        conf = (" data-confirm='%s' data-title='%s' data-ok='%s' data-kind='%s'" % (esc(confirm), esc(title), esc(label),
                "danger" if "danger" in cls else "primary")) if confirm else ""
        hidden = "".join("<input type='hidden' name='%s' value='%s'>" % (k, esc(v)) for k, v in fields.items())
        return ("<form method='post' action='/cameras/%s'%s><input type='hidden' name='csrf' value='%s'>%s"
                "<button class='btn %s' type='submit'>%s</button></form>" % (action, conf, csrf, hidden, cls, label))

    usb_opts = "".join("<option value='%s'%s>%s: %s</option>" % (esc(dev), " selected" if dev == cfg["usb"] else "", esc(name), esc(dev))
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
    if read_only:  # every control on this page is a form; the route refuses its POSTs anyway (D-040)
        body = re.sub(r"<form[ >].*?</form>", "", body, flags=re.S)
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
            if sum(1 for x in cfg["rtsp"] if x.get("enabled")) >= MAX_ACTIVE_RTSP:
                return "cam_two", None
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



# ---------- video uploads (D-039): facepipe processes them beside live recognition (live first); never attendance ----------
# data/uploads/<id>/: input (written here as input.part, renamed when checked), meta.json, state (queued, processing, done,
# failed, cancelled; set by facepipe after queued), reason, progress.json, result.mp4, result.json, cancel (a request).
UPLOADS = os.path.join(DATA, "uploads")
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
UPLOAD_MAX_LAN = 500 * 1048576
UPLOAD_MAX_REMOTE = 95 * 1048576  # Cloudflare's free plan refuses request bodies over 100 MB
UPLOAD_MAX_JOBS = 3               # queued or processing at once
UPLOAD_DIR_CAP = 4 << 30          # inputs and results together
UPLOAD_RESERVE = 2 << 30          # free space kept on the data partition (facepipe checks it too)
UPLOAD_MAX_S = 1800               # 30 minutes
UPLOAD_KEEP_S = 7 * 86400         # owner, 2026-11-07: deleted 7 days after upload
GPU_LOCK = os.path.join(DATA, "gpu-batch.lock")  # held by facepipe for an upload, by enrollment here: never both at once


def up_read(d, name, default=""):
    try:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def du(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def uploads():
    """newest first: id, dir, state ("uploading" while the file arrives), meta, reason, progress"""
    out = []
    for i in (os.listdir(UPLOADS) if os.path.isdir(UPLOADS) else []):
        d = os.path.join(UPLOADS, i)
        if not UPLOAD_ID_RE.match(i) or not os.path.isdir(d):
            continue
        u = {"id": i, "dir": d, "state": up_read(d, "state", "uploading"), "reason": up_read(d, "reason")}
        for k, f in (("meta", "meta.json"), ("progress", "progress.json")):
            try:
                u[k] = json.loads(up_read(d, f, "{}"))
            except ValueError:
                u[k] = {}
        out.append(u)
    return sorted(out, key=lambda u: u["meta"].get("created", 0), reverse=True)


def cleanup_uploads(now=None):
    """7-day retention; an upload that never finished arriving (browser closed) goes after an hour"""
    now = now or time.time()
    for u in uploads():
        part = os.path.join(u["dir"], "input.part")
        stale = u["state"] == "uploading" and now - (os.path.getmtime(part) if os.path.exists(part) else os.path.getmtime(u["dir"])) > 3600
        old = u["state"] != "processing" and now - u["meta"].get("created", now) > UPLOAD_KEEP_S
        if stale or old:
            shutil.rmtree(u["dir"], ignore_errors=True)


def probe_video(path):
    """(meta, None) for a video facepipe will process, or (None, reason). gst-discoverer runs in its own process, so a
    file that crashes a demuxer here crashes only the check (facepipe re-checks the limits while decoding)."""
    try:
        r = subprocess.run(["gst-discoverer-1.0", "-v", "-t", "20", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=40)
    except (OSError, subprocess.SubprocessError):
        return None, "the video could not be checked"
    return parse_discoverer(r.returncode, r.stdout.decode(errors="replace"))


def parse_discoverer(code, text):
    codec = re.search(r"^\s+video: video/x-(h264|h265)\b", text, re.M)
    w, h = re.search(r"^\s+Width: (\d+)", text, re.M), re.search(r"^\s+Height: (\d+)", text, re.M)
    d = re.search(r"^\s*Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)", text, re.M)
    if code != 0 or not re.search(r"^\s+video: ", text, re.M):
        return None, "this file is not a video the device can read"
    if not codec:
        return None, "only H.264 or H.265 videos are processed (most phones and cameras record these)"
    if not (w and h and d):
        return None, "the video's size or length could not be read"
    w, h = int(w.group(1)), int(h.group(1))
    secs = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    if w > 1920 or h > 1080:
        return None, "the video is larger than 1920 x 1080"
    if not 0 < secs <= UPLOAD_MAX_S:
        return None, "the video is longer than 30 minutes"
    return {"width": w, "height": h, "duration": round(secs, 2), "codec": codec.group(1).upper().replace("H2", "H.2")}, None


def receive_upload(rfile, n, filename, remote):
    """stores one video of n bytes from rfile; returns (id, None) or (None, reason)"""
    limit = UPLOAD_MAX_REMOTE if remote else UPLOAD_MAX_LAN
    if n <= 0:
        return None, "choose a video file first"
    if n > limit:
        return None, "the file is larger than %d MB%s" % (limit // 1048576, " (the limit through the internet address; "
                                                          "on the home network it is 500 MB)" if remote else "")
    os.makedirs(UPLOADS, exist_ok=True)
    cleanup_uploads()
    jobs = uploads()
    if sum(u["state"] in ("uploading", "queued", "processing") for u in jobs) >= UPLOAD_MAX_JOBS:
        return None, "%d videos are waiting already; try again when one is done" % UPLOAD_MAX_JOBS
    # the result may be about as large as 4 Mb/s x 30 min x 1.5; reserve that, the file itself and the system reserve
    need = n + int(UPLOAD_MAX_S * 4e6 / 8 * 1.5)
    if du(UPLOADS) + need > UPLOAD_DIR_CAP:
        return None, "the upload space (4 GB) is full; delete older videos first"
    if free_bytes(UPLOADS) - need < UPLOAD_RESERVE:
        return None, "the device's disk is almost full"
    uid = secrets.token_hex(8)
    d = os.path.join(UPLOADS, uid)
    os.makedirs(d)
    part, left = os.path.join(d, "input.part"), n
    try:
        with open(part, "wb") as f:
            while left > 0:
                chunk = rfile.read(min(1048576, left))
                if not chunk:
                    raise OSError("the upload stopped before the end")
                f.write(chunk)
                left -= len(chunk)
                if left and (n - left) % (64 << 20) < 1048576 and free_bytes(UPLOADS) < UPLOAD_RESERVE:
                    raise OSError("the device's disk is almost full")
        meta, err = probe_video(part)
        if err:
            raise OSError(err)
    except (OSError, ValueError, socket.timeout) as e:
        shutil.rmtree(d, ignore_errors=True)
        return None, str(e) if isinstance(e, OSError) and not isinstance(e, socket.timeout) else "the upload stopped before the end"
    meta.update(name=re.sub(r"[^\w .()-]", "_", filename)[:80] or "video", size=n, created=time.time())
    os.replace(part, os.path.join(d, "input"))
    write_private(os.path.join(d, "meta.json"), json.dumps(meta))
    write_private(os.path.join(d, "state"), "queued")
    LOG.write("INFO", "video upload %s queued (%d MB, %.0f s)" % (uid, n // 1048576, meta["duration"]))
    return uid, None


def upload_action(action, uid):
    """cancel or delete; returns a FLASH key"""
    d = os.path.join(UPLOADS, uid)
    if not UPLOAD_ID_RE.match(uid) or not os.path.isdir(d):
        return "denied"
    state = up_read(d, "state", "uploading")
    if action == "cancel" or state == "processing":  # facepipe stops it within a second and marks it cancelled
        if state in ("queued", "processing"):
            open(os.path.join(d, "cancel"), "a").close()
            if state == "queued":
                write_private(os.path.join(d, "state"), "cancelled")
        return "up_cancelled"
    shutil.rmtree(d, ignore_errors=True)
    return "up_deleted"


def page_uploads(csrf, remote, flash=""):
    cleanup_uploads()
    st = status()
    label = {"uploading": ("Uploading", "out"), "queued": ("Waiting", "out"), "processing": ("Processing", "in"),
             "done": ("Done", "in"), "failed": ("Failed", "bad"), "cancelled": ("Cancelled", "out")}

    def form(action, uid, text, cls=""):
        return ("<form method='post' action='/uploads/%s'><input type='hidden' name='csrf' value='%s'>"
                "<input type='hidden' name='id' value='%s'><button class='btn %s' type='submit'>%s</button></form>" % (action, csrf, uid, cls, text))
    rows = []
    for u in uploads():
        m, s = u["meta"], u["state"]
        note = ""
        if s == "processing":
            p = u["progress"]
            pct = 100.0 * p.get("pos", 0) / max(1.0, p.get("duration", m.get("duration", 1)))
            note = ("waiting: people are in view of a live camera, which goes first" if p.get("state") == "waiting"
                    else "%d%% processed" % min(99, pct))
        elif s == "done":
            try:
                found = json.loads(up_read(u["dir"], "result.json", "[]"))
            except ValueError:
                found = []
            names = sorted({display_name(f["name"]) for f in found if f.get("name") != "unknown"})
            unknown = sum(1 for f in found if f.get("name") == "unknown")
            note = ", ".join(names + (["%d unknown" % unknown] if unknown else [])) or "no faces found"
        elif s == "failed":
            note = u["reason"]
        acts = ""
        if s == "done":
            acts += "<a class='btn primary' href='/uploads/%s/result.mp4'>Download</a>" % u["id"]
        if s in ("queued", "processing"):
            acts += form("cancel", u["id"], "Cancel")
        if s not in ("processing", "uploading"):
            acts += form("delete", u["id"], "Delete", "danger")
        lt, cls = label.get(s, (s, "out"))
        rows.append("<tr><td><b>%s</b><div class='m muted'>%s</div></td><td class='num'>%s</td><td><span class='badge %s'>%s</span>"
                    "<div class='m'>%s</div></td><td class='num'>%s</td><td class='acts'>%s</td></tr>" % (
                        esc(m.get("name", "video")), "%s, %d x %d, %d s" % (m.get("codec", ""), m.get("width", 0), m.get("height", 0),
                                                                             m.get("duration", 0)) if m else "",
                        "%.1f MB" % (m.get("size", 0) / 1048576.0), cls, lt, esc(note),
                        hms(datetime.datetime.fromtimestamp(m["created"]).isoformat()) if m.get("created") else "", acts))
    table = ("<table><thead><tr><th scope='col'>Video</th><th scope='col'>Size</th><th scope='col'>State</th>"
             "<th scope='col'>Uploaded</th><th scope='col'>Actions</th></tr></thead><tbody>%s</tbody></table>" % "".join(rows)) \
        if rows else "<p class='empty'>No videos uploaded.</p>"
    limit = (UPLOAD_MAX_REMOTE if remote else UPLOAD_MAX_LAN) // 1048576
    body = """<h1>Video upload</h1><p class="sub">Run face recognition on a recorded video. The device draws the boxes and names into a copy you can download. Uploaded videos never count as attendance and are deleted after 7 days.</p>
<section aria-labelledby="h-up"><div class="sh"><h2 id="h-up">Upload a video</h2><span class="count">H.264 or H.265, up to 1920 x 1080 and 30 minutes, %d MB</span></div>
<form id="upform" class="body form wide" data-limit="%d" data-csrf="%s">
<label class="field grow2"><span>Video file</span><input id="upfile" type="file" accept="video/*" required></label>
<div><button class="btn primary" type="submit">Upload</button> <progress id="upbar" max="100" value="0" hidden></progress> <span id="upmsg" class="m" role="status"></span></div>
<small class="muted">Live cameras always go first: a video is processed while no one is in view of a live camera.%s</small></form></section>
<section aria-labelledby="h-list"><div class="sh"><h2 id="h-list">Videos</h2><span class="count">%s</span></div>%s</section>""" % (
        limit, limit * 1048576, csrf,
        " Through the internet address the limit is 95 MB; on the home network 500 MB." if remote else "",
        "recognition " + {"live": "running", "no_video": "waiting for a camera", "stopped": "stopped: videos wait until it runs"}[st["state"]], table)
    return layout("uploads", "Video upload", body, flash=flash, refresh=any(u["state"] in ("queued", "processing") for u in uploads()))



# ---------- accounts (D-040): the owner (web_auth, CLI) plus admins and viewers added on the Users page ----------
# Viewers see everything an admin sees, read-only (owner, 2026-11-07): Overview, Live, Attendance, CSV, faces, evidence,
# clips, and the System and Cameras pages without their controls; not People, Video upload or Users, and no changes. Sign-in names are an email, a phone number
# (with +country code) or both; they are not verified. No email or SMS is sent: the admin passes the set-password link on.
ROLES = ("admin", "viewer")
EMAIL_RE = re.compile(r"^[^@\s<>\"'`]{1,64}@[^@\s<>\"'`]{1,190}\.[^@\s<>\"'`]{2,}$")
PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
UID_RE = re.compile(r"^u_[0-9a-f]{16}$")
LINK_RE = re.compile(r"^[0-9a-f]{32}$")
LINK_HOURS = 48
MIN_PW = 15  # NIST SP 800-63-4 for passwords used alone; spaces allowed, so a few words do
PUBLIC_URL = os.environ.get("FACEPIPE_PUBLIC_URL", "")  # e.g. https://attendance.example.com: set-password links use it
ADMIN_PAGES = ("people", "uploads", "users")
VIEWER_GET = ("/", "/live", "/live.mjpg", "/attendance", "/csv", "/api/status", "/api/health", "/system", "/cameras", "/account")
VIEWER_POST = ("/logout", "/account/password")
REQ = threading.local()  # the signed-in principal of this request's thread, for the page layout


def norm_email(s):
    s = (s or "").strip().lower()
    return s if EMAIL_RE.match(s) else None


def norm_phone(s):
    s = re.sub(r"[\s().-]", "", (s or "").strip())
    return s if PHONE_RE.match(s) else None


def public_origin():
    """FACEPIPE_PUBLIC_URL if it is a plain https origin, else "" (the page then adds the browser's own address)"""
    u = urlparse(PUBLIC_URL)
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/") or u.query or u.params or u.fragment or u.username:
        return ""
    return "https://" + u.netloc


def pw_problem(pw, *personal):
    if len(pw) < MIN_PW:
        return "Use at least %d characters; a few words with spaces work well." % MIN_PW
    if len(pw) > 256:
        return "Use at most 256 characters."
    if any(x and pw.strip().lower() == str(x).strip().lower() for x in personal):
        return "The password must not be your name, email or phone number."
    return None


def allowed(p, method, path):
    """the one authorization rule (D-040): admins everything; viewers the exact read-only routes below"""
    if p["role"] == "admin":
        return True
    if method in ("GET", "HEAD"):
        return path in VIEWER_GET or bool(
            (path.startswith("/rec/") and REC_RE.match(path[5:])) or (path.startswith("/snap/") and SNAP_RE.match(path[6:]))
            or re.match(r"^/photo/[^/]+$", path))
    return path in VIEWER_POST


def account_row(c, uid):
    return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def issue_link(uid):
    """a new single-use set-password link for the account; any older one stops working"""
    t = secrets.token_hex(16)
    with _db_lock, db() as c:
        c.execute("INSERT OR REPLACE INTO account_links VALUES (?,?,?)", (uid, token_hash(t), time.time() + LINK_HOURS * 3600))
    return t


def link_account(t):
    if not LINK_RE.match(t or ""):
        return None
    with db() as c:
        return c.execute("SELECT u.* FROM account_links l JOIN users u ON u.id = l.user WHERE l.hash=? AND l.expires>?",
                         (token_hash(t), time.time())).fetchone()


def redeem_link(t, pw):
    """sets the password and uses the link up in one transaction; returns an error text or None"""
    r = link_account(t)
    if not r:
        return "This link is not valid any more; ask for a new one."
    err = pw_problem(pw, r["name"], r["email"], r["phone"])
    if err:
        return err
    salt = os.urandom(16)
    digest = hash_pw(pw, salt)
    with _db_lock, db() as c:
        if c.execute("DELETE FROM account_links WHERE hash=? AND expires>?", (token_hash(t), time.time())).rowcount != 1:
            return "This link is not valid any more; ask for a new one."
        c.execute("UPDATE users SET salt=?, hash=?, version=version+1 WHERE id=?", (salt.hex(), digest, r["id"]))
    LOG.write("INFO", "account %s: password set with a link" % r["id"])
    return None


def set_account_password(uid, pw):
    salt = os.urandom(16)
    digest = hash_pw(pw, salt)
    with _db_lock, db() as c:
        c.execute("UPDATE users SET salt=?, hash=?, version=version+1 WHERE id=?", (salt.hex(), digest, uid))
        c.execute("DELETE FROM account_links WHERE user=?", (uid,))


def link_html(t):
    path = "/account/setup?t=" + t
    origin = public_origin()
    return ("<code class='link'>%s</code>" % esc(origin + path)) if origin else \
        "<code class='link' data-rel='%s'>%s</code>" % (esc(path), esc(path))


SHOWN_LINKS = {}  # one-time display of a new set-password link: nonce -> (account id, token, expires)


def page_users(csrf, owner_name, flash="", show=""):
    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY role, name").fetchall()
        pending = {r["user"]: r["expires"] for r in c.execute("SELECT user, expires FROM account_links").fetchall()}
    new = ""
    if show in SHOWN_LINKS and SHOWN_LINKS[show][2] > time.time():
        uid, t, _ = SHOWN_LINKS.pop(show)
        who = next((r["name"] for r in rows if r["id"] == uid), "the user")
        new = ("<div class='flash ok' role='status'>Send this link to %s yourself (WhatsApp, email). It works once, for %d hours, "
               "and is shown only now: %s</div>" % (esc(who), LINK_HOURS, link_html(t)))
    reauth = ("<label class='field'><span>Your password</span><input name='current' type='password' required maxlength='256' "
              "autocomplete='current-password'></label>")

    def act(action, uid, label, cls="", extra=""):
        return ("<details class='act'><summary class='btn %s'>%s</summary><form method='post' action='/users/%s' class='form'>"
                "<input type='hidden' name='csrf' value='%s'><input type='hidden' name='id' value='%s'>%s%s"
                "<button class='btn %s' type='submit'>%s</button></form></details>" % (cls, label, action, csrf, uid, extra, reauth, cls, label))
    trs = ["<tr><td><b>%s</b><div class='m muted'>owner, set on the device</div></td><td>%s</td><td><span class='badge in'>Admin</span></td>"
           "<td class='m muted'>&ndash;</td><td></td></tr>" % (esc(owner_name), esc(owner_name))]
    for r in rows:
        names = " &middot; ".join(esc(x) for x in (r["email"], r["phone"]) if x)
        state = ("link sent, valid until %s" % hm(datetime.datetime.fromtimestamp(pending[r["id"]]).isoformat())) if r["id"] in pending \
            else ("" if r["hash"] else "link expired: make a new one")
        last = hms(datetime.datetime.fromtimestamp(r["last_login"]).isoformat()) if r["last_login"] else "never"
        other = "viewer" if r["role"] == "admin" else "admin"
        trs.append("<tr><td><b>%s</b><div class='m muted'>%s</div></td><td>%s</td><td><span class='badge %s'>%s</span></td>"
                   "<td class='m muted'>%s</td><td class='acts'>%s%s%s</td></tr>" % (
                       esc(r["name"]), esc(state), names, "in" if r["role"] == "admin" else "out", r["role"].title(), last,
                       act("role", r["id"], "Make " + other, extra="<input type='hidden' name='role' value='%s'>" % other),
                       act("link", r["id"], "New link"), act("delete", r["id"], "Delete", "danger")))
    body = """<h1>Users</h1><p class="sub">Who can sign in. Admins can do everything; viewers can see everything (attendance, faces, evidence, live view) but change nothing.</p>%s
<section aria-labelledby="h-u"><div class="sh"><h2 id="h-u">Accounts</h2><span class="count">%d</span></div>
<table><thead><tr><th scope="col">Name</th><th scope="col">Signs in with</th><th scope="col">Role</th><th scope="col">Last sign-in</th><th scope="col">Actions</th></tr></thead><tbody>%s</tbody></table></section>
<section aria-labelledby="h-add"><div class="sh"><h2 id="h-add">Add a user</h2></div>
<form method="post" action="/users/add" class="body form wide" autocomplete="off"><input type="hidden" name="csrf" value="%s">
<div class="row"><label class="field"><span>Name</span><input name="name" required maxlength="60"></label>
<label class="field"><span>Role</span><select name="role"><option value="viewer">Viewer (sees everything, changes nothing)</option><option value="admin">Admin</option></select></label></div>
<div class="row"><label class="field"><span>Email (optional)</span><input name="email" type="email" maxlength="254" autocomplete="off"></label>
<label class="field"><span>Phone (optional, with country code)</span><input name="phone" type="tel" maxlength="24" placeholder="+91 98765 43210" autocomplete="off"></label></div>
<div class="row">%s</div>
<small class="muted">At least an email or a phone number; either signs in. You get a set-password link to send the person yourself; the device sends nothing.</small>
<div><button class="btn primary" type="submit">Add user</button></div></form></section>""" % (
        new, len(rows) + 1, "".join(trs), csrf, reauth)
    return layout("users", "Users", body, flash=flash)


def users_post(server, actor, path, form):
    """handles /users/<action>; returns (FLASH key, nonce of a link to show once or "")"""
    get = lambda k: form.get(k, [""])[0]
    if not server.verify(actor, get("current")):
        return "u_reauth", ""
    action = path.rsplit("/", 1)[-1]
    if action == "add":
        name, role = get("name").strip(), get("role")
        email, phone = get("email").strip(), get("phone").strip()
        e, ph = norm_email(email), norm_phone(phone)
        c_owner = server.creds()
        if not NAME_RE.match(name) or role not in ROLES or (email and not e) or (phone and not ph) or not (e or ph):
            return "u_bad", ""
        if c_owner and any(x and x == c_owner[0].lower() for x in (e, ph)):
            return "u_taken", ""
        uid = "u_" + secrets.token_hex(8)
        try:
            with _db_lock, db() as c:
                c.execute("INSERT INTO users (id, name, email, phone, role, version, created) VALUES (?,?,?,?,?,0,?)",
                          (uid, name, e, ph, role, time.time()))
        except sqlite3.IntegrityError:
            return "u_taken", ""
        LOG.write("INFO", "account %s added as %s by %s" % (uid, role, actor["uid"]))
        nonce = secrets.token_hex(8)
        SHOWN_LINKS.clear()
        SHOWN_LINKS[nonce] = (uid, issue_link(uid), time.time() + 600)
        return "u_added", nonce
    uid = get("id")
    if not UID_RE.match(uid):
        return "denied", ""
    with db() as c:
        r = account_row(c, uid)
    if not r:
        return "denied", ""
    if action == "role" and get("role") in ROLES:
        with _db_lock, db() as c:  # the version change ends the account's sessions (D-040)
            c.execute("UPDATE users SET role=?, version=version+1 WHERE id=?", (get("role"), uid))
        LOG.write("INFO", "account %s made %s by %s" % (uid, get("role"), actor["uid"]))
        return "u_role", ""
    if action == "link":
        nonce = secrets.token_hex(8)
        SHOWN_LINKS.clear()
        SHOWN_LINKS[nonce] = (uid, issue_link(uid), time.time() + 600)
        LOG.write("INFO", "account %s: new set-password link by %s" % (uid, actor["uid"]))
        return "u_link", nonce
    if action == "delete":
        with _db_lock, db() as c:
            c.execute("DELETE FROM account_links WHERE user=?", (uid,))
            c.execute("DELETE FROM users WHERE id=?", (uid,))
        LOG.write("INFO", "account %s deleted by %s" % (uid, actor["uid"]))
        return "u_deleted", ""
    return "denied", ""


def page_account(p, csrf, flash=""):
    with db() as c:
        r = account_row(c, p["uid"]) if p["uid"] != "owner" else None
    names = " &middot; ".join(esc(x) for x in ((r["email"], r["phone"]) if r else (p["name"],)) if x)
    change = ("<p class='body muted'>The owner's password is set on the device: <code>python3 web.py --set-password %s</code>.</p>" % esc(p["name"])
              if p["uid"] == "owner" else """<form method="post" action="/account/password" class="body form"><input type="hidden" name="csrf" value="%s">
<label class="field"><span>Current password</span><input name="current" type="password" required maxlength="256" autocomplete="current-password"></label>
<label class="field"><span>New password (at least %d characters)</span><input name="password" type="password" required minlength="%d" maxlength="256" autocomplete="new-password"></label>
<label class="field"><span>New password again</span><input name="again" type="password" required maxlength="256" autocomplete="new-password"></label>
<div><button class="btn primary" type="submit">Change password</button></div></form>""" % (csrf, MIN_PW, MIN_PW))
    body = """<h1>Your account</h1><p class="sub">%s, %s. Signs in with %s.</p>
<section aria-labelledby="h-pw"><div class="sh"><h2 id="h-pw">Password</h2></div>%s</section>""" % (
        esc(p["name"]), "administrator" if p["role"] == "admin" else "viewer (sees everything, changes nothing)", names, change)
    return layout("account", "Your account", body, flash=flash)


def page_setup(t, ok, error=""):
    if not ok:
        body = "<h1>Link not valid</h1><p class='sub'>This set-password link was used already or has expired. Ask the administrator for a new one.</p>"
        return public_page("Set your password", body, "login", "Account", "Recognition runs on this device; nothing leaves it.")
    body = """<h1>Set your password</h1><p class="sub">Choose a password of at least %d characters; a few words with spaces work well. Then sign in with your email or phone number.</p>
<div class="flash bad"%s role="alert">%s</div>
<section><form class="body form" method="post" action="/account/setup"><input type="hidden" name="t" value="%s">
<label class="field"><span>New password</span><input name="password" type="password" required minlength="%d" maxlength="256" autocomplete="new-password" autofocus></label>
<label class="field"><span>Again</span><input name="again" type="password" required maxlength="256" autocomplete="new-password"></label>
<button class="btn primary submit" type="submit">Set password</button></form></section>""" % (
        MIN_PW, "" if error else " hidden", esc(error), esc(t), MIN_PW)
    return public_page("Set your password", body, "login", "Account", "Recognition runs on this device; nothing leaves it.")


def lan_address(ip):
    """private, link-local or loopback: the local network (D-041; the same rule facepipe applies to RTSP viewers)"""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    if a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"):  # carrier-grade NAT: the ISP's network, not ours
        return False
    return a.is_private or a.is_link_local or a.is_loopback


# ---------- server ----------
MAX_BODY = 4096       # forms here are tiny
MAX_REQUESTS = 24     # concurrent requests; each thread costs memory on a 4 GB device
MAX_LIVE = 4          # concurrent MJPEG viewers
FAIL_LIMIT, FAIL_WINDOW = 10, 900  # failed logins per client before refusing without hashing (PBKDF2 is costly)
WEB_STARTED = int(time.time())
SESSION_HOURS = 12
COOKIE = "fp_session"
TOKEN_RE = re.compile(r"^v2\.(owner|u_[0-9a-f]{16})\.([0-9]{10})\.([0-9a-f]{64})$")  # v2.<account>.<expiry>.<mac> (D-040)
PAGE_PATHS = ("/", "/live", "/attendance", "/people", "/cameras", "/uploads", "/users", "/account", "/system")  # where a sign-in may lead back to
PROTECTED = ("/api/", "/live.mjpg", "/rec/", "/snap/", "/photo/", "/pending/", "/csv", "/uploads/")  # 401 before sign-in, never 404
NEXT_RE = re.compile(r"^/(live|attendance|people|cameras|uploads|users|account|system)?(\?[A-Za-z0-9=&_.:-]{0,200})?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "facepipe"
    sys_version = ""
    timeout = 30  # a client that stops sending is dropped

    def client_ip(self):
        """the visitor's address: behind the Cloudflare Tunnel every request comes from cloudflared on this host, so the
        CF-Connecting-IP header names the visitor; it is trusted only on connections from this host"""
        ip = self.client_address[0]
        cf = self.headers.get("CF-Connecting-IP", "")
        if ip in ("127.0.0.1", "::1") and cf and len(cf) <= 45 and re.match(r"^[0-9A-Fa-f:.]+$", cf):
            return cf
        return ip

    def on_lan(self):
        """a visitor on the local network, not through the Cloudflare Tunnel (D-041)"""
        if self.headers.get("CF-Connecting-IP") and self.client_address[0] in ("127.0.0.1", "::1"):
            return False
        return lan_address(self.client_address[0])

    def https(self):
        """the visitor used HTTPS: only the Cloudflare Tunnel (cloudflared on this host) says so, in X-Forwarded-Proto;
        the same header from any other machine is ignored"""
        return self.client_address[0] in ("127.0.0.1", "::1") and self.headers.get("X-Forwarded-Proto") == "https"

    def user(self):
        """the signed-in principal from the session cookie, or None (D-036, D-040)"""
        self.token = ""
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                self.token = v
        self.principal = REQ.p = self.server.session_principal(self.token)
        return self.principal

    def csrf(self):
        p = getattr(self, "principal", None)
        return self.server.csrf_for(p) if p else ""

    def cookie(self, value, age):
        return "%s=%s; Max-Age=%d; Path=/; HttpOnly; SameSite=Lax%s" % (COOKIE, value, age, "; Secure" if self.https() else "")

    def login_get(self, q):
        nxt = q.get("next", ["/"])[0]
        nxt = nxt if NEXT_RE.match(nxt) else "/"
        if self.user():
            return self.redirect(nxt)
        self.html_page(page_login(nxt, info="Password set. Sign in with your email or phone number." if q.get("msg") == ["pw_set"] else ""))

    def login_post(self):
        """public: user name and password; refused from other sites, throttled per visitor before the costly hash"""
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.send(413, "Too large", "text/plain; charset=utf-8")
        form = parse_qs(self.rfile.read(n).decode(errors="replace"))
        get = lambda k: form.get(k, [""])[0]
        nxt = get("next") if NEXT_RE.match(get("next")) else "/"
        ip, now = self.client_ip(), time.time()
        fails = [t for t in self.server.fails.get(ip, []) if now - t < FAIL_WINDOW]
        page = lambda code, msg: self.send(code, page_login(nxt, msg), extra={"Content-Security-Policy": CSP})
        if not self.same_origin():
            LOG.write("WARNING", "sign-in refused from %s (origin)" % ip)
            return page(403, "The form was refused; open the sign-in page again.")
        if len(fails) >= FAIL_LIMIT:
            return page(429, "Too many failed attempts; try again in 15 minutes.")
        if not self.server.creds():
            return page(503, "No login is set on this device.")
        p = self.server.find_login(get("user"))
        if p and self.server.cooling(p["uid"]):  # a short pause for this account after repeated failures (D-040)
            return page(429, "Too many attempts for this account; wait a few seconds and try again.")
        if self.server.verify(p, get("password")):  # unknown names cost the same hashing
            self.server.fails.pop(ip, None)
            self.server.acct_ok(p["uid"])
            if p["uid"] != "owner":
                with _db_lock, db() as c:
                    c.execute("UPDATE users SET last_login=? WHERE id=?", (now, p["uid"]))
            LOG.write("INFO", "signed in: %s (%s) from %s" % (p["uid"], p["role"], ip))
            exp = int(now) + SESSION_HOURS * 3600
            nxt = nxt if allowed(p, "GET", urlparse(nxt).path) else "/"
            return self.redirect(nxt, {"Set-Cookie": self.cookie(self.server.session_token(p, exp), SESSION_HOURS * 3600)})
        self.server.fails[ip] = fails + [now]
        if p:
            self.server.acct_fail(p["uid"])
        LOG.write("WARNING", "sign-in failed from %s" % ip)
        return page(401, "Wrong sign-in name or password.")

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
        if CSRF_MARK in body:  # the layout's sign-out form
            body = body.replace(CSRF_MARK, self.csrf())
        self.send(200, body, extra={"Content-Security-Policy": CSP})

    def redirect(self, where, extra=None):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.headers_common()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def file(self, path, ctype=None, cache="private, max-age=86400"):
        """file with HTTP Range support (browsers need it to seek in MP4 clips), streamed in 64 KB chunks"""
        ctype = ctype or CTYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        # a missing file is a 404, not an exception in the handler thread: that drops the connection with no answer,
        # and every caller here (static assets, clips, evidence photos) can be asked for a file that is not there
        if not os.path.isfile(path):
            return self.send(404, "Not found", "text/plain; charset=utf-8")
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
        for k, v in getattr(self, "extra_headers", {}).items():
            self.send_header(k, v)
        self.headers_common()
        self.end_headers()
        if self.command == "HEAD":
            return
        left = end - start + 1
        p, sent, at = getattr(self, "principal", None), 0, time.time()
        with open(path, "rb") as f:
            f.seek(start)
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
                sent += len(chunk)
                if p and (sent >= 2 << 20 or time.time() - at > 3):  # a signed-in download: still allowed? (D-040)
                    sent, at = 0, time.time()
                    q = self.server.session_principal(self.token)
                    if not q or not allowed(q, "GET", urlparse(self.path).path):
                        self.close_connection = True
                        break

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
                    q = self.server.session_principal(self.token)  # expired, password or role changed, deleted
                    if not q or not allowed(q, "GET", "/live.mjpg"):
                        break
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
        """POST guard: the browser's Origin (or Referer) must be this site, scheme included; other sites cannot post here"""
        host = self.headers.get("Host", "")
        u = urlparse(self.headers.get("Origin") or self.headers.get("Referer") or "")
        return bool(host) and "%s://%s" % (u.scheme, u.netloc) == "%s://%s" % ("https" if self.https() else "http", host)

    def login_required(self, url):
        """pages go to the sign-in page and come back after it; data, video and files get a plain 401 (no browser pop-up)"""
        if url.path in PAGE_PATHS:
            nxt = url.path + ("?" + url.query if url.query else "")
            return self.redirect("/login?next=" + quote(nxt if NEXT_RE.match(nxt) else "/", safe=""))
        if url.path.startswith(PROTECTED):  # whether a file exists is not told before sign-in
            return self.send(401, "Login required", "text/plain; charset=utf-8")
        return self.not_found()

    def not_found(self):
        self.send(404, page_not_found(), extra={"Content-Security-Policy": CSP})

    def do_HEAD(self):
        self.do_GET()

    def forbidden(self, path):
        """a signed-in viewer on an admin route (D-040)"""
        if path in PAGE_PATHS:
            body = layout("overview", "Not available", "<h1>Not available</h1><p class='sub'>This page is for administrators. "
                          "Your account can see Overview, Live, Attendance, Cameras and System.</p>")
            return self.send(403, body.replace(CSRF_MARK, self.csrf()), extra={"Content-Security-Policy": CSP})
        return self.send(403, "Not allowed for this account", "text/plain; charset=utf-8")

    def do_GET(self):
        self.extra_headers = {}  # per request (a kept-alive connection reuses this handler)
        self.principal = REQ.p = None
        url = urlparse(self.path)
        if url.path.startswith("/static/") and url.path[8:] in STATIC:  # public assets, no data in them
            return self.file(os.path.join(WEB_DIR, url.path[8:]), STATIC[url.path[8:]], cache="public, max-age=3600")
        q = parse_qs(url.query)
        if url.path == "/register":  # public: the invitation token is the permission
            t = q.get("t", [""])[0]
            return self.html_page(page_register(valid_invite(t), t))
        if url.path == "/register/done":
            return self.html_page(page_register_done())
        if url.path == "/login":
            return self.login_get(q)
        if url.path == "/account/setup":  # public: the single-use link is the permission; viewing does not use it up
            t = q.get("t", [""])[0]
            return self.html_page(page_setup(t, bool(link_account(t))))
        user = self.user()
        if not user:
            return self.login_required(url)
        if not allowed(user, self.command, url.path):
            return self.forbidden(url.path)
        if url.path == "/users":
            return self.html_page(page_users(self.csrf(), self.server.creds()[0], q.get("msg", [""])[0], q.get("show", [""])[0]))
        if url.path == "/account":
            return self.html_page(page_account(user, self.csrf(), q.get("msg", [""])[0]))
        if url.path == "/":
            return self.html_page(page_overview())
        if url.path == "/live":
            return self.html_page(page_live((self.headers.get("Host") or "this-device").rsplit(":", 1)[0], self.on_lan()))
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
            return self.html_page(page_people(self.csrf(), host, q.get("show", [""])[0], q.get("msg", [""])[0]))
        m = re.match(r"^/pending/([0-9a-f]{12})/([1-5])\.jpg$", url.path)
        if m and os.path.exists(os.path.join(REG_DIR, m.group(1), m.group(2) + ".jpg")):
            return self.file(os.path.join(REG_DIR, m.group(1), m.group(2) + ".jpg"), cache="no-store")
        if url.path == "/cameras":
            return self.html_page(page_cameras(self.csrf(), q.get("msg", [""])[0], read_only=user["role"] != "admin"))
        if url.path == "/system":
            return self.html_page(page_system(self.csrf(), q.get("msg", [""])[0], user["role"] != "admin"))
        if url.path == "/uploads":
            return self.html_page(page_uploads(self.csrf(), self.https(), q.get("msg", [""])[0]))
        m = re.match(r"^/uploads/([0-9a-f]{16})/result\.mp4$", url.path)
        if m and up_read(os.path.join(UPLOADS, m.group(1)), "state") == "done":
            name = os.path.splitext(json.loads(up_read(os.path.join(UPLOADS, m.group(1)), "meta.json", "{}")).get("name", "video"))[0]
            self.extra_headers = {"Content-Disposition": "attachment; filename=\"%s-recognized.mp4\"" % re.sub(r"[^\w.-]", "_", name)}
            return self.file(os.path.join(UPLOADS, m.group(1), "result.mp4"), cache="no-store")
        if url.path == "/live.mjpg":
            return self.live()
        if url.path == "/api/health":  # the panel's markup, so the page needs no template of its own
            return self.send(200, json.dumps({"html": health_html(device_health())}), "application/json")
        if url.path == "/api/status":  # web_started lets an open page notice a restart of this server
            return self.send(200, json.dumps(dict(status(), web_started=WEB_STARTED)), "application/json")
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
        self.not_found()

    def register_post(self):
        """public: name, photos and consent for a valid invitation"""
        fetch = self.headers.get("X-Requested-With") == "fetch"
        ip, now = self.client_ip(), time.time()
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

    def upload_post(self):
        """a video as the raw request body (sent by the page's script with a progress bar); answers JSON"""
        ok = self.same_origin() and hmac.compare_digest(self.headers.get("X-CSRF", "").encode(errors="replace"), self.csrf().encode())
        try:
            n = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            n = -1
        if not ok or n < 0:
            self.close_connection = True  # the body is not read
            return self.send(403 if not ok else 411, json.dumps({"ok": False, "error": "refused; reload the page"}), "application/json")
        uid, err = receive_upload(self.rfile, n, unquote(self.headers.get("X-Filename", "video"))[:200], self.https())
        if err:
            self.close_connection = True
            LOG.write("WARNING", "video upload refused: %s" % err)
            return self.send(400, json.dumps({"ok": False, "error": err}), "application/json")
        return self.send(200, json.dumps({"ok": True, "id": uid}), "application/json")

    def setup_post(self):
        """public: a new password through a set-password link (D-040)"""
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.send(413, "Too large", "text/plain; charset=utf-8")
        form = parse_qs(self.rfile.read(n).decode(errors="replace"))
        get = lambda k: form.get(k, [""])[0]
        ip, now = self.client_ip(), time.time()
        fails = [t for t in self.server.fails.get(ip, []) if now - t < FAIL_WINDOW]
        t = get("t")
        page = lambda code, msg, ok=True: self.send(code, page_setup(t, ok, msg), extra={"Content-Security-Policy": CSP})
        if not self.same_origin():
            return page(403, "The form was refused; open the link again.")
        if len(fails) >= FAIL_LIMIT:
            return page(429, "Too many attempts; try again in 15 minutes.")
        if not link_account(t):
            self.server.fails[ip] = fails + [now]
            return page(403, "", ok=False)
        if get("password") != get("again"):
            return page(400, "The two passwords differ.")
        with self.server.hash_slots:
            err = redeem_link(t, get("password"))
        if err:
            return page(400, err, ok=bool(link_account(t)))
        return self.redirect("/login?msg=pw_set")

    def do_POST(self):
        self.principal = REQ.p = None
        if urlparse(self.path).path == "/register":
            return self.register_post()
        if urlparse(self.path).path == "/login":
            return self.login_post()
        if urlparse(self.path).path == "/account/setup":
            return self.setup_post()
        user = self.user()
        if not user:
            return self.send(401, "Login required", "text/plain; charset=utf-8")
        if not allowed(user, "POST", urlparse(self.path).path):  # before any body is read (D-040)
            self.close_connection = True
            return self.send(403, "Not allowed for this account", "text/plain; charset=utf-8")
        if urlparse(self.path).path == "/uploads/add":
            return self.upload_post()
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.send(413, "Too large", "text/plain; charset=utf-8")
        form = parse_qs(self.rfile.read(n).decode(errors="replace"))
        token = form.get("csrf", [""])[0]
        if not self.same_origin() or not hmac.compare_digest(token.encode(errors="replace"), self.csrf().encode()):
            LOG.write("WARNING", "POST %s refused from %s (origin or form token)" % (self.path[:60], self.client_ip()))
            back = "/" + (self.path.split("/")[1] if self.path.split("/")[1] in ("people", "cameras", "uploads", "users", "account") else
                          ("system" if user["role"] == "admin" else ""))
            return self.redirect(back + "?msg=denied")
        path = urlparse(self.path).path
        if path == "/logout":
            LOG.write("INFO", "signed out from %s" % self.client_ip())
            return self.redirect("/login", {"Set-Cookie": self.cookie("", 0)})
        if path == "/system/matching":
            with _enroll_lock:
                return self.redirect("/system?msg=" + save_matching(form))
        if path == "/system/service":
            return self.redirect("/system?msg=" + service_action(form.get("action", [""])[0], user["name"]))
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
                return self.html_page(page_cameras(self.csrf(), msg, test))
            return self.redirect("/cameras?msg=" + msg)
        if path.startswith("/users/"):
            msg, show = users_post(self.server, user, path, form)
            return self.redirect("/users?msg=" + msg + ("&show=" + show if show else ""))
        if path == "/account/password":
            if user["uid"] == "owner" or not self.server.verify(user, form.get("current", [""])[0]):
                return self.redirect("/account?msg=pw_wrong")
            new = form.get("password", [""])[0]
            if new != form.get("again", [""])[0]:
                return self.redirect("/account?msg=pw_mismatch")
            with db() as c:
                r = account_row(c, user["uid"])
            if pw_problem(new, r["name"], r["email"], r["phone"]):
                return self.redirect("/account?msg=pw_weak")
            with self.server.hash_slots:
                set_account_password(user["uid"], new)
            LOG.write("INFO", "account %s changed its password" % user["uid"])
            return self.redirect("/login", {"Set-Cookie": self.cookie("", 0)})
        if path in ("/uploads/cancel", "/uploads/delete"):
            return self.redirect("/uploads?msg=" + upload_action(path.rsplit("/", 1)[1], form.get("id", [""])[0]))
        if path == "/people/remove":
            person = form.get("person", [""])[0]
            if person not in enrolled() or not KEY_RE.match(person):
                return self.redirect("/people?msg=denied")
            return self.redirect("/people?msg=" + ("removed" if remove_person(person) else "remove_failed"))
        self.send(404, "Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # no access log (the page polls every few seconds); failed logins and errors are logged

    def log_error(self, fmt, *args):
        LOG.write("ERROR", "%s: %s" % (self.client_ip(), fmt % args))


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def __init__(self, *a, **kw):
        HTTPServer.__init__(self, *a, **kw)
        self.slots = threading.BoundedSemaphore(MAX_REQUESTS)
        self.live_slots = threading.BoundedSemaphore(MAX_LIVE)
        self.fails = {}
        self.reg_hits = {}
        self.acct, self.acct_lock = {}, threading.Lock()  # account id -> [failures, cooldown until] (D-040)
        self.hash_slots = threading.BoundedSemaphore(2)     # at most 2 password hashes at once on the Nano
        self.auth_file, self._auth, self._auth_sig = AUTH_FILE, None, None
        os.makedirs(DATA, exist_ok=True)
        self.key = session_key(os.path.join(DATA, "session.key"))

    def creds(self):
        """(user, salt, hash) from web_auth, read again whenever the file changes, so --set-password takes effect at once
        and ends every session; None when it is missing or damaged (nobody gets in)"""
        try:
            st = os.stat(self.auth_file)
            sig = (st.st_ino, st.st_mtime_ns, st.st_size)
            if sig != self._auth_sig:
                self._auth, self._auth_sig = load_auth(self.auth_file), sig
            return self._auth
        except (OSError, ValueError):
            return None

    def owner(self):
        """the owner principal (web_auth): always an admin, version 0, its hash changes with --set-password"""
        c = self.creds()
        return {"uid": "owner", "name": c[0], "role": "admin", "salt": c[1], "digest": c[2], "version": 0} if c else None

    def principal(self, uid):
        """an account with a password, or None (also when the database fails: closed, D-040)"""
        if uid == "owner":
            return self.owner()
        try:
            with db() as c:
                r = c.execute("SELECT * FROM users WHERE id=? AND hash IS NOT NULL", (uid,)).fetchone()
        except sqlite3.Error:
            return None
        return {"uid": r["id"], "name": r["name"], "role": r["role"], "salt": bytes.fromhex(r["salt"]), "digest": r["hash"],
                "version": r["version"]} if r else None

    def find_login(self, ident):
        """the principal for a sign-in name: the owner's user name, or an account's email or phone number"""
        c = self.creds()
        if c and hmac.compare_digest(ident.encode(errors="replace"), c[0].encode()):
            return self.owner()
        e, ph = norm_email(ident), norm_phone(ident)
        if not (e or ph):
            return None
        try:
            with db() as cn:
                r = cn.execute("SELECT id FROM users WHERE (email=? OR phone=?) AND hash IS NOT NULL", (e, ph)).fetchone()
        except sqlite3.Error:
            return None
        return self.principal(r["id"]) if r else None

    def verify(self, p, pw):
        """password check with the same hashing work whether or not the account exists"""
        with self.hash_slots:
            d = hash_pw(pw, p["salt"] if p else b"\0" * 16)
        return bool(p) and hmac.compare_digest(d.encode(), p["digest"].encode())

    def cooling(self, uid):
        with self.acct_lock:
            f = self.acct.get(uid)
            return bool(f and f[1] > time.time())

    def acct_fail(self, uid):
        """after 5 failures a pause doubling up to 30 s; no long lockout (anyone could lock a family member out)"""
        with self.acct_lock:
            if len(self.acct) > 1000:  # ponytail: bounded by clearing; a per-entry expiry if this ever matters
                self.acct.clear()
            f = self.acct.setdefault(uid, [0, 0.0])
            f[0] += 1
            if f[0] >= 5:
                f[1] = time.time() + min(2 ** (f[0] - 5), 30)

    def acct_ok(self, uid):
        with self.acct_lock:
            self.acct.pop(uid, None)

    def sign(self, purpose, p, *parts):
        """HMAC over a purpose, the account, its version and password hash: a role or password change, or deleting the
        account, ends its sessions and open forms (D-036, D-040)"""
        return hmac.new(self.key, "|".join((purpose, p["uid"], str(p["version"]), p["digest"]) + parts).encode(),
                        hashlib.sha256).hexdigest()

    def csrf_for(self, p):
        return self.sign("csrf:v2", p)

    def session_token(self, p, exp):
        return "v2.%s.%d.%s" % (p["uid"], exp, self.sign("session:v2", p, str(exp)))

    def session_principal(self, token):
        """the principal for a valid, unexpired session token, else None"""
        m = TOKEN_RE.match(token or "")
        if not m or not time.time() < int(m.group(2)) <= time.time() + SESSION_HOURS * 3600:
            return None
        p = self.principal(m.group(1))
        return p if p and hmac.compare_digest(self.sign("session:v2", p, m.group(2)).encode(), m.group(3).encode()) else None

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
    # the shown number follows the rule that decided (owner: "-0.04" under L2 looked wrong)
    assert match_text({"score": -0.035, "metric": "l2", "l2": 1.439}) == "L2 1.44" and match_text({"score": 0.78}) == "Cosine 0.78"
    assert match_text({"score": 0.6, "metric": "cosine", "l2": 0.89}) == "Cosine 0.60" and match_text(p["best_e"]) == "Cosine 0.70"
    assert rule_text().startswith(("L2 ≤ ", "Cosine ≥ "))
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
    # the service's state on every page (owner, 2026-11-07): a wanted stop says who and when
    import tempfile
    g = globals()
    saved = g["DESIRED"], g["SERVICE_LAST"]
    tmp = tempfile.mkdtemp()
    g["DESIRED"], g["SERVICE_LAST"] = os.path.join(tmp, "desired"), os.path.join(tmp, "last")
    try:
        write_private(g["DESIRED"], "stopped")
        write_private(g["SERVICE_LAST"], json.dumps({"action": "stop", "by": "Hamza", "at": time.time()}))
        state, head, text = service_view()
        assert state == "stopped" and "STOPPED" in head and "Stopped by Hamza at" in text and "svc-banner stopped" in service_banner()
        write_private(g["DESIRED"], "running")
        assert service_view()[0] in ("stopped", "no_video", "live")  # from status.json, whatever this machine has
        write_private(g["SERVICE_LAST"], "not json")
        assert last_action() == ""
    finally:
        g["DESIRED"], g["SERVICE_LAST"] = saved
        shutil.rmtree(tmp)
    assert save_matching({"metric": ["l2"], "cosine": ["0.5"], "l2": ["2.0"]}) == "match_bad"  # out of range: nothing written
    assert not os.path.exists(MATCH_CONF) and "Cosine similarity" in matching_html("t")
    h = device_health()  # whatever this machine exposes; the panel must render either way
    assert "Temperature" in health_html(h) and h["level"] in ("ok", "warn", "bad") and isinstance(h["cpu"], list)
    # the banner must name which of the two is wrong, on any machine: these do not depend on real sensors
    def banner_for(temps, avail):
        fake = dict(h, temps=temps, mem=dict(h["mem"], total=3963, available=avail))
        hottest = max(temps.values()) if temps else 0
        fake["level"] = "bad" if hottest >= HOT_C else "warn" if hottest >= WARN_C or avail < LOW_MEM_MB else "ok"
        return health_html(fake).split("</div>")[0]
    assert "running hot: CPU is at 85" in banner_for({"CPU": 85.0}, 2000), banner_for({"CPU": 85.0}, 2000)
    assert "low on memory: 200 MB of 3963 MB free" in banner_for({"CPU": 50.0}, 200)
    assert "running hot" in banner_for({"CPU": 85.0}, 200) and "low on memory" in banner_for({"CPU": 85.0}, 200)
    assert "Too hot: CPU is at 92" in banner_for({"CPU": 92.0}, 2000)
    assert "flash" not in banner_for({"CPU": 50.0}, 2000)
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
    srv.auth_file = os.path.join(DATA, "selftest-auth")
    write_auth(srv.auth_file, "admin", "secret123")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    host = "127.0.0.1:%d" % srv.server_address[1]
    good = None  # the session cookie, after signing in below

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None  # report the 303 itself
    opener = urllib.request.build_opener(NoRedirect)

    def get(path, auth="", headers=None, data=None):
        h = dict(headers or {})
        if auth or (auth == "" and good):
            h["Cookie"] = auth or good
        req = urllib.request.Request(base + path, data=data, headers=h)
        try:
            r = opener.open(req, timeout=10)
            return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
    good = selftest_login(srv, get, host)
    assert get("/static/app.css", auth=None)[0] == 200 and get("/static/../web_auth", auth=None)[0] == 404
    assert get("/static/favicon.svg", auth=None)[1]["Content-Type"] == "image/svg+xml"
    # a file the release forgot to ship must answer 404, not drop the connection (docs/bugs/favicon-missing-500.md)
    logo = os.path.join(WEB_DIR, "logo.svg")
    os.rename(logo, logo + ".hidden")
    try:
        assert get("/static/logo.svg", auth=None)[0] == 404
    finally:
        os.rename(logo + ".hidden", logo)
    assert all(lan_address(a) for a in ("192" ".168.0.9", "10" ".1.2.3", "172" ".16.0.1", "127.0.0.1", "fe80::1", "fd00::3",
                                        "::ffff:192" ".168.0.9"))  # private examples in two pieces: ci/scan.sh
    assert not any(lan_address(a) for a in ("8.8.8.8", "100.64.0.1", "2401:4900::1", "::ffff:8.8.8.8", "x"))
    code, hd, body = get("/no-such-page", auth=None)
    assert code == 404 and b"Page not found" in body and "script-src 'self'" in hd["Content-Security-Policy"]
    assert get("/no-such-page")[0] == 404 and get("/rec/2026-11-06/000000_clip.mp4", auth=None)[0] == 401
    assert get("/rec/2026-11-06/000000_clip.mp4")[0] == 404
    for p in ("/", "/live", "/attendance", "/attendance?date=" + day, "/people", "/system"):
        code, hd, body = get(p)
        assert code == 200 and "script-src 'self'" in hd.get("Content-Security-Policy", ""), (p, code)
        assert b"aria-current='page'" in body and b"<script>x" not in body, p
    assert b"&lt;script&gt;x&lt;/script&gt;" in get("/attendance")[2]
    assert json.loads(get("/api/status")[2].decode())["web_started"] == WEB_STARTED
    assert json.loads(get("/api/health")[2].decode())["html"].count("hcard") == 4 and get("/api/health", auth=None)[0] == 401
    assert get("/attendance?date=2026-02-30")[0] == 400 and get("/csv?date=../x")[0] == 400
    code, hd, body = get("/static/app.js", headers={"Range": "bytes=0-9"})
    assert code == 206 and len(body) == 10 and hd["Content-Range"].startswith("bytes 0-9/")
    form = ("csrf=%s&action=nothing" % srv.csrf_for(srv.owner())).encode()
    # no Origin: refused; wrong token: refused; right token and origin, unknown action: refused by the whitelist
    assert get("/")[1].get("Referrer-Policy") == "same-origin"  # under no-referrer Chrome posts "Origin: null"
    for hd, d in (({}, form), ({"Origin": "http://" + host}, b"csrf=bad&action=start"), ({"Origin": "null"}, form),
                  ({"Origin": "http://evil.example"}, form), ({"Origin": "http://" + host}, form)):
        code, h2, _ = get("/system/service", headers=hd, data=d)
        assert code == 303 and h2["Location"] == "/system?msg=denied", (hd, code, h2.get("Location"))
    assert get("/system/service", data=b"x" * (MAX_BODY + 1), headers={"Origin": "http://" + host})[0] == 413
    selftest_registration(get, host)
    selftest_cameras(get, host, srv.csrf_for(srv.owner()))
    selftest_uploads(get, host, srv.csrf_for(srv.owner()))
    selftest_accounts(srv, get, host, good)
    srv.shutdown()
    os.remove(srv.auth_file)


def selftest_login(srv, get, host):
    """sign-in page, session cookie, tampering, expiry, redirects, sign-out, password change (D-036); returns a cookie"""
    o = {"Origin": "http://" + host}
    code, h, _ = get("/", auth=None)
    assert code == 303 and h["Location"] == "/login?next=%2F" and "WWW-Authenticate" not in h, (code, h)
    code, h, _ = get("/api/status", auth=None)
    assert code == 401 and "WWW-Authenticate" not in h and get("/live.mjpg", auth=None)[0] == 401
    assert b"autocomplete=\"current-password\"" in get("/login", auth=None)[2]

    def login(user, pw, nxt="/", headers=o):
        return get("/login", auth=None, headers=headers, data=("user=%s&password=%s&next=%s" % (
            quote(user), quote(pw), quote(nxt, safe=""))).encode())
    assert login("admin", "wrong")[0] == 401 and login("\u00e9\u4e2d", "\u00e9\u4e2d")[0] == 401  # non-ASCII: no exception
    assert login("admin", "secret123", headers={})[0] == 403 and login("admin", "secret123", headers={"Origin": "null"})[0] == 403
    # X-Forwarded-Proto counts only from this host: here it is, so the origin must then be https
    assert login("admin", "secret123", headers=dict(o, **{"X-Forwarded-Proto": "https"}))[0] == 403
    code, h, _ = login("admin", "secret123", headers={"Origin": "https://" + host, "X-Forwarded-Proto": "https"})
    assert code == 303 and "; Secure" in h["Set-Cookie"]
    for bad in ("//evil.example", "/\\evil.example", "https://evil.example", "/live\r\nX: y"):
        code, h, _ = login("admin", "secret123", bad)
        assert code == 303 and h["Location"] == "/", (bad, h["Location"])
    code, h, _ = login("admin", "secret123", "/attendance?date=2026-11-06")
    assert code == 303 and h["Location"] == "/attendance?date=2026-11-06" and "; Secure" not in h["Set-Cookie"]
    assert "HttpOnly" in h["Set-Cookie"] and "SameSite=Lax" in h["Set-Cookie"] and "Domain" not in h["Set-Cookie"]
    cookie = h["Set-Cookie"].split(";")[0]
    tok = cookie.split("=", 1)[1]
    assert get("/", auth=cookie)[0] == 200 and srv.session_principal(tok)["uid"] == "owner" and tok.startswith("v2.owner.")
    assert get("/login", auth=cookie)[0] == 303  # signed in already
    exp = int(time.time()) - 1  # expired, tampered, malformed, too far ahead: all signed out
    v1 = "v1.%d.%s" % (int(time.time()) + 60, "0" * 64)  # cookies of v1.8 and earlier: signed out once (D-040)
    for t in (srv.session_token(srv.owner(), exp), tok[:-1] + ("0" if tok[-1] != "0" else "1"), "v1.x.y", v1, "\u00e9" * 50,
              srv.session_token(srv.owner(), int(time.time()) + SESSION_HOURS * 3600 + 60), tok.replace("v2.owner.", "v2.u_" + "0" * 16 + ".")):
        assert get("/", auth=COOKIE + "=" + t)[0] == 303, t
    other = Server(("127.0.0.1", 0), Handler)  # a restart reads the same key: still signed in
    other.auth_file = srv.auth_file
    assert other.session_principal(tok)["uid"] == "owner" and other.csrf_for(other.owner()) == srv.csrf_for(srv.owner())
    other.server_close()
    form = ("csrf=%s" % srv.csrf_for(srv.owner())).encode()
    code, h, _ = get("/logout", auth=cookie, headers=o, data=form)
    assert code == 303 and h["Location"] == "/login" and "Max-Age=0" in h["Set-Cookie"]
    old_csrf = srv.csrf_for(srv.owner())
    write_auth(srv.auth_file, "admin", "secret123")  # a new password (same text, new salt) ends sessions and forms
    assert get("/", auth=cookie)[0] == 303 and srv.csrf_for(srv.owner()) != old_csrf
    fresh = login("admin", "secret123")[1]["Set-Cookie"].split(";")[0]
    assert get("/", auth=fresh)[0] == 200
    return fresh


def selftest_accounts(srv, get, host, owner_cookie):
    """users and roles (D-040): links, sign-in names, the viewer's routes, revocation, cooldown, closed on DB failure"""
    import tempfile
    o = {"Origin": "http://" + host}
    tok = srv.csrf_for(srv.owner())

    def post(path, fields, cookie=None, headers=o):
        return get(path, auth=cookie or owner_cookie, headers=headers, data="&".join(
            "%s=%s" % (k, quote(v, safe="")) for k, v in dict(fields, csrf=fields.get("csrf", tok)).items()).encode())

    def login(ident, pw, nxt="/"):
        return get("/login", auth=None, headers=o, data=("user=%s&password=%s&next=%s" % (quote(ident), quote(pw), quote(nxt, safe=""))).encode())
    assert norm_email(" Fam@Example.COM ") == "fam@example.com" and norm_email("x@y") is None
    assert norm_phone("+91 98765-43210") == "+919876543210" and norm_phone("9876543210") is None and norm_phone("+0123456789") is None
    assert pw_problem("short") and pw_problem("fam@example.com", "Fam", "fam@example.com") and not pw_problem("correct horse battery")
    g = globals()
    saved = g["PUBLIC_URL"]
    for v, want in (("https://att.example.com", "https://att.example.com"), ("http://att.example.com", ""),
                    ("https://att.example.com/x", ""), ("https://u@att.example.com", ""), ("", "")):
        g["PUBLIC_URL"] = v
        assert public_origin() == want, v
    g["PUBLIC_URL"] = saved
    # add: the admin's own password first; then the link is shown once
    assert post("/users/add", {"name": "Family", "role": "viewer", "email": "Fam@Example.com", "current": "wrong"})[1]["Location"] == "/users?msg=u_reauth"
    for bad in ({"phone": "98765 43210"}, {"email": "nope"}, {}):
        assert post("/users/add", dict(bad, name="Family", role="viewer", current="secret123"))[1]["Location"] == "/users?msg=u_bad", bad
    loc = post("/users/add", {"name": "Family", "role": "viewer", "email": "Fam@Example.com", "phone": "+91 98765 43210",
                              "current": "secret123"})[1]["Location"]
    assert loc.startswith("/users?msg=u_added&show="), loc
    nonce = loc.rsplit("=", 1)[1]
    uid, link, _ = SHOWN_LINKS[nonce]
    page = get(loc)[2]
    assert link.encode() in page and nonce not in SHOWN_LINKS  # shown once
    assert post("/users/add", {"name": "Other", "role": "viewer", "email": "fam@example.com", "current": "secret123"})[1]["Location"] == "/users?msg=u_taken"
    assert post("/users/add", {"name": "Other", "role": "viewer", "phone": "+919876543210", "current": "secret123"})[1]["Location"] == "/users?msg=u_taken"
    # the link: viewing does not use it up; a weak password is refused; then it works once
    assert b"Set your password" in get("/account/setup?t=" + link, auth=None)[2] and b"Set your password" in get("/account/setup?t=" + link, auth=None)[2]
    setup = lambda t, pw, again=None: get("/account/setup", auth=None, headers=o, data=("t=%s&password=%s&again=%s" % (
        t, quote(pw), quote(again if again is not None else pw))).encode())
    assert setup(link, "too short")[0] == 400 and setup(link, "correct horse battery", "other words here")[0] == 400
    assert login("fam@example.com", "correct horse battery")[0] == 401  # no password yet
    code, h, _ = setup(link, "correct horse battery")
    assert code == 303 and h["Location"] == "/login?msg=pw_set" and setup(link, "correct horse battery")[0] == 403
    assert b"Link not valid" in get("/account/setup?t=" + link, auth=None)[2] and get("/account/setup", auth=None, headers={"Origin": "http://evil"},
                                                                                    data=b"t=x&password=y&again=y")[0] == 403
    # concurrent use of one link: one wins
    t2 = issue_link(uid)
    res = []
    th = [threading.Thread(target=lambda: res.append(redeem_link(t2, "another long passphrase"))) for _ in range(2)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    assert sorted(r is None for r in res) == [False, True], res
    set_account_password(uid, "correct horse battery")
    # sign in by email (any case) or phone (any format); next is checked against the role
    code, h, _ = login("FAM@example.com", "correct horse battery", "/users")
    assert code == 303 and h["Location"] == "/", h.get("Location")
    viewer = h["Set-Cookie"].split(";")[0]
    assert login("+91 (98765) 43210", "correct horse battery")[0] == 303 and login("nobody@example.com", "x" * 20)[0] == 401
    # the viewer sees everything read-only and nothing else
    for path in ("/", "/live", "/attendance", "/api/status", "/api/health", "/system", "/cameras", "/account", "/csv?date=2026-11-06"):
        assert get(path, auth=viewer)[0] == 200, path
    assert b"href='/users'" not in get("/", auth=viewer)[2] and b"href='/users'" in get("/", auth=owner_cookie)[2]
    sysv, syso = get("/system", auth=viewer)[2], get("/system", auth=owner_cookie)[2]
    assert b"/system/service" not in sysv and b"/system/matching" not in sysv and b"/system/matching" in syso
    camv = get("/cameras", auth=viewer)[2]
    assert b"<form method=\"post\" action=\"/cameras" not in camv and b"action='/cameras" not in camv and b"Cameras</h1>" in camv
    for path in ("/people", "/uploads", "/users", "/pending/0123456789ab/1.jpg",
                 "/uploads/" + "a" * 16 + "/result.mp4"):
        assert get(path, auth=viewer)[0] == 403, path
    vt = srv.csrf_for(srv.principal(uid))
    for path in ("/system/service", "/cameras/add", "/people/invite", "/users/add", "/uploads/delete", "/system/matching"):
        assert post(path, {"action": "stop", "csrf": vt}, cookie=viewer)[0] == 403, path
    assert get("/uploads/add", auth=viewer, headers=dict(o, **{"X-CSRF": vt}), data=b"x" * 1000)[0] == 403
    assert post("/logout", {"csrf": tok}, cookie=viewer)[1]["Location"] == "/?msg=denied"  # another user's form token
    # role change and back: the old cookies do not come back (version, D-040)
    assert post("/users/role", {"id": uid, "role": "admin", "current": "secret123"})[1]["Location"] == "/users?msg=u_role"
    assert get("/", auth=viewer)[0] == 303
    admin_cookie = login("fam@example.com", "correct horse battery")[1]["Set-Cookie"].split(";")[0]
    assert get("/users", auth=admin_cookie)[0] == 200
    post("/users/role", {"id": uid, "role": "viewer", "current": "secret123"})
    assert get("/users", auth=admin_cookie)[0] == 303 and get("/", auth=viewer)[0] == 303
    # own password: the current one first; then every session of that account ends
    viewer = login("fam@example.com", "correct horse battery")[1]["Set-Cookie"].split(";")[0]
    vt = srv.csrf_for(srv.principal(uid))
    assert post("/account/password", {"csrf": vt, "current": "wrong", "password": "brand new passphrase", "again": "brand new passphrase"},
                cookie=viewer)[1]["Location"] == "/account?msg=pw_wrong"
    assert post("/account/password", {"csrf": vt, "current": "correct horse battery", "password": "fam@example.com", "again": "fam@example.com"},
                cookie=viewer)[1]["Location"] == "/account?msg=pw_weak"
    assert post("/account/password", {"csrf": vt, "current": "correct horse battery", "password": "brand new passphrase",
                                      "again": "brand new passphrase"}, cookie=viewer)[1]["Location"] == "/login"
    assert get("/", auth=viewer)[0] == 303
    viewer = login("fam@example.com", "brand new passphrase")[1]["Set-Cookie"].split(";")[0]
    # a download stops when access ends (checked every 2 MB or 3 s)
    rec = tempfile.mkdtemp()
    saved_rec = g["REC_DIR"]
    g["REC_DIR"] = rec
    try:
        os.makedirs(os.path.join(rec, "2026-11-06"))
        with open(os.path.join(rec, "2026-11-06", "000000_9.jpg"), "wb") as f:
            f.write(b"\0" * (24 << 20))
        s = socket.create_connection(("127.0.0.1", srv.server_address[1]))
        s.sendall(("GET /rec/2026-11-06/000000_9.jpg HTTP/1.1\r\nHost: %s\r\nCookie: %s\r\nConnection: close\r\n\r\n" % (host, viewer)).encode())
        s.settimeout(10)
        got = len(s.recv(65536))
        post("/users/delete", {"id": uid, "current": "secret123"})  # deleting the account ends its sign-in at once
        while True:
            d = s.recv(1 << 20)
            if not d:
                break
            got += len(d)
        s.close()
        assert got < (20 << 20), got
    finally:
        g["REC_DIR"] = saved_rec
        shutil.rmtree(rec, ignore_errors=True)
    assert get("/", auth=viewer)[0] == 303 and srv.principal(uid) is None and not link_account(t2)
    # per-account pause after 5 failures, even with the right password; then it passes again
    uid2 = "u_" + "1" * 16
    with _db_lock, db() as c:
        c.execute("INSERT INTO users (id, name, email, role, version, created) VALUES (?,?,?,?,0,?)", (uid2, "Aunt", "aunt@example.com", "viewer", time.time()))
    set_account_password(uid2, "aunt long passphrase")
    srv.fails.clear()  # the per-visitor limit is not what this checks
    for _ in range(5):
        assert login("aunt@example.com", "wrong but long enough")[0] == 401
    assert login("aunt@example.com", "aunt long passphrase")[0] == 429
    srv.acct_ok(uid2)
    srv.fails.clear()
    code, h, _ = login("aunt@example.com", "aunt long passphrase")
    assert code == 303
    aunt = h["Set-Cookie"].split(";")[0]
    # the database fails: nobody but the owner gets in (closed)
    saved_db = g["DB_PATH"]
    g["DB_PATH"] = os.path.join(rec, "missing", "web.db")  # a folder that does not exist: sqlite cannot open it
    try:
        assert get("/", auth=aunt)[0] == 303 and get("/", auth=owner_cookie)[0] == 200
    finally:
        g["DB_PATH"] = saved_db
    with _db_lock, db() as c:
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM account_links")


def selftest_cameras(get, host, tok):
    """passwords never reach a page; add, enable, remove a network camera while a CSI camera is in use (no restarts)"""
    assert masked("rtsp://admin:s3cret@cam:554/s") == "rtsp://admin:\u2022\u2022\u2022@cam:554/s" and masked("rtsp://cam/s") == "rtsp://cam/s"
    assert with_login("rtsp://cam:554/s", "u", "p@ss:1") == "rtsp://u:p%40ss%3A1@cam:554/s"
    assert active_line({"source": "csi", "csi": "0", "usb": "", "size": "1920x1080", "rtsp": []}) == "csi:0 1920 1080"
    for p in (CAM_JSON, CAM_ACTIVE):
        if os.path.exists(p):
            os.remove(p)
    write_private(CAM_JSON, json.dumps({"source": "csi", "csi": "0", "usb": "/dev/video1", "size": "1920x1080", "rtsp": []}))
    hd = {"Origin": "http://" + host}
    code, h, _ = get("/cameras/add", headers=hd, data=("csrf=%s&name=Gate&url=rtsp%%3A%%2F%%2Fcam%%3A554%%2Fs&user=admin&pw=topsecret" % tok).encode())
    assert code == 303 and h["Location"] == "/cameras?msg=cam_added", (code, h.get("Location"))
    cfg = load_cameras()
    assert cfg["rtsp"][0]["url"] == "rtsp://admin:topsecret@cam:554/s" and cfg["rtsp"][0]["enabled"]
    assert oct(os.stat(CAM_JSON).st_mode & 0o777) == "0o600"
    page = get("/cameras")[2]
    assert b"topsecret" not in page and b"Gate" in page and "\u2022".encode() in page
    write_private(CAM_ACTIVE, "rtsp://admin:topsecret@cam:554/s 1280 720\n")  # the System page names the camera in use
    assert camera_label() == "Gate (network camera at cam)", camera_label()
    os.remove(CAM_ACTIVE)
    cid = cfg["rtsp"][0]["id"]
    assert get("/cameras/toggle", headers=hd, data=("csrf=%s&id=%s" % (tok, cid)).encode())[1]["Location"] == "/cameras?msg=cam_saved_inactive"
    assert not load_cameras()["rtsp"][0]["enabled"]
    assert get("/cameras/add", headers=hd, data=("csrf=%s&name=X&url=http%%3A%%2F%%2Fx" % tok).encode())[1]["Location"] == "/cameras?msg=cam_bad"
    assert get("/cameras/source", headers=hd, data=("csrf=%s&source=rtsp&csi=0&size=1920x1080" % tok).encode())[1]["Location"] == "/cameras?msg=cam_none_enabled"
    assert get("/cameras/remove", headers=hd, data=("csrf=%s&id=%s" % (tok, cid)).encode())[1]["Location"] == "/cameras?msg=cam_removed"
    assert load_cameras()["rtsp"] == [] and not os.path.exists(CAM_ACTIVE)
    # two at once (D-039): each enabled camera is one line with its id and name; a third is refused
    two = [{"id": "a1", "name": "Gate", "url": "rtsp://a/s", "enabled": True},
           {"id": "b2", "name": "Back door", "url": "rtsp://b/s", "enabled": True},
           {"id": "c3", "name": "Yard", "url": "rtsp://c/s", "enabled": False}]
    cfg = {"source": "csi", "csi": "0", "usb": "", "size": "1280x720", "rtsp": two}
    assert active_line(dict(cfg, source="rtsp")) == "rtsp://a/s 1280 720 a1 Gate\nrtsp://b/s 1280 720 b2 Back door"
    write_private(CAM_JSON, json.dumps(cfg))
    assert get("/cameras/toggle", headers=hd, data=("csrf=%s&id=c3" % tok).encode())[1]["Location"] == "/cameras?msg=cam_two"
    assert not load_cameras()["rtsp"][2]["enabled"]
    write_private(CAM_ACTIVE, active_line(dict(cfg, source="rtsp")) + "\n")
    assert camera_label() == "Gate and Back door (network cameras)", camera_label()
    for p in (CAM_JSON, CAM_ACTIVE):
        os.remove(p)


def selftest_uploads(get, host, tok):
    """video uploads (D-039): the discoverer's verdict, limits, retention, and the guarded routes; in a temporary folder"""
    import tempfile
    g = globals()
    saved = g["UPLOADS"], g["UPLOAD_RESERVE"]
    g["UPLOADS"], g["UPLOAD_RESERVE"] = tempfile.mkdtemp(), 0  # never the real uploads; any free space will do here
    try:
        selftest_uploads_in(get, host, tok)
    finally:
        shutil.rmtree(g["UPLOADS"], ignore_errors=True)
        g["UPLOADS"], g["UPLOAD_RESERVE"] = saved


def selftest_uploads_in(get, host, tok):
    good = ("Done discovering file:///x\n  container: video/quicktime\n    video: video/x-h264, width=(int)1920\n"
            "      Width: 1920\n      Height: 540\n  Duration: 0:00:08.940165061\n")
    meta, err = parse_discoverer(0, good)
    assert not err and meta == {"width": 1920, "height": 540, "duration": 8.94, "codec": "H.264"}, (meta, err)
    assert parse_discoverer(0, good.replace("x-h264", "x-h265"))[0]["codec"] == "H.265"
    assert "H.264 or H.265" in parse_discoverer(0, good.replace("video/x-h264", "video/x-vp9"))[1]
    assert "1920 x 1080" in parse_discoverer(0, good.replace("Width: 1920", "Width: 3840"))[1]
    assert "30 minutes" in parse_discoverer(0, good.replace("0:00:08", "0:31:00"))[1]
    assert "not a video" in parse_discoverer(1, good)[1] and "not a video" in parse_discoverer(0, "  audio: x\n")[1]
    assert "larger than 95 MB" in receive_upload(io.BytesIO(b""), 96 << 20, "a.mp4", True)[1]
    assert "choose a video" in receive_upload(io.BytesIO(b""), 0, "a.mp4", False)[1]
    assert "stopped before the end" in receive_upload(io.BytesIO(b"abc"), 10, "a.mp4", False)[1]
    assert not [u for u in uploads() if u["state"] == "uploading"]  # a broken upload leaves nothing behind
    # retention: 7 days after upload, never while processing
    for uid, state, age in (("a" * 16, "done", 8), ("b" * 16, "processing", 8), ("c" * 16, "failed", 1)):
        d = os.path.join(UPLOADS, uid)
        os.makedirs(d, exist_ok=True)
        write_private(os.path.join(d, "state"), state)
        write_private(os.path.join(d, "meta.json"), json.dumps({"name": "v.mp4", "created": time.time() - age * 86400}))
    cleanup_uploads()
    assert sorted(u["id"] for u in uploads()) == ["b" * 16, "c" * 16]
    hd = {"Origin": "http://" + host}
    code, _, body = get("/uploads")
    assert code == 200 and b"v.mp4" in body and b"Video upload" in body
    assert get("/uploads/" + "c" * 16 + "/result.mp4")[0] == 404  # only finished results
    assert get("/uploads/" + "c" * 16 + "/result.mp4", auth=None)[0] == 401
    assert get("/uploads/add", headers={"Origin": "http://evil", "X-CSRF": tok}, data=b"x")[0] == 403
    assert get("/uploads/add", headers=dict(hd, **{"X-CSRF": "wrong"}), data=b"x")[0] == 403
    assert get("/uploads/delete", headers=hd, data=("csrf=%s&id=%s" % (tok, "c" * 16)).encode())[1]["Location"] == "/uploads?msg=up_deleted"
    assert get("/uploads/delete", headers=hd, data=("csrf=%s&id=../x" % tok).encode())[1]["Location"] == "/uploads?msg=denied"
    assert get("/uploads/cancel", headers=hd, data=("csrf=%s&id=%s" % (tok, "b" * 16)).encode())[1]["Location"] == "/uploads?msg=up_cancelled"
    assert os.path.exists(os.path.join(UPLOADS, "b" * 16, "cancel"))  # facepipe stops it and marks it cancelled


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


# a function, not a bare block, so the compiled build can call it: there web.py is a two-line stub and this file is
# webapp.so (D-043)
def main():
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
        LOG.write("INFO", "web %s: attendance page on port %d, data %s" % (VERSION, PORT, DATA))
        srv.serve_forever()


if __name__ == "__main__":
    main()
