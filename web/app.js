(function () {
  "use strict";
  var page = document.body.dataset.page;

  // status pill, every 10 s
  var pill = document.getElementById("status");
  function poll() {
    fetch("/api/status", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (s) { pill.className = "pill " + s.state; pill.textContent = s.detail; })
      .catch(function () { pill.className = "pill stopped"; pill.textContent = "Page cannot reach the device"; });
  }
  setInterval(poll, 10000);

  // sortable tables
  document.querySelectorAll("table[data-sort]").forEach(function (t) {
    t.querySelectorAll("th[data-k]").forEach(function (th) {
      th.querySelector("button").addEventListener("click", function () {
        var asc = th.getAttribute("aria-sort") !== "ascending";
        t.querySelectorAll("th").forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", asc ? "ascending" : "descending");
        var col = th.cellIndex, rows = Array.prototype.slice.call(t.tBodies[0].rows);
        rows.sort(function (a, b) {
          var x = a.cells[col].dataset.v, y = b.cells[col].dataset.v, nx = parseFloat(x), ny = parseFloat(y);
          var c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y);
          return asc ? c : -c;
        });
        rows.forEach(function (r) { t.tBodies[0].appendChild(r); });
      });
    });
  });

  // name search
  var q = document.getElementById("q");
  if (q) q.addEventListener("input", function () {
    var s = q.value.trim().toLowerCase();
    document.querySelectorAll("[data-name]").forEach(function (el) { el.hidden = s && el.dataset.name.indexOf(s) < 0; });
  });

  // attendance: date picker, print
  var dp = document.getElementById("date");
  if (dp) dp.addEventListener("change", function () { if (dp.value) dp.form.submit(); });
  var pr = document.getElementById("print");
  if (pr) pr.addEventListener("click", function () { window.print(); });

  // live view: the stream runs only while shown (facepipe encodes JPEG only while someone watches)
  var img = document.getElementById("liveimg"), lb = document.getElementById("livebtn");
  function setLive(on) {
    if (!img) return;
    if (on) img.src = "/live.mjpg?t=" + Date.now(); else img.removeAttribute("src");
    if (lb) { lb.textContent = on ? "Pause" : "Resume"; lb.setAttribute("aria-pressed", on ? "false" : "true"); }
  }
  if (img) {
    setLive(true);
    if (lb) lb.addEventListener("click", function () { setLive(!img.getAttribute("src")); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) setLive(false); });
  }

  // confirmations for service actions
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
  });

  // pages that show today's data refresh themselves, unless the user is searching or watching live
  if (document.body.dataset.refresh === "1" || page === "overview") setInterval(function () {
    if (!document.hidden && !(q && q.value) && !img) location.reload();
  }, 30000);
})();
