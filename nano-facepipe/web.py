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
Run:  python3 web.py   (env: FACEPIPE_DATA, FACEPIPE_DIR, FACEPIPE_WEB_PORT=8000, FACEPIPE_SITE="Face Attendance")
Self-check:  python3 web.py --selftest
"""
import base64, csv, datetime, getpass, hashlib, hmac, html, io, json, os, re, socketserver, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("FACEPIPE_DATA", "/mnt/ssd/facepipe/data")
GALLERY_DIR = os.path.join(os.environ.get("FACEPIPE_DIR", HERE), "gallery")
GALLERY_TSV = os.environ.get("FACEPIPE_GALLERY", os.path.join(DATA, "gallery.tsv"))
AUTH_FILE = os.environ.get("FACEPIPE_WEB_AUTH", os.path.join(HERE, "web_auth"))  # outside DATA, never served
PORT = int(os.environ.get("FACEPIPE_WEB_PORT", "8000"))
SITE = os.environ.get("FACEPIPE_SITE", "Face Attendance")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SNAP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{6}_\d+\.(jpg|bmp)$")
REC_DIR = os.environ.get("FACEPIPE_REC", "/mnt/rec")
REC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}/\d{6}_(\d+\.jpg|clip\.mp4)$")
LIVE_DIR = "/dev/shm/facepipe"
RTSP_AUTH = os.environ.get("FACEPIPE_RTSP_AUTH", os.path.join(HERE, "rtsp_auth"))
RTSP_PORT = os.environ.get("FACEPIPE_RTSP_PORT", "8554")
CTYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".bmp": "image/bmp", ".mp4": "video/mp4"}
STALE_S = 20  # status.json older than this: recognition service is not running


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


def hm(iso):
    return iso[11:16] if iso else ""


def hms(iso):
    return iso[11:19] if iso else ""


# ---------- pages ----------
CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1d2433;--muted:#5b6475;--line:#e3e6ec;--accent:#2457c5;--ok:#1a7f4b;--okbg:#e5f4ec;
--warn:#9a5b00;--warnbg:#fdf1dc;--bad:#b42318;--badbg:#fde8e7}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif}
a{color:var(--accent)}:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:2}
.bar{max-width:1100px;margin:0 auto;padding:.7rem 1.2rem;display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
.brand{font-weight:650;font-size:1.05rem;display:flex;align-items:center;gap:.5rem;margin-right:auto}
.brand svg{width:26px;height:26px}
.pill{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .7rem;border-radius:999px;font-size:.85rem;font-weight:550}
.pill::before{content:"";width:.55rem;height:.55rem;border-radius:50%;background:currentColor}
.live{color:var(--ok);background:var(--okbg)}.no_video{color:var(--warn);background:var(--warnbg)}.stopped{color:var(--bad);background:var(--badbg)}
nav.date{display:flex;gap:.35rem;align-items:center}
.btn,input[type=date],input[type=search]{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:8px;padding:.38rem .7rem;text-decoration:none;cursor:pointer;line-height:1.2}
.btn:hover{border-color:var(--accent)}.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn[aria-disabled=true]{opacity:.45;pointer-events:none}
main{max-width:1100px;margin:0 auto;padding:1.2rem}
h1{font-size:1.45rem;margin:.2rem 0 1rem}h2{font-size:1.1rem;margin:0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.9rem;margin-bottom:1.2rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem 1.1rem}
.card .k{color:var(--muted);font-size:.85rem}.card .v{font-size:1.8rem;font-weight:650;margin-top:.15rem}
.card .s{color:var(--muted);font-size:.85rem}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:1.2rem}
.sh{display:flex;align-items:center;gap:.8rem;padding:.9rem 1.1rem;border-bottom:1px solid var(--line);flex-wrap:wrap}
.sh .count{color:var(--muted);font-weight:500}.sh input{margin-left:auto;min-width:14rem}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:.55rem 1.1rem;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:.8rem;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);font-weight:600;white-space:nowrap}
th button{all:unset;cursor:pointer}th button::after{content:" \\2195";opacity:.35}
th[aria-sort=ascending] button::after{content:" \\2191";opacity:1}th[aria-sort=descending] button::after{content:" \\2193";opacity:1}
tbody tr:hover{background:#fafbfc}tbody tr:last-child td{border-bottom:0}
td.num{font-variant-numeric:tabular-nums}
.who{display:flex;align-items:center;gap:.7rem;font-weight:550}
.av{width:40px;height:40px;border-radius:50%;object-fit:cover;background:var(--line);flex:none}
.bar-m{display:inline-block;width:60px;height:6px;border-radius:3px;background:var(--line);vertical-align:middle;margin-right:.4rem}
.bar-m i{display:block;height:100%;border-radius:3px;background:var(--ok)}
.chips{display:flex;flex-wrap:wrap;gap:.6rem;padding:1rem 1.1rem}
.chip{display:flex;align-items:center;gap:.5rem;border:1px solid var(--line);border-radius:999px;padding:.25rem .8rem .25rem .25rem}
.chip .av{width:30px;height:30px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:.8rem;padding:1rem 1.1rem}
figure{margin:0;text-align:center;font-size:.82rem;color:var(--muted)}
figure img{width:100%;aspect-ratio:1;border-radius:10px;object-fit:cover;background:var(--line);display:block;margin-bottom:.3rem}
.empty{padding:1.2rem 1.1rem;color:var(--muted)}.muted{color:var(--muted)}
.sh #livebtn{margin-left:auto}#livebox{padding:0 1.1rem 1.1rem}
#liveimg{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#111;border-radius:10px}
td.ev{white-space:nowrap}
footer{max-width:1100px;margin:0 auto;padding:0 1.2rem 2rem;color:var(--muted);font-size:.82rem}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
@media (max-width:640px){.sh input{margin-left:0;width:100%}th:nth-child(5),td:nth-child(5){display:none}}
@media print{header,.noprint,.sh input{display:none!important}body{background:#fff}section,.card{border-color:#bbb;break-inside:avoid}
main{padding:0}tbody tr:hover{background:none}}
"""

