"""Turn the captured Nano pages into one self-contained demo page."""
import io, json, os, re, urllib.parse
from pathlib import Path

S = Path("/mnt/c/Users/hamza/AppData/Local/Temp/claude/f7760584710cd448/63a89032-f624-4450-a44c-689fb5756b71/scratchpad")
P = S / "nano_pages"
PAGES = [("overview", "/", "Overview"), ("live", "/live", "Live"), ("attendance", "/attendance", "Attendance"),
         ("people", "/people", "People"), ("cameras", "/cameras", "Cameras"), ("uploads", "/uploads", "Video upload"),
         ("users", "/users", "Users"), ("system", "/system", "System")]
VISITS = [(8, 41, 0, "aarav", 0), (8, 44, 0, "neha", 0), (8, 52, 1, "unknown", 1), (9, 3, 0, "ravi", 0),
          (9, 18, 1, "sofia", 0), (9, 26, 0, "unknown", 2), (9, 48, 1, "neha", 0), (10, 2, 0, "ravi", 0),
          (10, 15, 1, "unknown", 1), (10, 39, 0, "sofia", 0), (11, 7, 1, "aarav", 0), (11, 24, 0, "unknown", 3),
          (11, 52, 1, "mei", 0), (13, 5, 0, "mei", 0), (13, 41, 1, "aarav", 0), (14, 2, 0, "ravi", 0),
          (14, 55, 1, "unknown", 4), (15, 9, 0, "sofia", 0), (15, 33, 1, "neha", 0), (15, 58, 0, "mei", 0)]
TRACK = {100 + i: ("Unknown #%d" % v[4] if v[3] == "unknown" else v[3]) for i, v in enumerate(VISITS)}

SKIN = ["#c9a183", "#a9784f", "#7d5536", "#d8bca3", "#95684a", "#bb8f66"]
HAIR = ["#262b33", "#3a2b23", "#15181d", "#4c3625", "#30323a", "#1b1f26"]
SHIRT = ["#3b5a6b", "#454e58", "#53455f", "#3a5a4c", "#5f4a38", "#3f4a63"]
TEAL, AMBER = "#34d399", "#fbbf24"


def h32(s):
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


# Geometry of the subject, in its own units, origin between the collarbones. The head is the part the box frames.
HX, HY, HCY = 20.0, 26.0, -26.0   # head half-width, half-height and centre: the head spans y -52 .. 0
FIG_W, FTOP, FBOT = 46.0, -54.0, 58.0
CELL = 5.6                        # one mosaic cell at full size


def shade(hexcolour, f):
    """f below 1 darkens, above 1 lightens, clamped."""
    n = int(hexcolour[1:], 16)
    out = 0
    for sh in (16, 8, 0):
        out |= max(0, min(255, int(round(((n >> sh) & 255) * f)))) << sh
    return "#%06x" % out


def in_head(cx, cy):
    return (cx * cx) / (HX * HX) + ((cy - HCY) * (cy - HCY)) / (HY * HY) <= 1


def in_body(cx, cy):
    if cy < -12:
        return abs(cx) <= 8            # neck
    t = min(1.0, (cy + 12) / 34.0)     # the shoulders widen over this run, then hold
    return abs(cx) <= 10 + 33 * (t ** 0.5)


