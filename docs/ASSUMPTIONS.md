# Assumptions and open questions

Mark each item verified, unverified or wrong. Clear or consciously accept the unverified ones before real people depend on the system.

| Assumption | Status | Note |
| --- | --- | --- |
| Cosine 0.5 separates enrolled people from strangers | Unverified | Taken from the CCTV system; only one enrolled person tested; no labelled set |
| Match rule (cosine and L2) | Verified | `facepipe --selftest` passed on the Nano |
| Embeddings match the InsightFace reference | Verified | Cosine 0.97-0.99 on the same images; ArcFace output equals FP32 ONNX exactly on the same crop |
| 15 fps processing is enough for attendance | Verified for one walking person | Not tested with groups |
| The IOU tracker keeps one ID per visit | Verified for one person | One track held 1,143 of 1,147 frames; crowds untested |
| Event clips and evidence frames are written correctly | Verified | Real events: valid 8.8 s 1080p clip and 1080p evidence JPEG; the evidence showed the old label until fixed by timestamp ordering (bugs/evidence-shows-old-label.md), then the name |
| RTSP stream plays in VLC with the login | Partly verified | Server listening on 8554 with the login set; playback not yet confirmed |
| Weekly rotation frees space before the volume fills | Unverified | Logic reviewed, never triggered (64 GB volume nearly empty) |
| CSI cameras work | Verified | Raspberry Pi HQ Camera after removing R8, with `bufapi-version=1`; natural colour with a lens fitted; recognition at cosine 0.62-0.69; IMX219 not tested |
| The Nano survives continuous MAXN load | Unverified | It crashed once at MAXN without a fan; 5V 4A supply and fan on order |
| microSD endurance is adequate | Unverified | Writes: small snapshots, events, clips capped at 64 GB |
| Faces upside down are not recognized | Verified | Landmarks are mislabeled; the camera must be mounted upright or the image flipped |
