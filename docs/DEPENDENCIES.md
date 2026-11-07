# Dependencies and secrets

No dependency is downloaded during a build or at run time: everything comes with JetPack, is a file placed by hand, or is the one build tool installed once by hand (Nuitka, below).

## Platform (from JetPack 4.6.6)

| Dependency | Version | Why | Maintainer |
| --- | --- | --- | --- |
| DeepStream SDK | 6.0.1 | Pipeline elements: nvstreammux, nvtracker, nvdsosd, nvvideoconvert; NvBufSurfTransform (VIC) for the detector input | NVIDIA |
| TensorRT | 8.2.1 | Inference engines | NVIDIA |
| CUDA | 10.2 | GPU memory and copies | NVIDIA |
| GStreamer | 1.14.5 | Pipeline framework, appsink/appsrc, mp4mux, rtph264pay | GStreamer project via Ubuntu |
| gst-rtsp-server | 1.14.5 runtime library | RTSP server (prototypes declared in facepipe.cpp) | GStreamer project via Ubuntu |
| L4T multimedia | 32.7.6 | nvv4l2decoder, nvv4l2h264enc, nvjpegenc, nvarguscamerasrc | NVIDIA |
| OpenCV | 4.9.0 with CUDA (built from source on the device) | Alignment warp, SVD, image reading in enrollment | OpenCV project |
| Python | 3.6.9 standard library | Web page | Python project via Ubuntu |
| Nuitka | 4.3.4 (`pip3 install --user nuitka==4.3.4`, build time only) | Compiles `web.py` into `webapp.so`, so a deployed device carries no readable Python (D-043). `deploy/install.sh` refuses to install without it: an update would fail and that release would never be retried | Nuitka project via PyPI |

## Models (not in the repository)

| Model | File | Why | Source and license |
| --- | --- | --- | --- |
| SCRFD-10G with keypoints | `models/scrfd_10g_384x640.onnx` (reshaped from `scrfd_10g_gnkps`) | Face detection and landmarks | InsightFace; check the license before commercial use |
| ArcFace w600k_r50 | `models/w600k_r50.onnx` (md5 80248d427976241cbd1343889ed132b3) | Face embeddings | InsightFace buffalo_l; same license note |

## Secrets

| Secret | Where | Set by |
| --- | --- | --- |
| Dashboard login | `web_auth` (PBKDF2 hash, mode 0600) | Owner: `python3 web.py --set-password <user>` |
| RTSP login | `rtsp_auth` (plain, mode 0600) | Owner: `python3 web.py --set-rtsp-password <user>` |
| Camera address | `facepipe.env` | Owner; the repository has `facepipe.env.example` with a placeholder |
| GitHub read-only token | `gh_token` (mode 0600) | Owner: the repository is private, and automatic updates read the release list and clone the tag with it (D-044). Fine-grained, this repository only, Contents: read |

Every push is scanned for IP addresses and these file names before it leaves the machine.