def person(seed, x, y, s, cell=None):
    """The subject as the camera recorded them, published the way an evidence still is: pixelated, not drawn. Nothing
    is clipped - a cell is drawn or it is not - so the outline comes out stepped, the way a downsampled frame looks,
    and there is no likeness to mistake for anyone."""
    r = h32(seed)

    def nxt():
        nonlocal r
        r = (r * 1664525 + 1013904223) & 0xFFFFFFFF
        return r / 4294967296.0

    skin, hair, coat = SKIN[h32(seed) % 6], HAIR[(h32(seed) >> 3) % 6], SHIRT[(h32(seed) >> 6) % 6]
    cs = cell or CELL
    cells = []
    cy = FTOP
    while cy < FBOT:
        cx = -FIG_W
        while cx < FIG_W:
            mx, my = cx + cs / 2, cy + cs / 2
            head = in_head(mx, my)
            if head or in_body(mx, my):
                hairline = HCY - HY * 0.18 + (nxt() - 0.5) * cs   # ragged by one cell, so it is not a drawn line
                tone = (hair if my < hairline else skin) if head else (skin if my < -8 else coat)
                f = (0.86 + nxt() * 0.26) * (1 - 0.16 * max(0.0, (mx + FIG_W) / (2 * FIG_W) - 0.3))
                cells.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                             % (cx, cy, cs + 0.4, cs + 0.4, shade(tone, f)))
            cx += cs
        cy += cs
    return '<g transform="translate(%g,%g) scale(%g)">%s</g>' % (x, y, s, "".join(cells))


