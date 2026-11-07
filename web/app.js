(function () {
  "use strict";
  var page = document.body.dataset.page;

  // status pill, every 10 s
  var pill = document.getElementById("status"), started = null, reach = true, out = false;
  // the session ended (12 h, signed out elsewhere, new password): stop everything and go to the sign-in page
  function signedIn(r) {
    if (r.status === 401) {
      out = true; setLive(false);
      location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      throw new Error("signed out");
    }
    return r;
  }
  function poll() {
    fetch("/api/status", { credentials: "same-origin", cache: "no-store" })
      .then(signedIn).then(function (r) { return r.json(); })
      .then(function (s) {
        pill.className = "pill " + s.state; pill.textContent = s.detail;
        // the web server restarted (an update) or came back: its old video connection is gone, open a new one
        if ((started !== null && s.web_started !== started) || !reach) liveRestart();
        started = s.web_started; reach = true;
      })
      .catch(function () { pill.className = "pill stopped"; pill.textContent = "Page cannot reach the device"; reach = false; });
  }
  var liveRestart = function () {};
  if (pill) setInterval(poll, 10000);  // public pages (registration, sign-in) have no device status

  // small screens: the sidebar opens as a menu
  var mb = document.getElementById("menubtn"), side = document.getElementById("side");
  if (mb) mb.addEventListener("click", function () {
    var open = side.classList.toggle("open");
    mb.setAttribute("aria-expanded", open ? "true" : "false");
  });

  // registration: drop zone and previews of the chosen photos
  var drop = document.getElementById("drop"), pv = document.getElementById("previews");
  if (drop) {
    var fin = drop.querySelector("input[type=file]");
    var show = function () {
      pv.textContent = "";
      Array.prototype.slice.call(fin.files, 0, 5).forEach(function (f) {
        var im = document.createElement("img");
        im.alt = f.name; im.src = URL.createObjectURL(f);
        im.onload = function () { URL.revokeObjectURL(im.src); };
        pv.appendChild(im);
      });
    };
    fin.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) { fin.files = e.dataTransfer.files; show(); });
  }

  // video upload: the file is the request body, so the bar can show progress; the device checks it and answers JSON
  var upf = document.getElementById("upform");
  if (upf) upf.addEventListener("submit", function (e) {
    e.preventDefault();
    var file = document.getElementById("upfile").files[0], bar = document.getElementById("upbar"),
      msg = document.getElementById("upmsg"), btn = upf.querySelector("button");
    if (!file) return;
    if (file.size > +upf.dataset.limit) { msg.textContent = "The file is larger than " + Math.round(upf.dataset.limit / 1048576) + " MB."; return; }
    var x = new XMLHttpRequest();
    x.open("POST", "/uploads/add");
    x.setRequestHeader("X-CSRF", upf.dataset.csrf);
    x.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    x.upload.onprogress = function (ev) { if (ev.lengthComputable) bar.value = 100 * ev.loaded / ev.total; };
    x.onload = function () {
      var j = {};
      try { j = JSON.parse(x.responseText); } catch (err) {}
      if (x.status === 401) { location.href = "/login?next=%2Fuploads"; return; }
      if (j.ok) { location.href = "/uploads"; return; }
      msg.textContent = j.error || "The upload failed.";
      btn.disabled = false; bar.hidden = true;
    };
    x.onerror = function () { msg.textContent = "The upload was interrupted."; btn.disabled = false; bar.hidden = true; };
    btn.disabled = true; bar.hidden = false; bar.value = 0;
    msg.textContent = "Uploading; the device checks the video when it has arrived.";
    x.send(file);
  });

  // System page: device health every 5 s, so a problem shows up while you look at the page
  var hp = document.getElementById("health");
  if (hp) setInterval(function () {
    if (document.hidden) return;
    fetch("/api/health", { credentials: "same-origin", cache: "no-store" })
      .then(signedIn).then(function (r) { return r.json(); }).then(function (j) { hp.innerHTML = j.html; }).catch(function () {});
  }, 5000);

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

  // live view: the stream runs only while shown (facepipe encodes JPEG only while someone watches). Pausing keeps the
  // last frame on a canvas instead of leaving an empty image.
  var img = document.getElementById("liveimg"), lb = document.getElementById("livebtn");
  var frozen = document.getElementById("livefrozen"), tag = document.getElementById("livetag"), playing = false;
  function setLive(on) {
    if (!img || on === playing) return;
    playing = on;
    if (on) {
      frozen.hidden = true; tag.hidden = true; img.hidden = false;
      img.src = "/live.mjpg?t=" + Date.now();
    } else {
      if (img.naturalWidth) {
        frozen.width = img.naturalWidth; frozen.height = img.naturalHeight;
        frozen.getContext("2d").drawImage(img, 0, 0);
        frozen.hidden = false;
      }
      tag.hidden = false; img.hidden = true;
      img.removeAttribute("src");
    }
    if (lb) { lb.textContent = on ? "Pause" : "Resume"; lb.setAttribute("aria-pressed", on ? "false" : "true"); }
  }
  if (img) {
    liveRestart = function () { if (playing) { playing = false; setLive(true); } };
    // dropped stream: retry after 3 s if still signed in (a 401 goes to the sign-in page instead of retrying forever)
    img.addEventListener("error", function () {
      if (playing && !out) setTimeout(function () {
        fetch("/api/status", { credentials: "same-origin", cache: "no-store" }).then(signedIn)
          .then(function () { liveRestart(); }).catch(function () {});
      }, 3000);
    });
    setLive(true);
    if (lb) lb.addEventListener("click", function () { setLive(!playing); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) setLive(false); });
  }

  // confirmations for service actions: the site's own dialog, not the browser's pop-up
  var dlg = document.getElementById("confirm"), pending = null;
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      pending = f;
      document.getElementById("dlg-title").textContent = f.dataset.title;
      document.getElementById("dlg-text").textContent = f.dataset.confirm;
      var ok = document.getElementById("dlg-ok");
      ok.textContent = f.dataset.ok;
      ok.className = "btn " + (f.dataset.kind === "danger" ? "danger-solid" : "primary");
      dlg.showModal();
      document.getElementById("dlg-cancel").focus();
    });
  });
  if (dlg) {
    document.getElementById("dlg-cancel").addEventListener("click", function () { pending = null; dlg.close(); });
    document.getElementById("dlg-ok").addEventListener("click", function () {
      var f = pending;
      dlg.close();
      if (f) { f.querySelector("button").disabled = true; f.submit(); }  // form.submit() skips the submit handler
    });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) { pending = null; dlg.close(); } });  // click outside
  }

  // invitation link: copy to clipboard
  var cp = document.getElementById("copylink");
  if (cp) cp.addEventListener("click", function () {
    var inp = document.getElementById("invlink");
    inp.select();
    (navigator.clipboard ? navigator.clipboard.writeText(inp.value) : Promise.reject()).then(function () {
      cp.textContent = "Copied";
    }, function () { document.execCommand("copy"); cp.textContent = "Copied"; });
  });

  // registration: shrink photos to 1600 px JPEG on the person's device (fast upload, no camera metadata), then send
  var rf = document.getElementById("regform");
  function shrink(file) {
    return new Promise(function (ok) {
      var url = URL.createObjectURL(file), im = new Image();
      im.onload = function () {
        var s = Math.min(1, 1600 / Math.max(im.width, im.height)), c = document.createElement("canvas");
        c.width = Math.round(im.width * s); c.height = Math.round(im.height * s);
        c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(function (b) { ok(b || file); }, "image/jpeg", 0.92);
      };
      im.onerror = function () { URL.revokeObjectURL(url); ok(file); };  // the server says what is wrong with it
      im.src = url;
    });
  }
  if (rf) rf.addEventListener("submit", function (e) {
    e.preventDefault();
    var btn = rf.querySelector("button[type=submit]"), box = document.getElementById("regerr");
    var files = Array.prototype.slice.call(rf.querySelector("input[type=file]").files);
    btn.disabled = true; btn.textContent = "Sending…"; box.hidden = true;
    Promise.all(files.map(shrink)).then(function (blobs) {
      var fd = new FormData(rf);
      fd.delete("photos");
      blobs.forEach(function (b, i) { fd.append("photos", b, "photo" + (i + 1) + ".jpg"); });
      return fetch("/register", { method: "POST", body: fd, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { location.href = j.next; return; }
      box.textContent = j.error; box.hidden = false; btn.disabled = false; btn.textContent = "Send Registration";
    }).catch(function () {
      box.textContent = "Could not send; check the connection and try again."; box.hidden = false;
      btn.disabled = false; btn.textContent = "Send Registration";
    });
  });

  // pages that show today's data refresh themselves, unless the user is searching or watching live
  if (document.body.dataset.refresh === "1" || page === "overview") setInterval(function () {
    if (!document.hidden && !(q && q.value) && !img) location.reload();
  }, 30000);
})();
