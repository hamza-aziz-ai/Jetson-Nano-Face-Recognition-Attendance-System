# Embeddings disagreed with the reference server

Date: 2026-11-06. Status: fixed upstream (CCTV v2.15.2); this project uses RGB.

**Symptom:** On the same decoded frames, cosine between this pipeline and the CCTV InsightFace-REST server was only 0.91-0.93, where 0.98+ was expected.

**Investigation:** Landmarks agreed within 1-3 px. Sending this pipeline's own 112x112 crop to the server still gave 0.935, so the gap was in recognition, not the crop. An FP32 ONNX run of the same crop matched this pipeline exactly (1.0000) and matched the server only when fed BGR (1.0000 vs 0.9349 for RGB).

**Cause:** The server's TensorRT ArcFace backend defaulted to `swapRB=False` on images decoded as BGR, so w600k_r50 (trained on RGB) received BGR.

**Fix:** Server fixed and gallery re-embedded in RGB in the companion project; this pipeline switched to RGB (`REC_BGR=false`). Same-frame agreement afterwards: 0.979-0.988.
