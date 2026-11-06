# Tracker dropped every detected face

Date: 2026-11-06. Status: fixed.

**Symptom:** SCRFD found faces (max score 0.864, sane boxes such as 387x480 px), but after nvtracker the object count was 0.

**Cause:** With `network-type=100` (raw tensors), nvinfer does not set `NvDsFrameMeta.bInferDone`. nvtracker treats such frames as having no fresh detections and ignores objects added by a probe.

**Fix:** The detection probe sets `fm->bInferDone = TRUE` when it finds the tensor output meta.

**Proof:** The next run reported 4 tracks and 4 recognitions on the 4-photo test video.
