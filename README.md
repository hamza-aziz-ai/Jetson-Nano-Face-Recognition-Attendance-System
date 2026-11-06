# NVIDIA Jetson Edge Devices

Edge AI projects for NVIDIA Jetson boards. The main project is **facepipe** (`nano-facepipe/`): a standalone, fully offline face recognition attendance system for the **Jetson Nano 4 GB**, built on DeepStream 6.0.1 and TensorRT 8.2, using the Nano's hardware video blocks (NVDEC, NVENC, NVJPG).

## facepipe: face attendance on a Jetson Nano

One camera in, attendance out. No server, no cloud: detection, tracking, recognition, the attendance log and the web dashboard all run on the Nano.

### Features

- **Sources:** RTSP/IP cameras (`rtsp://`, forced to TCP), video files, CSI cameras (`csi:0`, through the Argus ISP) and USB cameras (`usb:/dev/video0`, MJPEG decoded on NVJPG or raw YUY2).
- **Detection:** SCRFD-10G at 640x384 in TensorRT FP16 with a custom decoder that keeps the 5 facial landmarks.
- **Tracking:** NvMultiObjectTracker (IOU), so each person is recognized once per visit, not once per frame.
- **Recognition:** ArcFace `w600k_r50` on faces aligned to the standard 5-point template (Umeyama similarity), matched by cosine against an on-device gallery. Unknown tracks are retried up to 5 times, which recovers from a bad first frame.
- **Enrollment:** put photos in `gallery/<name>/` and run `facepipe-enroll gallery`; the running service reloads the gallery without a restart.
- **Attendance log:** one event per person per visit in daily JSON-lines files, with a face snapshot.
- **Evidence:** for each event, a full-frame 1080p image with the box and name (NVJPG) and an H.264 clip from 3 s before to 5 s after (NVENC), on a size-capped recordings volume that rotates out the oldest week when full.
- **Live video:** boxes and names drawn by `nvdsosd`; an MJPEG live view in the dashboard (encoded only while someone watches) and an RTSP stream at `rtsp://<nano>:8554/live` for VLC or an NVR, both behind a login.
- **Dashboard:** present, absent and unknown faces per day, live status, search and sort, CSV export, print layout. Python standard library only, no internet needed, strict security headers.
- **Runs unattended:** two systemd services that reconnect to the camera and restart on failure.

### Measured on a Jetson Nano 4 GB (MAXN, JetPack 4.6.6)

| Setting | Throughput |
| --- | --- |
| 1080p H.264, detector on every frame, no video outputs | 19.2 fps (GPU bound) |
| 30 fps camera, every 2nd frame processed (default) | 15.0 fps |
| Same, with overlay, NVENC and NVJPG outputs | 13-15 fps |
| SCRFD-10G 640x384 alone (TensorRT FP16) | 55 ms |
| w600k_r50 alone, batch 1 | 48 ms |

Embeddings agree with an InsightFace reference implementation at cosine 0.97-0.99 on the same images, and the Nano's ArcFace output equals FP32 ONNX exactly on the same aligned crop.

### Pipeline

```
source (RTSP/file: uridecodebin+NVDEC | csi: nvarguscamerasrc | usb: v4l2src [+NVJPG decode])
  -> keep every Nth frame -> nvstreammux -> nvvideoconvert RGBA
  -> nvinfer SCRFD (raw tensors) -> [probe: decode boxes + landmarks]
  -> nvtracker -> [probe: label, align 112x112, queue to recognition worker]
  -> nvdsosd -> tee -> nvjpegenc (NVJPG): live view + evidence frames
                    -> nvv4l2h264enc (NVENC) -> RTSP server + event clips (mp4)
recognition worker: w600k_r50 (TensorRT) -> gallery match -> events, snapshots
web.py: dashboard, CSV, live MJPEG, evidence files
```

### Requirements

