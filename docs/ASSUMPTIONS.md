# Assumptions and open questions

Mark each item verified, unverified or wrong. Clear or consciously accept the unverified ones before real people depend on the system.

| Assumption | Status | Note |
| --- | --- | --- |
| Cosine 0.5 separates enrolled people from strangers | Unverified | Taken from the CCTV system; only one enrolled person tested; no labelled set |
| Embeddings match the InsightFace reference | Verified | Cosine 0.97-0.99 on the same images; ArcFace output equals FP32 ONNX exactly on the same crop |
| 15 fps processing is enough for attendance | Verified for one walking person | Not tested with groups |
| The IOU tracker keeps one ID per visit | Verified for one person | One track held 1,143 of 1,147 frames; crowds untested |
| Event clips and evidence frames are written correctly | Unverified | Code path ran with the encoders on; no clip seen with a face in front of the camera |
| RTSP stream plays in VLC with the login | Unverified | RTSP login not yet set on the device |
| Weekly rotation frees space before the volume fills | Unverified | Logic reviewed, never triggered (64 GB volume nearly empty) |
| CSI cameras work | Wrong for the Raspberry Pi HQ Camera as shipped | Needs R8 removed; IMX219-based cameras not tested |
| The Nano survives continuous MAXN load | Unverified | It crashed once at MAXN without a fan; 5V 4A supply and fan on order |
| microSD endurance is adequate | Unverified | Writes: small snapshots, events, clips capped at 64 GB |
| Faces upside down are not recognized | Verified | Landmarks are mislabeled; the camera must be mounted upright or the image flipped |