JS = """
(function(){
  var today = document.body.dataset.today === "1";
  var dp = document.getElementById("date");
  dp.addEventListener("change", function(){ if (dp.value) dp.form.submit(); });
  document.getElementById("print").addEventListener("click", function(){ window.print(); });
  document.querySelectorAll("table[data-sort]").forEach(function(t){
    t.querySelectorAll("th[data-k]").forEach(function(th, i){
      th.querySelector("button").addEventListener("click", function(){
        var asc = th.getAttribute("aria-sort") !== "ascending";
        t.querySelectorAll("th").forEach(function(h){ h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var col = th.cellIndex, rows = Array.prototype.slice.call(t.tBodies[0].rows);
        rows.sort(function(a, b){
          var x = a.cells[col].dataset.v, y = b.cells[col].dataset.v, nx = parseFloat(x), ny = parseFloat(y);
          var c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y);
          return asc ? c : -c;
        });
        rows.forEach(function(r){ t.tBodies[0].appendChild(r); });
      });
    });
  });
  var q = document.getElementById("q");
  if (q) q.addEventListener("input", function(){
    var s = q.value.trim().toLowerCase();
    document.querySelectorAll("[data-name]").forEach(function(el){
      el.hidden = s && el.dataset.name.indexOf(s) < 0;
    });
  });
  var pill = document.getElementById("status");
  function poll(){
    fetch("/api/status", {credentials: "same-origin", cache: "no-store"}).then(function(r){ return r.json(); }).then(function(s){
      pill.className = "pill " + s.state; pill.textContent = s.detail;
    }).catch(function(){ pill.className = "pill stopped"; pill.textContent = "Page cannot reach the device"; });
  }
  setInterval(poll, 10000);
  var lb = document.getElementById("livebtn"), box = document.getElementById("livebox"), img = document.getElementById("liveimg");
  if (lb) lb.addEventListener("click", function(){
    var show = box.hidden;
    box.hidden = !show;
    lb.setAttribute("aria-expanded", show ? "true" : "false");
    lb.textContent = show ? "Hide live view" : "Show live view";
    if (show) img.src = "/live.mjpg?t=" + Date.now(); else img.removeAttribute("src");  // closing it stops the stream
  });
  if (today) setInterval(function(){
    if (!document.hidden && !(q && q.value) && !(box && !box.hidden)) location.reload();
  }, 30000);
})();
"""

