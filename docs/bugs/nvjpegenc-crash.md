# nvjpegenc segmentation fault

Date: 2026-11-06. Status: worked around.

**Symptom:** facepipe crashed seconds after the video outputs were added. gdb: SIGSEGV in thread `qj:src`, in `jpegTegraEncoderCompress` (libnvjpeg.so) via libnvtvmr.so.

**Cause:** On JetPack 4.6, `nvjpegenc` expects the older NvBuffer memory layout and crashes on DeepStream's NvBufSurface buffers in GPU memory.

**Fix:** nvvideoconvert outputs I420 in system memory for the JPEG branch; nvjpegenc still encodes on NVJPG.

**Cost:** One copy of each frame to system memory; overall throughput went from 15.2 to 13-15 fps with all outputs on.
