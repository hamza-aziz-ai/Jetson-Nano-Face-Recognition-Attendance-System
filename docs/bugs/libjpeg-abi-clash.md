# libjpeg ABI clash

Date: 2026-11-06. Status: fixed.

**Symptom:** The pipeline stopped at start with `JPEG parameter struct mismatch: library thinks size is 584, caller expects 728`.

**Cause:** OpenCV `imgcodecs` (built with libjpeg-turbo, libjpeg 8 API) and the L4T JPEG library used by nvjpegenc export the same symbols with different struct sizes. Whichever loads first serves both.

**Fix:** The live `facepipe` binary no longer links `imgcodecs`; face snapshots are written as BMP by hand. Enrollment, which needs `imread`, is a separate binary (`facepipe-enroll`, built with `-DFACEPIPE_ENROLL`).

**Proof:** `ldd facepipe` shows no imgcodecs or libjpeg from OpenCV; the pipeline runs with the JPEG branch active.