- Jetson Nano 4 GB with JetPack 4.6.x (L4T 32.7), DeepStream 6.0.1, TensorRT 8.2, CUDA 10.2.
- OpenCV 4.x with `core`, `imgproc` and `imgcodecs` (paths in the `Makefile` point at `/opt/opencv-4.9.0-cuda`; change `OCV` and `OPENCV_PC` for yours).
- Models from [InsightFace](https://github.com/deepinsight/insightface): SCRFD-10G with keypoints, reshaped to a fixed 1x3x384x640 input, and `w600k_r50` (buffalo_l). Check the InsightFace model license before any commercial use.
- A 5V 4A power supply and a fan for continuous MAXN operation.

### Setup

1. Build the TensorRT engines on the Nano (the code expects them in `/mnt/ssd/bench/`; change `DET_ENGINE` and `REC_ENGINE` in `facepipe.cpp` otherwise):
   ```sh
   trtexec --onnx=scrfd_10g_384x640.onnx --fp16 --workspace=1024 --saveEngine=/mnt/ssd/bench/scrfd_384x640_b1.plan
   trtexec --onnx=w600k_r50.onnx --fp16 --workspace=1024 --minShapes=input.1:1x3x112x112 \
     --optShapes=input.1:8x3x112x112 --maxShapes=input.1:16x3x112x112 --saveEngine=/mnt/ssd/bench/r50_b16.plan
   ```
   Also point `onnx-file` and `model-engine-file` in `pgie_scrfd.txt` at your files.
2. Build: `cd nano-facepipe && make all` (builds `facepipe`, `facepipe-enroll`, and copies the sample IOU tracker config).
3. Configure: `cp facepipe.env.example facepipe.env` and set the camera and paths.
4. Enroll: create `gallery/<person_name>/` folders with a few clear photos each (front, slight left and right), then `./facepipe-enroll gallery`.
5. Set the dashboard login: `python3 web.py --set-password <user>`. Optionally the RTSP login: `python3 web.py --set-rtsp-password <user>` (without it the RTSP server stays off).
6. Optional recordings volume, for example a 64 GB ext4 image file:
   ```sh
   sudo fallocate -l 64G /var/lib/facepipe-rec.img
   sudo mkfs.ext4 -q -L facepipe-rec -m 0 /var/lib/facepipe-rec.img
   sudo mkdir -p /mnt/rec
   echo '/var/lib/facepipe-rec.img /mnt/rec ext4 loop,nofail,noatime 0 2' | sudo tee -a /etc/fstab
   sudo mount /mnt/rec && sudo chown $USER:$USER /mnt/rec
   ```
7. Install the services (edit `User=` and paths first):
   ```sh
   sudo cp facepipe.service facepipe-web.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now facepipe.service facepipe-web.service
   ```
8. Open `http://<nano>:8000`. Logs: `sudo journalctl -u facepipe -f`.

### Configuration

All settings are environment variables in `facepipe.env`; see `facepipe.env.example` for the full list with comments. The most used:

| Variable | Default | Meaning |
| --- | --- | --- |
| `FACEPIPE_URI` | | `rtsp://...`, `file://...`, `csi:0`, `usb:/dev/video0` |
| `FACEPIPE_DROP` | `2` | process every Nth frame; keep camera fps / N at or below about 19 on a Nano |
| `FACEPIPE_MATCH` | `0.5` | cosine threshold for a match |
| `FACEPIPE_REC` | `/mnt/rec` | recordings volume; recording turns itself off if it is not writable |
| `FACEPIPE_USB_FORMAT` | `mjpeg` | `mjpeg` or `raw` for USB cameras |
| `FACEPIPE_CAM_FPS` | `30` | capture rate for CSI and USB cameras |

### Security and privacy

- Face photos, embeddings, snapshots and recordings never leave the device.
- The dashboard needs a login (PBKDF2-hashed password, HTTP Basic auth). Use it on a trusted network: it is plain HTTP, so put a TLS reverse proxy in front for anything wider.
- The RTSP stream needs its own login; without one the RTSP server does not start.
- Never commit `web_auth`, `rtsp_auth`, `facepipe.env`, `data/` or `gallery/`; `.gitignore` excludes them.

### Notes and limits

- One camera per Nano. The GPU is the limit: about 19 fps of detection at 640x384.
- An upside-down face is detected but its landmarks are mislabeled, so it is not recognized; flip the camera image if a camera is mounted upside down.
- On JetPack 4.6, `nvjpegenc` crashes on DeepStream buffers in GPU memory, so frames reach it in system memory. The live binary must not link OpenCV `imgcodecs`, whose libjpeg clashes with the L4T one; enrollment is a separate binary for that reason.
- `check_vs_rest.py` compares facepipe embeddings with an InsightFace-REST server, for verifying a deployment.
