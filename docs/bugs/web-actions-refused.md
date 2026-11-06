# Service buttons refused the owner's own clicks

Date: 2026-11-07. Status: fixed in v1.2.1. Found by: the owner, on the first day of v1.2.0 ("After clicking it says The request was refused (expired form or another site)").

**Symptom:** Stop (and every service button) on the System page answered "The request was refused (expired form or another site)", from the owner's own browser, on a freshly loaded page.

**Cause:** every page sent `Referrer-Policy: no-referrer`. Under that policy Chrome sends `Origin: null` on form posts, so the same-origin check (Origin must name this host) refused them. The self-test missed it because it set the `Origin` header itself, the way a script does, not the way a browser does.

**Fix:** `Referrer-Policy: same-origin` (and the matching meta tag): the browser now sends this site's origin to itself and still sends nothing to other sites. The self-test checks the header and that `Origin: null` stays refused.

**Also fixed with it:** Pause on the Live page left a broken image; it now keeps the last frame with a "Paused" label. The browser's pop-up for confirmations is replaced by the site's own dialog.

**Lesson:** a self-test of a browser security check has to send what browsers send.
