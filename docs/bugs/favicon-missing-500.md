# Every page asked for an icon the device did not have, and the server dropped the connection

Date: 2026-11-07. Status: fixed in v1.13.0. Found by: the compiled-build work (D-043), while running the website's self-test in a folder that mirrored the device.

**Symptom:** none that anyone noticed. Browsers showed every page correctly. On the device, `GET /static/favicon.svg` closed the connection with no answer (`RemoteDisconnected`), while `/static/logo.svg` answered 200.

**Cause:** two faults, one on top of the other.

1. `web/favicon.svg` was added to the repository in v1.10.0 and is listed in `STATIC`, so the website serves it and `web/layout.html` links it on every page. The shipped file list in `deploy/deploy.sh` was never extended, so no deployed device ever had the file.
2. `Handler.file()` called `os.path.getsize()` on a path it had not checked. A missing file raised `FileNotFoundError` inside the request thread, and `ThreadingHTTPServer` answers such a request with nothing at all. The same hole was open for every caller of `file()`: event clips, evidence photos and profile photos that had been deleted or rotated away would behave the same way.

**Fix:** `file()` answers 404 for a path that is not a file, and `web/favicon.svg` was added to the deployed file list. The self-test asks for a missing static file and expects 404.

**Lesson:** a release's file list is as much a part of the build as the code; a file that only the browser requests, and only for decoration, can be missing for three versions without a single complaint. The self-test passed throughout because it ran inside the source tree, where the file exists. Testing the package, not the checkout, is what found it.