LOGO = ("<svg viewBox='0 0 24 24' aria-hidden='true'><rect width='24' height='24' rx='6' fill='#2457c5'/>"
        "<circle cx='12' cy='10' r='3.6' fill='none' stroke='#fff' stroke-width='1.8'/>"
        "<path d='M5.5 19c1.3-3 3.7-4.4 6.5-4.4s5.2 1.4 6.5 4.4' fill='none' stroke='#fff' stroke-width='1.8' stroke-linecap='round'/></svg>")
FAVICON = "data:image/svg+xml," + quote(LOGO.replace("aria-hidden='true'", "xmlns='http://www.w3.org/2000/svg'"))


def evidence(e):
    """links to the full-frame image and the event clip (recordings volume), if they exist"""
    out = []
    if e.get("frame") and REC_RE.match(e["frame"]):
        out.append("<a href='/rec/%s' target='_blank' rel='noopener'>Frame</a>" % html.escape(e["frame"]))
    if e.get("clip") and REC_RE.match(e["clip"]):
        out.append("<a href='/rec/%s' target='_blank' rel='noopener'>Clip</a>" % html.escape(e["clip"]))
    return " &middot; ".join(out) or "<span class='muted'>&ndash;</span>"


def page(day, today, nonce, host="this-device"):
    esc = html.escape
    people, unknown = attendance(read_events(day))
    gal = enrolled()
    absent = sorted(n for n in gal if n not in people)
    st = status()
    d = datetime.date(*map(int, day.split("-")))
    prev_d, next_d = (d - datetime.timedelta(days=1)).isoformat(), (d + datetime.timedelta(days=1)).isoformat()
    is_today = day == today
    rate = "%d%% of %d enrolled" % (round(100.0 * len([n for n in people if n in gal]) / len(gal)), len(gal)) if gal else "no one enrolled"
    last = max((p["last"] for p in people.values()), default="")
    last_any = max([last] + [e["time"] for e in unknown]) if (last or unknown) else ""

    def avatar(n):
        return "<img class='av' src='/photo/%s' alt='' loading='lazy'>" % quote(n) if n in gal else "<span class='av'></span>"

    rows = []
    for n, p in sorted(people.items(), key=lambda kv: kv[1]["first"]):
        pct = max(0, min(100, int(p["best"] * 100)))
        rows.append(
            "<tr data-name='%s'><td data-v='%s'><div class='who'><img class='av' src='/snap/%s' alt='Face seen at %s'>%s%s</div></td>"
            "<td class='num' data-v='%s'>%s</td><td class='num' data-v='%s'>%s</td><td class='num' data-v='%d'>%d</td>"
            "<td class='num' data-v='%.3f'><span class='bar-m'><i style='width:%d%%'></i></span>%.2f</td><td class='ev'>%s</td></tr>"
            % (esc(display_name(n).lower()), esc(display_name(n).lower()), esc(p["snap"]), hms(p["first"]), esc(display_name(n)),
               "" if n in gal else " <small>(no longer enrolled)</small>", p["first"], hms(p["first"]), p["last"], hms(p["last"]),
               p["sightings"], p["sightings"], p["best"], pct, p["best"], evidence(p)))
    present_html = (
        "<table data-sort><thead><tr><th data-k scope='col'><button>Name</button></th><th data-k scope='col'><button>First seen</button></th>"
        "<th data-k scope='col'><button>Last seen</button></th><th data-k scope='col'><button>Sightings</button></th>"
        "<th data-k scope='col'><button>Best match</button></th><th scope='col'>Evidence</th></tr></thead><tbody>%s</tbody></table>"
        % "".join(rows)
        if rows else "<p class='empty'>No one recognized %s.</p>" % ("yet today" if is_today else "on this day"))
    absent_html = ("<div class='chips'>%s</div>" % "".join(
        "<span class='chip' data-name='%s'>%s%s</span>" % (esc(display_name(n).lower()), avatar(n), esc(display_name(n)))
        for n in absent)) if absent else "<p class='empty'>%s</p>" % ("Everyone enrolled is present." if gal else "No one is enrolled yet.")
    unknown_html = ("<div class='grid'>%s</div>" % "".join(
        "<figure><img src='/snap/%s' alt='Unknown face at %s' loading='lazy'><figcaption>%s &middot; %.2f<br>%s</figcaption></figure>"
        % (esc(e["snap"]), hms(e["time"]), hms(e["time"]), e["score"], evidence(e)) for e in reversed(unknown))
                    ) if unknown else "<p class='empty'>No unknown faces.</p>"

    return """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{site} &middot; {nice}</title><link rel="icon" href="{fav}"><style nonce="{nonce}">{css}</style></head>
<body data-today="{tflag}">
<header><div class="bar">
 <div class="brand">{logo}<span>{site}</span></div>
 <span id="status" class="pill {state}" role="status" aria-live="polite">{detail}</span>
 <nav class="date" aria-label="Choose date">
  <a class="btn" href="/?date={prev}" aria-label="Previous day">&lsaquo;</a>
  <form method="get" action="/"><label class="sr" for="date">Date</label>
   <input id="date" type="date" name="date" value="{day}" max="{today}"></form>
  <a class="btn" href="/?date={next}" aria-label="Next day" {nextdis}>&rsaquo;</a>
  <a class="btn" href="/" {todaydis}>Today</a>
 </nav>
 <a class="btn primary noprint" href="/csv?date={day}">Export CSV</a>
 <button class="btn noprint" type="button" id="print">Print</button>
</div></header>
<main>
<h1>Attendance &middot; {nice}</h1>
<div class="cards">
 <div class="card"><div class="k">Present</div><div class="v">{n_present}</div><div class="s">{rate}</div></div>
 <div class="card"><div class="k">Absent</div><div class="v">{n_absent}</div><div class="s">of the enrolled list</div></div>
 <div class="card"><div class="k">Unknown faces</div><div class="v">{n_unknown}</div><div class="s">not matched to anyone</div></div>
 <div class="card"><div class="k">Last activity</div><div class="v">{last_hm}</div><div class="s">{last_s}</div></div>
</div>
{live}
<section aria-labelledby="h-present"><div class="sh"><h2 id="h-present">Present</h2><span class="count">{n_present}</span>
 <label class="sr" for="q">Search names</label><input id="q" type="search" placeholder="Search names" autocomplete="off"></div>{present}</section>
<section aria-labelledby="h-absent"><div class="sh"><h2 id="h-absent">Absent</h2><span class="count">{n_absent}</span></div>{absent}</section>
<section aria-labelledby="h-unknown" class="noprint"><div class="sh"><h2 id="h-unknown">Unknown faces</h2><span class="count">{n_unknown}</span></div>{unknown}</section>
</main>
<footer>Jetson Nano &middot; on-device recognition, no data leaves this device &middot; generated {now}{refresh}{rtsp}</footer>
<script nonce="{nonce}">{js}</script>
</body></html>""".format(
        site=esc(SITE), nice=d.strftime("%A, %d %B %Y"), fav=FAVICON, nonce=nonce, css=CSS, js=JS, logo=LOGO,
        tflag="1" if is_today else "0", state=st["state"], detail=esc(st["detail"]), prev=prev_d, next=next_d, day=day,
        today=today, nextdis='aria-disabled="true" tabindex="-1"' if day >= today else "",
        todaydis='aria-disabled="true" tabindex="-1"' if is_today else "",
        n_present=len(people), rate=rate, n_absent=len(absent), n_unknown=len(unknown),
        last_hm=hm(last_any) or "&ndash;", last_s="last face seen" if last_any else "nothing seen",
        present=present_html, absent=absent_html, unknown=unknown_html,
        now=datetime.datetime.now().strftime("%H:%M:%S"), refresh=" &middot; refreshes every 30 s" if is_today else "",
        live=LIVE_SECTION if is_today else "",
        rtsp=(" &middot; video stream for VLC/NVR: rtsp://%s:%s/live (separate login)" % (esc(host), RTSP_PORT))
        if os.path.exists(RTSP_AUTH) else "")


