#!/usr/bin/env python3
"""Fix the SCRFD-10G ONNX model to a 1x3x384x640 input, as facepipe expects.

Input: scrfd_10g_gnkps.onnx from the InsightFace-REST model zoo (outputs named score_8, bbox_8, kps_8, ... _16, _32).
Output: scrfd_10g_384x640.onnx, byte-identical to InsightFace-REST's own reshape for SCRFD (input fixed, outputs
untouched; TensorRT infers them).
Needs the `onnx` package; run it on any PC (not needed on the Nano):
    python3 tools/reshape_scrfd.py scrfd_10g_gnkps.onnx models/scrfd_10g_384x640.onnx
"""
import sys

import onnx

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
names = [o.name for o in m.graph.output]
need = ["%s_%d" % (k, s) for k in ("score", "bbox", "kps") for s in (8, 16, 32)]
missing = [n for n in need if n not in names]
if missing:
    sys.exit("not the expected SCRFD model: missing outputs %s (found %s)" % (missing, names))
dims = m.graph.input[0].type.tensor_type.shape.dim
for d, v in zip(dims, (1, 3, 384, 640)):  # setting dim_value replaces a symbolic dim; outputs stay as they are
    d.dim_value = v
onnx.checker.check_model(m)
onnx.save(m, dst)
print("saved", dst, "input", [d.dim_value for d in m.graph.input[0].type.tensor_type.shape.dim])
