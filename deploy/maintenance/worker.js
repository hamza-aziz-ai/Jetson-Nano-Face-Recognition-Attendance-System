// Cloudflare Worker in front of the website's pages (D-037). It passes every request to the Nano through the tunnel
// and shows the maintenance page below only when the Nano cannot answer: 530 (tunnel down, Cloudflare error 1033),
// 502 (cloudflared up, website down), 504 and 520-529 (connection errors). Answers from the website itself, its own
// 503s included, pass through unchanged. Deploy: sh deploy/maintenance/deploy.sh <hostname> (docs/REMOTE-ACCESS.md).
const DOWN = new Set([502, 504, 520, 521, 522, 523, 524, 525, 526, 527, 528, 529, 530]);

export default {
  async fetch(request) {
    try {
      const r = await fetch(request);
      return DOWN.has(r.status) ? maintenance() : r;
    } catch (e) {
      return maintenance();
    }
  },
};

function maintenance() {
  return new Response(PAGE, {
    status: 503,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "retry-after": "30",
      "x-content-type-options": "nosniff",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
    },
  });
}

const LOGO = "data:image/svg+xml," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" fill="#2457c5"/>' +
  '<circle cx="12" cy="10" r="3.6" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M5.5 19c1.3-3 3.7-4.4 6.5-4.4' +
  's5.2 1.4 6.5 4.4" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>');

const PAGE = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Down for maintenance &middot; Face Attendance</title>
<link rel="icon" href="${LOGO}">
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
color:#e6ecf8;background:linear-gradient(160deg,#0b1730 0%,#13244a 60%,#1d3466 100%) fixed}
header{width:100%;max-width:680px;margin:0 auto;padding:1.4rem 1.2rem 0;display:flex;align-items:center;gap:.7rem}
header img{width:32px;height:32px}header b{display:block;color:#fff}header small{color:#b8c4dc;font-size:.8rem}
main{flex:1;display:flex;flex-direction:column;justify-content:center;width:100%;max-width:680px;margin:0 auto;padding:1rem 1.2rem}
.card{background:#fff;color:#1d2939;border-radius:14px;padding:2rem 1.8rem;box-shadow:0 18px 50px rgba(0,0,0,.35);text-align:center}
.icon{width:64px;height:64px;margin:0 auto 1rem;border-radius:50%;display:grid;place-items:center;background:#fff4e5}
.icon svg{width:32px;height:32px;stroke:#b54708;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
h1{margin:0 0 .5rem;font-size:1.6rem;color:#101828}
p{margin:.4rem auto;max-width:30rem;color:#475467;text-align:justify;hyphens:auto}
.retry{display:inline-flex;align-items:center;gap:.5rem;margin-top:1.2rem;padding:.35rem .9rem;border-radius:999px;background:#eef4ff;
color:#2457c5;font-size:.85rem;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;background:#2457c5;animation:p 1.6s ease-in-out infinite}
@keyframes p{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
footer{text-align:center;color:#b8c4dc;font-size:.8rem;padding:1.2rem}
</style>
</head>
<body>
<header><img src="${LOGO}" alt=""><span><b>Face Attendance</b><small>Jetson Nano edge AI</small></span></header>
<main>
<div class="card" role="status">
<div class="icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/></svg></div>
<h1>Down for maintenance</h1>
<p>The attendance device cannot be reached right now: it is restarting, being updated, or its internet connection is down. Nothing needs to be done on your side.</p>
<p>This page checks again every 30 seconds and opens the website as soon as the device answers.</p>
<span class="retry"><span class="dot" aria-hidden="true"></span>Checking again automatically</span>
</div>
</main>
<footer>Recognition runs on the device itself; nothing is stored in the cloud.</footer>
</body>
</html>`;