def svg_uri(w, h, body):
    return "data:image/svg+xml;utf8," + urllib.parse.quote(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">%s</svg>' % (w, h, w, h, body), safe="")


def portrait(label, known):
    c = TEAL if known else AMBER
    return svg_uri(96, 96, '<rect width="96" height="96" fill="#20262d"/>%s<rect x="1" y="1" width="94" height="94" '
                           'fill="none" stroke="%s" stroke-width="2"/>' % (person(label, 48, 74, 1.0, 8), c))


def room(w, h):
    return ('<defs><linearGradient id="r" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1b2430"/>'
            '<stop offset="1" stop-color="#0d131a"/></linearGradient></defs><rect width="%d" height="%d" fill="url(#r)"/>'
            '<path d="M0,%g L%d,%g L%d,%d L0,%dZ" fill="#111922"/>'
            '<rect x="%g" y="%g" width="%g" height="%g" fill="#16212c"/>' % (w, h, h * .62, w, h * .56, w, h, h, w * .06, h * .12, w * .17, h * .34))


def scene(label, known, w=640, h=360):
    """One frame as the camera recorded it: the box frames the head itself, which is where the detector put it."""
    c = TEAL if known else AMBER
    x, y, sc = w / 2.0, h - 36, 2.1
    m = 2.5 * sc
    bx, by = x - HX * sc - m, y + (HCY - HY) * sc - m
    bw, bh = 2 * HX * sc + 2 * m, 2 * HY * sc + 2 * m
    return svg_uri(w, h, room(w, h) + person(label, x, y, sc) +
                   '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" stroke="%s" stroke-width="2"/>'
                   '<rect x="%.1f" y="%.1f" width="%.1f" height="17" fill="%s"/>'
                   '<text x="%.1f" y="%.1f" font-family="ui-sans-serif,system-ui,sans-serif" font-size="11.5" '
                   'font-weight="600" fill="#04201d">%s</text>'
                   '<text x="%d" y="%d" text-anchor="end" font-family="ui-monospace,monospace" font-size="13" '
                   'fill="#8b98a7">SYNTHETIC &#183; DEMO</text>'
                   % (bx, by, bw, bh, c, bx, by - 17, max(bw, len(label) * 6.9 + 14), c,
                      bx + 6, by - 5, label, w - 10, h - 10))


shell = (P / "overview.html").read_text(encoding="utf-8")
head, rest = shell.split('<main id="main">', 1)
_, tail = rest.split("</main>", 1)

sections = []
for pid, href, label in PAGES:
    html = (P / (pid + ".html")).read_text(encoding="utf-8")
    inner = html.split('<main id="main">', 1)[1].split("</main>")[0]
    sections.append('<section data-view="%s"%s>\n%s\n</section>' % (pid, "" if pid == "overview" else " hidden", inner))
body = "\n".join(sections)
page = head + '<main id="main">\n' + body + "\n</main>" + tail

# --- pictures: the gallery photo of an enrolled person, the camera's crop of a stranger
page = re.sub(r"src='/photo/([a-z]+)'", lambda m: 'src="%s"' % portrait(m.group(1), True), page)


def snap(m):
    track = int(m.group(1))
    lbl = TRACK.get(track, "Unknown")
    return 'src="%s"' % portrait(lbl, not lbl.startswith("Unknown"))


page = re.sub(r"src='/snap/\d{4}-\d{2}-\d{2}/\d{6}_(\d+)\.bmp'", snap, page)

# --- evidence links open a drawn frame or clip instead of the recordings volume
page = re.sub(r"href='/rec/\d{4}-\d{2}-\d{2}/\d{6}_(\d+)\.jpg'", lambda m: 'href="#" data-frame="%s"' % m.group(1), page)
page = re.sub(r"href='/rec/[^']*clip\.mp4'", 'href="#" data-clip="1"', page)

# --- navigation between the eight pages, in the page
for pid, href, _l in PAGES:
    page = page.replace("href='%s'" % href, 'href="#%s" data-go="%s"' % (pid, pid))
    page = page.replace('href="%s"' % href, 'href="#%s" data-go="%s"' % (pid, pid))
page = re.sub(r'href=[\'"]/[^\'"#][^\'"]*[\'"]', 'href="#" data-demo-off', page)

# --- the stylesheet and script travel with the page
css = (P / "app.css").read_text(encoding="utf-8")
js = (P / "app.js").read_text(encoding="utf-8")
m = re.search(r'<link rel="stylesheet"[^>]*>', page)
page = page.replace(m.group(0), "<style>" + chr(10) + css + chr(10) + "</style>", 1)
page = re.sub(r'<script src="/static/app\.js[^>]*></script>', "", page)
logo = svg_uri(32, 32, '<rect width="32" height="32" rx="7" fill="#2457c5"/><circle cx="16" cy="13" r="5" fill="none" '
                       'stroke="#fff" stroke-width="2.2"/><path d="M7 26c1.8-4 5-6 9-6s7.2 2 9 6" fill="none" '
                       'stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>')
page = page.replace('src="/static/logo.svg"', 'src="%s"' % logo)
page = re.sub(r'<link rel="icon"[^>]*>', "", page)

scenes = {"known": scene("sofia", True), "stranger": scene("Unknown #2", False)}
shim = """
<script>
window.__SCENES__ = %s;
// The device's own page, served a recorded day instead of a board: no network, no sign-in, nothing to change.
(function () {
  const ok = b => Promise.resolve({ok:true, status:200, json:()=>Promise.resolve(b), text:()=>Promise.resolve(JSON.stringify(b))});
  const ST = %s, HE = %s;
  let n = 0;
  window.fetch = function (p) {
    p = String(p);
    if (p.indexOf("/api/status") === 0) { n++; const s = JSON.parse(JSON.stringify(ST));
      s.updated = Math.floor(Date.now()/1000); s.frames += n * 45; return ok(s); }
    if (p.indexOf("/api/health") === 0) return ok(HE);
    return ok({});
  };
})();
</script>
""" % (json.dumps(scenes), (P / "api_status.json").read_text(), (P / "api_health.json").read_text())

switcher = """
<dialog id="demo-ev" class="dlg"><h2 id="demo-ev-t">Evidence</h2>
<img id="demo-ev-i" alt="" style="width:100%;max-width:640px;background:#000">
<div class="dlg-actions"><button class="btn" type="button" id="demo-ev-x">Close</button></div></dialog>
<script>
(function () {
  const views = [...document.querySelectorAll("[data-view]")];
  const show = id => {
    const v = views.find(s => s.dataset.view === id) || views[0];
    views.forEach(s => { s.hidden = s !== v; });
    document.querySelectorAll("[data-go]").forEach(a =>
      a.dataset.go === v.dataset.view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
    const t = document.querySelector(".crumb b"); if (t) t.textContent = a2t[v.dataset.view] || "Overview";
    window.scrollTo(0, 0);
  };
  const a2t = __TITLES__;
  addEventListener("hashchange", () => show(location.hash.slice(1)));
  document.addEventListener("click", e => {
    const go = e.target.closest("[data-go]"); if (go) { show(go.dataset.go); return; }
    const off = e.target.closest("[data-demo-off]"); if (off) { e.preventDefault(); return; }
    const ev = e.target.closest("[data-frame],[data-clip]");
    if (ev) {
      e.preventDefault();
      const t = ev.closest("tr"), who = t ? (t.querySelector("th,td") || {}).textContent : "";
      const known = !/unknown/i.test(who || "");
      document.getElementById("demo-ev-t").textContent = ev.dataset.clip ? "Event clip" : "Full frame";
      document.getElementById("demo-ev-i").src = window.__SCENES__[known ? "known" : "stranger"];
      document.getElementById("demo-ev").showModal();
    }
  });
  document.getElementById("demo-ev-x").onclick = () => document.getElementById("demo-ev").close();
  document.addEventListener("submit", e => { e.preventDefault(); }, true);
  show(location.hash.slice(1));
})();
</script>
""".replace("__TITLES__", json.dumps({pid: label for pid, _h, label in PAGES}))

banner = """<div class="demo-bar"><b>Demo</b><span>The device's own dashboard, running a recorded day instead of a
Jetson Nano. Faces are drawn in your browser, not photographed &mdash; no picture of a person is in this page, and
nothing here changes anything. The other system, on a Jetson Orin Nano:
<a href="https://evap.hamzaaziz.dev/demo/">evap.hamzaaziz.dev/demo/</a></span></div>
<style>.demo-bar{display:flex;gap:10px 14px;align-items:baseline;flex-wrap:wrap;background:#eef4ff;color:#23406e;
border-bottom:1px solid #c9d8f5;padding:10px max(16px,env(safe-area-inset-right,0px)) 10px max(16px,env(safe-area-inset-left,0px));
font:13px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.demo-bar b{color:#2457c5;font-size:11px;letter-spacing:.09em;text-transform:uppercase}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .demo-bar{background:#16233c;color:#cfdcf6;border-bottom-color:#27405f}}
.signout,.who-link{display:none}</style>
"""

SA = bool(os.getenv("STANDALONE"))  # a page served on its own hostname keeps its document wrapper
if not SA:
    # the artifact skeleton supplies the document wrapper
    for tag in ("<!DOCTYPE html>\n", '<html lang="en">\n', "<head>\n", '<meta charset="utf-8">\n',
                '<meta name="viewport" content="width=device-width,initial-scale=1">\n',
                '<meta name="referrer" content="same-origin">\n', "</head>\n", "</body>\n", "</html>\n"):
        page = page.replace(tag, "", 1)
    page = re.sub(r"<body[^>]*>\n?", "", page, count=1)
    page = page.replace("<title>Overview &middot; Face Attendance</title>",
                        '<title>Facepipe Dashboard</title>\n<meta name="claude:viewport-fit-sides" content="cover">', 1)
else:
    page = page.replace("<title>Overview &middot; Face Attendance</title>",
                        "<title>Face Attendance \u00b7 Demo</title>", 1)
page = page.replace('<a class="skip" href="#main">', banner + '<a class="skip" href="#main">', 1)
page = page + shim + "<script>\n" + js + "\n</script>\n" + switcher

out = S / ("nano_site.html" if SA else "nano.html")
out.write_text(page, encoding="utf-8", newline="\n")
print(out.name, len(page), "bytes | sections", len(sections))
for bad in ("<!DOCTYPE", "<body", "/static/", "/photo/", "/snap/", "/rec/"):
    print("  %-12s left: %d" % (bad, page.count(bad)))