LIVE_SECTION = """<section class="noprint" aria-labelledby="h-live"><div class="sh"><h2 id="h-live">Live view</h2>
<span class="count">camera with recognition overlay</span>
<button class="btn" id="livebtn" type="button" aria-expanded="false" aria-controls="livebox">Show live view</button></div>
<div id="livebox" hidden><img id="liveimg" alt="Live camera view with face boxes and names"></div></section>"""


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
class Handler(BaseHTTPRequestHandler):
    server_version = "facepipe"
    sys_version = ""

    def authorized(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            user, pw = base64.b64decode(h[6:]).decode().split(":", 1)
        except Exception:
            return False
        u, salt, digest = self.server.auth
        return hmac.compare_digest(user, u) & hmac.compare_digest(hash_pw(pw, salt), digest)

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None, cache="no-store"):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def file(self, path):
        """static file with HTTP Range support (browsers need it to seek in MP4 clips)"""
        ctype = CTYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        size = os.path.getsize(path)
        m = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        start, end = 0, size - 1
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                return self.send(416, "", "text/plain", {"Content-Range": "bytes */%d" % size})
        with open(path, "rb") as f:
            f.seek(start)
            body = f.read(end - start + 1)
        extra = {"Accept-Ranges": "bytes"}
        if m and (m.group(1) or m.group(2)):
            extra["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
            return self.send(206, body, ctype, extra, cache="private, max-age=86400")
        self.send(200, body, ctype, extra, cache="private, max-age=86400")

    def live(self):
        """MJPEG: annotated frames from facepipe (NVJPG). Touching 'want' tells facepipe someone is watching."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        os.makedirs(LIVE_DIR, exist_ok=True)
        want, live, last, tick = os.path.join(LIVE_DIR, "want"), os.path.join(LIVE_DIR, "live.jpg"), 0, 0
        try:
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
        except (BrokenPipeError, ConnectionResetError):
            pass  # viewer left

    def do_GET(self):
        if not self.authorized():
            return self.send(401, "Login required", "text/plain; charset=utf-8",
                             {"WWW-Authenticate": 'Basic realm="%s", charset="UTF-8"' % SITE})
        url = urlparse(self.path)
        today = datetime.date.today().isoformat()
        day = parse_qs(url.query).get("date", [today])[0]
        if not DATE_RE.match(day):
            return self.send(400, "Bad date", "text/plain; charset=utf-8")
        try:
            datetime.date(*map(int, day.split("-")))
        except ValueError:
            return self.send(400, "Bad date", "text/plain; charset=utf-8")
        if url.path == "/":
            nonce = base64.b64encode(os.urandom(12)).decode()
            csp = ("default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'nonce-%s'; script-src 'nonce-%s'; "
                   "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'" % (nonce, nonce))
            host = (self.headers.get("Host") or "this-device").rsplit(":", 1)[0]
            return self.send(200, page(day, today, nonce, host), extra={"Content-Security-Policy": csp})
        if url.path == "/live.mjpg":
            return self.live()
        if url.path.startswith("/rec/") and REC_RE.match(url.path[5:]):
            path = os.path.join(REC_DIR, url.path[5:])
            if os.path.exists(path):
                return self.file(path)
        if url.path == "/api/status":
            return self.send(200, json.dumps(status()), "application/json")
        if url.path == "/csv":
            return self.send(200, "﻿" + csv_text(day), "text/csv; charset=utf-8",  # BOM: Excel opens UTF-8 names right
                             {"Content-Disposition": "attachment; filename=attendance-%s.csv" % day})
        if url.path.startswith("/snap/") and SNAP_RE.match(url.path[6:]):
            path = os.path.join(DATA, "snaps", url.path[6:])
            if os.path.exists(path):
                return self.file(path)
        if url.path.startswith("/photo/"):
            path = first_photo(unquote(url.path[7:]))
            if path:
                return self.file(path)
        self.send(404, "Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # keep the journal quiet; facepipe logs the important parts


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def selftest():
    ev = [{"time": "2026-11-06T09:00:00", "ts": 1, "name": "a_b", "score": 0.6, "snap": "s1"},
          {"time": "2026-11-06T09:05:00", "ts": 2, "name": "unknown", "score": 0.2, "snap": "s2"},
          {"time": "2026-11-06T17:00:00", "ts": 3, "name": "a_b", "score": 0.7, "snap": "s3"}]
    people, unknown = attendance(ev)
    p = people["a_b"]
    assert p["first"].endswith("09:00:00") and p["last"].endswith("17:00:00"), p
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
    html_ = page("2026-11-06", "2026-11-06", "n0nce")
    assert "Attendance" in html_ and "nonce=\"n0nce\"" in html_
    print("selftest ok")


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
        print("attendance page on port %d, data %s" % (PORT, DATA), flush=True)
        srv.serve_forever()
