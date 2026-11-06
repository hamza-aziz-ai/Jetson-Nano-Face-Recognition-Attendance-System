// Standalone face attendance for the Jetson Nano (DeepStream 6.0.1, TensorRT 8.2, JetPack 4.6.6). No server needed.
// Live:   uridecodebin -> nvstreammux -> nvvideoconvert(RGBA) -> nvinfer(SCRFD-10G 640x384, raw tensors)
//           -> [probe: SCRFD decode + NMS, adds face objects, landmarks packed in misc_obj_info]
//           -> nvtracker -> [probe: per track, Umeyama align 112x112, queue] -> fakesink.
//         A worker runs w600k_r50, matches the gallery (cosine, like CCTV COSINE_THRESHOLD) and appends one event per
//         track to $FACEPIPE_DATA/events-YYYY-MM-DD.jsonl with a face snapshot. Unknown tracks are retried a few times.
// Video:  tracker -> nvdsosd (boxes + names) -> tee: NVJPG 1080p JPEG (live web view, event evidence image) and
//         NVENC H.264 (RTSP rtsp://<nano>:8554/live with login; event clips 3 s before to 5 s after, in $FACEPIPE_REC).
// Sources: rtsp:// file:// http:// (NVDEC), csi:<sensor-id> (Argus/ISP), usb:/dev/videoN (MJPEG on NVJPG, or raw).
// Enroll: facepipe-enroll <gallery_dir>  (same source, -DFACEPIPE_ENROLL; gallery_dir/<person name>/*.jpg|png)
//         -> $FACEPIPE_GALLERY, reloaded live.
// Preprocessing and alignment match InsightFace-REST (SCRFD (x-127.5)/128 RGB, ArcFace x/127.5-1 RGB,
// ArcFace 5-point template, det 0.6, NMS 0.4) so embeddings compare with the CCTV gallery too.
// Usage: facepipe <source> [width height]   e.g. facepipe rtsp://cam/stream 1920 1080 | facepipe csi:0 1920 1080
// Env: FACEPIPE_DIR (app dir with configs and models/, default .), FACEPIPE_DATA (default $FACEPIPE_DIR/data),
//      FACEPIPE_GALLERY (default $FACEPIPE_DATA/gallery.tsv), FACEPIPE_MATCH (cosine threshold, default 0.5),
//      FACEPIPE_DROP=N (process every Nth decoded frame, default 2; 1 = all frames), FACEPIPE_REC (default /mnt/rec),
//      FACEPIPE_RTSP_AUTH (default $FACEPIPE_DIR/rtsp_auth), FACEPIPE_RTSP_PORT (default 8554).
// stdout: one JSON line per recognition attempt (with the embedding); stderr: logs.

#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <cuda_runtime.h>
#include <NvInfer.h>
#include <NvInferPlugin.h>
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include "gstnvdsmeta.h"
#include "gstnvdsinfer.h"
#include "nvbufsurface.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <deque>
#include <dirent.h>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <vector>

#ifndef FACEPIPE_VERSION
#define FACEPIPE_VERSION "dev"  // set from the VERSION file by the Makefile
#endif
static const int NET_W = 640, NET_H = 384;
static const float DET_THRESH = 0.6f, NMS_THRESH = 0.4f;
static const int MIN_FACE_PX = 40;   // ponytail: size gate only; add a pose/blur score if bad crops hurt matching
static const int MAX_TRIES = 5;      // unknown tracks are retried (bad first frame, head turned), then logged as unknown
static const int RETRY_GAP = 8;      // processed frames between attempts on the same track
// w600k_r50 takes RGB. Until CCTV v2.15.2 (2026-11-06, PR #112) REST fed it BGR (TRT Arcface swapRB=False default)
// and the gallery was BGR (Milvus _v4); set true only to match a pre-v2.15.2 REST/gallery.
static const bool REC_BGR = false;
// TensorRT engines live in $FACEPIPE_DIR/models (set in main)
static std::string REC_ENGINE, DET_ENGINE;
static const float ARC_DST[5][2] = {{38.2946f, 51.6963f}, {73.5318f, 51.5014f}, {56.0252f, 71.7366f},
                                    {41.5493f, 92.3655f}, {70.7299f, 92.2041f}};

static std::string env(const char *k, const std::string &def) { const char *v = getenv(k); return v && *v ? v : def; }
static std::string DATA_DIR, GALLERY, REC_DIR;
static const std::string LIVE_DIR = "/dev/shm/facepipe";  // live.jpg for the web page's MJPEG view (RAM, no SSD wear)
static float MATCH = 0.5f;

// ---- gst-rtsp-server 1.14: the runtime library is installed but not its -dev headers, so declare the few calls used.
// ponytail: hand-written prototypes; install libgstrtspserver-1.0-dev and include <gst/rtsp-server/rtsp-server.h> instead
// if more of the API is ever needed.
extern "C" {
typedef struct _GstRTSPServer GstRTSPServer;
typedef struct _GstRTSPMountPoints GstRTSPMountPoints;
typedef struct _GstRTSPMediaFactory GstRTSPMediaFactory;
typedef struct _GstRTSPAuth GstRTSPAuth;
typedef struct _GstRTSPToken GstRTSPToken;
GstRTSPServer *gst_rtsp_server_new(void);
GstRTSPMountPoints *gst_rtsp_server_get_mount_points(GstRTSPServer *);
GstRTSPMediaFactory *gst_rtsp_media_factory_new(void);
void gst_rtsp_media_factory_set_launch(GstRTSPMediaFactory *, const gchar *);
void gst_rtsp_media_factory_set_shared(GstRTSPMediaFactory *, gboolean);
void gst_rtsp_media_factory_add_role(GstRTSPMediaFactory *, const gchar *role, const gchar *fieldname, ...);
void gst_rtsp_mount_points_add_factory(GstRTSPMountPoints *, const gchar *, GstRTSPMediaFactory *);
guint gst_rtsp_server_attach(GstRTSPServer *, GMainContext *);
void gst_rtsp_server_set_auth(GstRTSPServer *, GstRTSPAuth *);
GstRTSPAuth *gst_rtsp_auth_new(void);
gchar *gst_rtsp_auth_make_basic(const gchar *user, const gchar *pass);
void gst_rtsp_auth_add_basic(GstRTSPAuth *, const gchar *basic, GstRTSPToken *);
GstRTSPToken *gst_rtsp_token_new(const gchar *firstfield, ...);
}

struct Face { float x1, y1, x2, y2, score; float kps[10]; };

// ---- SCRFD decode (strides 8/16/32, 2 anchors per cell, outputs already sigmoid) ----
static float iou(const Face &a, const Face &b) {
  float w = std::min(a.x2, b.x2) - std::max(a.x1, b.x1), h = std::min(a.y2, b.y2) - std::max(a.y1, b.y1);
  if (w <= 0 || h <= 0) return 0;
  float in = w * h;
  return in / ((a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - in);
}

template <class Get>  // Get(name) -> const float* for score_8, bbox_8, kps_8, ... or nullptr
static std::vector<Face> decode_scrfd(Get get, float inv_scale) {
  std::vector<Face> c;
  for (int s : {8, 16, 32}) {
    std::string k = std::to_string(s);
    const float *sc = get(("score_" + k).c_str()), *bb = get(("bbox_" + k).c_str()), *kp = get(("kps_" + k).c_str());
    if (!sc || !bb || !kp) continue;
    int gw = NET_W / s, n = gw * (NET_H / s) * 2;
    for (int i = 0; i < n; i++) {
      if (sc[i] < DET_THRESH) continue;
      int cell = i / 2;
      float ax = (cell % gw) * s, ay = (cell / gw) * s;
      Face f;
      f.score = sc[i];
      f.x1 = (ax - bb[i * 4 + 0] * s) * inv_scale; f.y1 = (ay - bb[i * 4 + 1] * s) * inv_scale;
      f.x2 = (ax + bb[i * 4 + 2] * s) * inv_scale; f.y2 = (ay + bb[i * 4 + 3] * s) * inv_scale;
      for (int j = 0; j < 10; j += 2) {
        f.kps[j] = (ax + kp[i * 10 + j] * s) * inv_scale;
        f.kps[j + 1] = (ay + kp[i * 10 + j + 1] * s) * inv_scale;
      }
      c.push_back(f);
    }
  }
  std::sort(c.begin(), c.end(), [](const Face &a, const Face &b) { return a.score > b.score; });
  std::vector<Face> keep;
  for (auto &f : c) {
    bool ok = true;
    for (auto &k : keep) if (iou(f, k) > NMS_THRESH) { ok = false; break; }
    if (ok) keep.push_back(f);
  }
  return keep;
}

// Landmarks ride on the object through the tracker: 10 coords as int16 pixels in misc_obj_info[0..2],
// misc_obj_info[3]=1 marks "has landmarks" (tracker-created objects on skipped frames have 0).
// ponytail: packing hack, use NvDsUserMeta with copy/release funcs if another component needs misc_obj_info.
static void pack_kps(NvDsObjectMeta *o, const float *kps) {
  int16_t v[12] = {0};
  for (int i = 0; i < 10; i++) v[i] = (int16_t)lrintf(kps[i]);
  memcpy(o->misc_obj_info, v, sizeof(v));
  o->misc_obj_info[3] = 1;
}
static bool unpack_kps(NvDsObjectMeta *o, float *kps) {
  if (o->misc_obj_info[3] != 1) return false;
  int16_t v[12];
  memcpy(v, o->misc_obj_info, sizeof(v));
  for (int i = 0; i < 10; i++) kps[i] = v[i];
  return true;
}

// ---- Umeyama similarity (with scale), same as InsightFace-REST _umeyama(lmk, arcface_src, True) ----
static cv::Mat umeyama(const float *kps) {
  double sm[2] = {0, 0}, dm[2] = {0, 0};
  for (int i = 0; i < 5; i++) { sm[0] += kps[2 * i]; sm[1] += kps[2 * i + 1]; dm[0] += ARC_DST[i][0]; dm[1] += ARC_DST[i][1]; }
  for (int k = 0; k < 2; k++) { sm[k] /= 5; dm[k] /= 5; }
  cv::Mat A = cv::Mat::zeros(2, 2, CV_64F);
  double var = 0;
  for (int i = 0; i < 5; i++) {
    double sx = kps[2 * i] - sm[0], sy = kps[2 * i + 1] - sm[1], dx = ARC_DST[i][0] - dm[0], dy = ARC_DST[i][1] - dm[1];
    A.at<double>(0, 0) += dx * sx; A.at<double>(0, 1) += dx * sy;
    A.at<double>(1, 0) += dy * sx; A.at<double>(1, 1) += dy * sy;
    var += sx * sx + sy * sy;
  }
  A /= 5; var /= 5;
  cv::Mat S, U, Vt;
  cv::SVD::compute(A, S, U, Vt);
  double d1 = (cv::determinant(A) < 0) ? -1 : 1;
  cv::Mat D = cv::Mat::eye(2, 2, CV_64F);
  D.at<double>(1, 1) = d1;
  cv::Mat R = U * D * Vt;
  double scale = (S.at<double>(0) + d1 * S.at<double>(1)) / var;
  cv::Mat M(2, 3, CV_64F);
  cv::Mat(scale * R).copyTo(M(cv::Rect(0, 0, 2, 2)));
  M.at<double>(0, 2) = dm[0] - scale * (R.at<double>(0, 0) * sm[0] + R.at<double>(0, 1) * sm[1]);
  M.at<double>(1, 2) = dm[1] - scale * (R.at<double>(1, 0) * sm[0] + R.at<double>(1, 1) * sm[1]);
  return M;
}

// ---- TensorRT engine with input binding 0 and float outputs ----
class TrtLog : public nvinfer1::ILogger {
  void log(Severity s, const char *msg) noexcept override { if (s <= Severity::kWARNING) fprintf(stderr, "[TRT] %s\n", msg); }
} trt_log;

struct Engine {
  nvinfer1::IRuntime *rt = nullptr;
  nvinfer1::ICudaEngine *eng = nullptr;
  nvinfer1::IExecutionContext *ctx = nullptr;
  std::vector<void *> dev;
  std::vector<std::vector<float>> host;
  void load(const char *path, nvinfer1::Dims4 in) {
    initLibNvInferPlugins(&trt_log, "");
    std::ifstream f(path, std::ios::binary);
    std::vector<char> blob((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    rt = nvinfer1::createInferRuntime(trt_log);
    eng = rt->deserializeCudaEngine(blob.data(), blob.size());
    if (!eng) { fprintf(stderr, "cannot load %s\n", path); exit(1); }
    ctx = eng->createExecutionContext();
    if (eng->getBindingDimensions(0).d[0] == -1) ctx->setBindingDimensions(0, in);  // only dynamic engines (ArcFace)
    for (int i = 0; i < eng->getNbBindings(); i++) {
      auto d = ctx->getBindingDimensions(i);
      size_t n = 1;
      for (int k = 0; k < d.nbDims; k++) n *= d.d[k];
      host.emplace_back(n);
      dev.push_back(nullptr);
      cudaMalloc(&dev.back(), n * sizeof(float));
    }
  }
  const float *out(const char *name) { int i = eng->getBindingIndex(name); return i < 0 ? nullptr : host[i].data(); }
  void run() {
    cudaMemcpy(dev[0], host[0].data(), host[0].size() * sizeof(float), cudaMemcpyHostToDevice);
    ctx->executeV2(dev.data());
    for (size_t i = 1; i < dev.size(); i++)
      cudaMemcpy(host[i].data(), dev[i], host[i].size() * sizeof(float), cudaMemcpyDeviceToHost);
  }
};

// 112x112 RGB u8 aligned face -> L2-normalised 512-d embedding
static std::vector<float> embed(Engine &rec, const cv::Mat &face) {
  float *in = rec.host[0].data();
  for (int y = 0; y < 112; y++)
    for (int x = 0; x < 112; x++)
      for (int c = 0; c < 3; c++) in[c * 112 * 112 + y * 112 + x] = face.at<cv::Vec3b>(y, x)[REC_BGR ? 2 - c : c] / 127.5f - 1.f;
  rec.run();
  std::vector<float> e(rec.host[1].begin(), rec.host[1].end());
  double n = 0;
  for (float v : e) n += v * v;
  n = std::sqrt(n);
  for (float &v : e) v /= n;
  return e;
}

static cv::Mat align(const cv::Mat &img, const float *kps) {
  cv::Mat out;
  cv::warpAffine(img, out, umeyama(kps), cv::Size(112, 112), cv::INTER_LINEAR, cv::BORDER_CONSTANT, cv::Scalar(0));
  return out;
}

// ---- gallery: TSV "name<TAB>v1,v2,...,v512", one line per enrolled photo; reloaded when the file changes ----
struct Gallery {
  std::vector<std::string> names;
  std::vector<std::vector<float>> embs;
  time_t mtime = 0;
  void reload_if_changed() {
    struct stat st;
    if (stat(GALLERY.c_str(), &st) != 0 || st.st_mtime == mtime) return;
    mtime = st.st_mtime;
    names.clear(); embs.clear();
    std::ifstream f(GALLERY);
    std::string line;
    while (std::getline(f, line)) {
      size_t tab = line.find('\t');
      if (tab == std::string::npos) continue;
      std::vector<float> e;
      std::stringstream ss(line.substr(tab + 1));
      std::string v;
      while (std::getline(ss, v, ',')) e.push_back(std::stof(v));
      if (e.size() != 512) continue;
      names.push_back(line.substr(0, tab));
      embs.push_back(e);
    }
    fprintf(stderr, "[facepipe] gallery %s: %zu photos\n", GALLERY.c_str(), names.size());
  }
  std::pair<std::string, float> match(const std::vector<float> &e) const {
    float best = -1;
    size_t bi = 0;
    for (size_t i = 0; i < embs.size(); i++) {
      float s = 0;
      for (int k = 0; k < 512; k++) s += e[k] * embs[i][k];
      if (s > best) { best = s; bi = i; }
    }
    return {best >= MATCH ? names[bi] : "unknown", best};
  }
};

// ---- events: data/events-YYYY-MM-DD.jsonl + data/snaps/YYYY-MM-DD/<HHMMSS>_<track>.jpg (local time) ----
static std::string json_str(const std::string &s) {
  std::string o = "\"";
  for (char c : s) { if (c == '"' || c == '\\') o += '\\'; if ((unsigned char)c >= 0x20) o += c; }
  return o + "\"";
}

static void stamp(std::string &day, std::string &hms, std::string *iso = nullptr) {
  time_t t = time(nullptr);
  struct tm lt;
  localtime_r(&t, &lt);
  char b[32];
  strftime(b, sizeof b, "%Y-%m-%d", &lt); day = b;
  strftime(b, sizeof b, "%H%M%S", &lt); hms = b;
  if (iso) { strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%S", &lt); *iso = b; }
}

// ---- video outputs: latest annotated JPEG (NVJPG), H.264 ring + event clips (NVENC), storage rotation ----
static bool rec_ok() { return access(REC_DIR.c_str(), W_OK) == 0; }  // the /mnt/rec mountpoint is root-owned until mounted

static std::mutex j_mu;
static std::condition_variable j_cv;
static std::vector<unsigned char> jpeg;  // latest annotated frame, 1080p
static unsigned long jpeg_seq = 0;

static GstFlowReturn on_jpeg(GstAppSink *s, gpointer) {
  GstSample *smp = gst_app_sink_pull_sample(s);
  if (!smp) return GST_FLOW_OK;
  GstMapInfo m;
  GstBuffer *b = gst_sample_get_buffer(smp);
  if (gst_buffer_map(b, &m, GST_MAP_READ)) {
    {
      std::lock_guard<std::mutex> lk(j_mu);
      jpeg.assign(m.data, m.data + m.size);
      jpeg_seq++;
    }
    j_cv.notify_all();
    struct stat st;  // the web page touches "want" while someone watches; write live.jpg only then
    if (stat((LIVE_DIR + "/want").c_str(), &st) == 0 && time(nullptr) - st.st_mtime < 10) {
      std::string tmp = LIVE_DIR + "/live.jpg.tmp";
      if (FILE *f = fopen(tmp.c_str(), "wb")) {
        fwrite(m.data, 1, m.size, f);
        fclose(f);
        rename(tmp.c_str(), (LIVE_DIR + "/live.jpg").c_str());
      }
    }
    gst_buffer_unmap(b, &m);
  }
  gst_sample_unref(smp);
  return GST_FLOW_OK;
}

// latest annotated frame at least `frames` newer than now (so the name label is drawn), or empty after 600 ms
static std::vector<unsigned char> next_jpeg(unsigned long frames) {
  std::unique_lock<std::mutex> lk(j_mu);
  unsigned long want = jpeg_seq + frames;
  j_cv.wait_for(lk, std::chrono::milliseconds(600), [want] { return jpeg_seq >= want; });
  return jpeg_seq >= want ? jpeg : std::vector<unsigned char>();
}

static const GstClockTime CLIP_PRE = 3 * GST_SECOND, CLIP_POST = 5 * GST_SECOND;
struct AU { GstBuffer *buf; GstClockTime pts; bool key; };
static std::mutex v_mu;
static std::deque<AU> ring;          // last ~CLIP_PRE s of H.264, starting at a keyframe
static GstCaps *h264_caps = nullptr;
static GstClockTime last_pts = 0;
struct Clip { GstElement *pipe = nullptr, *src = nullptr; GstClockTime base = 0, until = 0; std::string rel; };
static Clip clip;

static void rm_day(const std::string &dir) {
  if (DIR *d = opendir(dir.c_str())) {
    while (dirent *e = readdir(d)) if (e->d_name[0] != '.') unlink((dir + "/" + e->d_name).c_str());
    closedir(d);
  }
  rmdir(dir.c_str());
}

// Owner's rule: when the recordings volume is full (>= 90%), delete the oldest week of day folders. Never today.
static void rotate_storage() {
  static std::mutex mu;
  std::lock_guard<std::mutex> lk(mu);
  struct statvfs v;
  if (statvfs(REC_DIR.c_str(), &v) != 0 || v.f_blocks == 0 || 1.0 - (double)v.f_bavail / v.f_blocks < 0.9) return;
  std::vector<std::string> days;
  if (DIR *d = opendir(REC_DIR.c_str())) {
    while (dirent *e = readdir(d)) {
      int y, mo, da;
      if (strlen(e->d_name) == 10 && sscanf(e->d_name, "%4d-%2d-%2d", &y, &mo, &da) == 3) days.push_back(e->d_name);
    }
    closedir(d);
  }
  std::sort(days.begin(), days.end());
  std::string today, hms;
  stamp(today, hms);
  if (days.empty()) return;
  struct tm t = {};
  sscanf(days[0].c_str(), "%4d-%2d-%2d", &t.tm_year, &t.tm_mon, &t.tm_mday);
  t.tm_year -= 1900; t.tm_mon -= 1; t.tm_mday += 7; t.tm_hour = 12;
  mktime(&t);
  char cut[16];
  strftime(cut, sizeof cut, "%Y-%m-%d", &t);
  for (auto &d : days)
    if (d < cut && d != today) { rm_day(REC_DIR + "/" + d); fprintf(stderr, "[facepipe] storage full: deleted %s\n", d.c_str()); }
}

static void clip_push(GstBuffer *b, GstClockTime pts) {  // v_mu held
  GstBuffer *c = gst_buffer_copy(b);  // shares the memory, own timestamps
  GST_BUFFER_PTS(c) = GST_BUFFER_DTS(c) = pts - clip.base;
  GST_BUFFER_DURATION(c) = GST_CLOCK_TIME_NONE;
  gst_app_src_push_buffer(GST_APP_SRC(clip.src), c);
}

static void clip_finish() {  // v_mu held; mp4mux writes the index on EOS, so wait for it off the streaming thread
  GstElement *p = clip.pipe, *s = clip.src;
  clip = Clip();
  gst_app_src_end_of_stream(GST_APP_SRC(s));
  std::thread([p] {
    GstBus *bus = gst_element_get_bus(p);
    if (GstMessage *m = gst_bus_timed_pop_filtered(bus, 10 * GST_SECOND, (GstMessageType)(GST_MESSAGE_EOS | GST_MESSAGE_ERROR)))
      gst_message_unref(m);
    gst_object_unref(bus);
    gst_element_set_state(p, GST_STATE_NULL);
    gst_object_unref(p);
    rotate_storage();
  }).detach();
}

static GstFlowReturn on_h264(GstAppSink *s, gpointer) {
  GstSample *smp = gst_app_sink_pull_sample(s);
  if (!smp) return GST_FLOW_OK;
  GstBuffer *b = gst_sample_get_buffer(smp);
  GstClockTime pts = GST_BUFFER_PTS(b);
  bool key = !GST_BUFFER_FLAG_IS_SET(b, GST_BUFFER_FLAG_DELTA_UNIT);
  {
    std::lock_guard<std::mutex> lk(v_mu);
    if (!h264_caps) h264_caps = gst_caps_ref(gst_sample_get_caps(smp));
    last_pts = pts;
    ring.push_back({gst_buffer_ref(b), pts, key});
    size_t k = 0;  // drop everything before the newest keyframe that is at least CLIP_PRE old
    for (size_t i = 1; i < ring.size(); i++) if (ring[i].key && ring[i].pts + CLIP_PRE <= pts) k = i;
    for (size_t i = 0; i < k; i++) { gst_buffer_unref(ring.front().buf); ring.pop_front(); }
    if (clip.pipe) {
      if (pts > clip.until) clip_finish();
      else clip_push(b, pts);
    }
  }
  gst_sample_unref(smp);
  return GST_FLOW_OK;
}

// start (or extend) the event clip; returns its path relative to REC_DIR, or "" if recording is unavailable
static std::string clip_trigger() {
  if (!rec_ok()) return "";
  std::lock_guard<std::mutex> lk(v_mu);
  if (!h264_caps || ring.empty() || !ring.front().key) return "";
  if (clip.pipe) { clip.until = last_pts + CLIP_POST; return clip.rel; }
  std::string day, hms;
  stamp(day, hms);
  mkdir((REC_DIR + "/" + day).c_str(), 0755);
  std::string rel = day + "/" + hms + "_clip.mp4";
  GError *err = nullptr;
  GstElement *p = gst_parse_launch(("appsrc name=s is-live=true format=time max-bytes=20000000 ! h264parse ! "
                                    "mp4mux faststart=true ! filesink location=" + REC_DIR + "/" + rel).c_str(), &err);
  if (!p) { fprintf(stderr, "clip pipeline: %s\n", err ? err->message : "?"); if (err) g_error_free(err); return ""; }
  clip.pipe = p;
  clip.src = gst_bin_get_by_name(GST_BIN(p), "s");
  gst_object_unref(clip.src);  // the pipeline keeps it alive
  g_object_set(clip.src, "caps", h264_caps, nullptr);
  gst_element_set_state(p, GST_STATE_PLAYING);
  clip.base = ring.front().pts;
  clip.until = last_pts + CLIP_POST;
  clip.rel = rel;
  for (auto &a : ring) clip_push(a.buf, a.pts);
  return rel;
}

static std::string save_evidence(guint64 track) {
  if (!rec_ok()) return "";
  auto j = next_jpeg(2);
  if (j.empty()) return "";
  std::string day, hms;
  stamp(day, hms);
  mkdir((REC_DIR + "/" + day).c_str(), 0755);
  std::string rel = day + "/" + hms + "_" + std::to_string(track) + ".jpg";
  FILE *f = fopen((REC_DIR + "/" + rel).c_str(), "wb");
  if (!f) return "";
  fwrite(j.data(), 1, j.size(), f);
  fclose(f);
  return rel;
}

// 24-bit BMP of a small RGB crop. The live binary must not link OpenCV imgcodecs: its libjpeg clashes with the
// L4T libjpeg that nvjpegenc uses ("JPEG parameter struct mismatch: library thinks size is 584, caller expects 728").
static void write_bmp(const std::string &path, const cv::Mat &rgb) {
  int w = rgb.cols, h = rgb.rows, row = (w * 3 + 3) & ~3;
  uint32_t data = row * h, file = 54 + data;
  unsigned char hd[54] = {'B', 'M'};
  auto put32 = [&hd](int at, uint32_t v) { for (int i = 0; i < 4; i++) hd[at + i] = (v >> (8 * i)) & 255; };
  put32(2, file); put32(10, 54); put32(14, 40); put32(18, w); put32(22, h);
  hd[26] = 1; hd[28] = 24; put32(34, data);
  FILE *f = fopen(path.c_str(), "wb");
  if (!f) return;
  fwrite(hd, 1, 54, f);
  std::vector<unsigned char> line(row, 0);
  for (int y = h - 1; y >= 0; y--) {  // bottom-up, BGR
    for (int x = 0; x < w; x++) {
      const cv::Vec3b &p = rgb.at<cv::Vec3b>(y, x);
      line[x * 3] = p[2]; line[x * 3 + 1] = p[1]; line[x * 3 + 2] = p[0];
    }
    fwrite(line.data(), 1, row, f);
  }
  fclose(f);
}

static void log_event(guint64 track, const std::string &name, float score, float det, const cv::Mat &face_rgb,
                      const std::string &frame, const std::string &clip_rel) {
  auto now = std::chrono::system_clock::now();
  std::string day_s, hms_s, iso_s;
  stamp(day_s, hms_s, &iso_s);
  const char *day = day_s.c_str(), *hms = hms_s.c_str(), *iso = iso_s.c_str();
  std::string snapdir = DATA_DIR + "/snaps/" + day;
  mkdir((DATA_DIR + "/snaps").c_str(), 0755);
  mkdir(snapdir.c_str(), 0755);
  std::string snap = std::string(day) + "/" + hms + "_" + std::to_string(track) + ".bmp";
  write_bmp(DATA_DIR + "/snaps/" + snap, face_rgb);
  FILE *f = fopen((DATA_DIR + "/events-" + day + ".jsonl").c_str(), "a");
  if (!f) { fprintf(stderr, "cannot write events in %s\n", DATA_DIR.c_str()); return; }
  fprintf(f, "{\"time\":\"%s\",\"ts\":%.3f,\"track\":%llu,\"name\":%s,\"score\":%.3f,\"det\":%.3f,\"snap\":%s,\"frame\":%s,\"clip\":%s}\n",
          iso, std::chrono::duration<double>(now.time_since_epoch()).count(), (unsigned long long)track,
          json_str(name).c_str(), score, det, json_str(snap).c_str(), json_str(frame).c_str(), json_str(clip_rel).c_str());
  fclose(f);
}

// ---- per-track state, shared by the tracker probe (producer) and the worker ----
struct TrackState { int tries = 0; int last_frame = -1000000; bool pending = false, done = false; std::string name; float score = 0; };
static std::mutex st_mu;
static std::unordered_map<guint64, TrackState> tracks;  // ponytail: cleared at 100k tracks; prune by last-seen if long runs need it

struct Job { guint64 track; gint frame; float box[5]; cv::Mat face; };  // face: 112x112 RGB u8
static std::mutex q_mu;
static std::condition_variable q_cv;
static std::deque<Job> q_jobs;
static std::atomic<bool> q_stop{false};
static FILE *json_out = stdout;  // real stdout; fd 1 is pointed at stderr so DeepStream's own prints don't mix in
static const size_t Q_MAX = 32;  // drop jobs if the GPU falls behind instead of growing memory
static std::atomic<long> n_frames{0}, n_faces{0}, n_known{0}, n_unknown{0}, n_dropped{0};

static void rec_worker() {
  Engine rec;
  rec.load(REC_ENGINE.c_str(), nvinfer1::Dims4{1, 3, 112, 112});
  Gallery gal;
  while (true) {
    Job j;
    {
      std::unique_lock<std::mutex> lk(q_mu);
      q_cv.wait(lk, [] { return q_stop || !q_jobs.empty(); });
      if (q_jobs.empty()) break;
      j = std::move(q_jobs.front());
      q_jobs.pop_front();
    }
    gal.reload_if_changed();
    auto t0 = std::chrono::steady_clock::now();
    auto e = embed(rec, j.face);
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    auto m = gal.match(e);
    bool known = m.first != "unknown";
    int tries;
    {
      std::lock_guard<std::mutex> lk(st_mu);
      auto &s = tracks[j.track];
      s.pending = false;
      tries = s.tries;
      if (known || s.tries >= MAX_TRIES) { s.done = true; s.name = m.first; s.score = m.second; }  // label shows from now
    }
    if (known || tries >= MAX_TRIES) {
      std::string c = clip_trigger();       // clip starts CLIP_PRE before now
      std::string fr = save_evidence(j.track);  // waits for a frame with the name drawn
      log_event(j.track, m.first, m.second, j.box[4], j.face, fr, c);
      (known ? n_known : n_unknown)++;
    }
    std::string s;
    char tmp[64];
    for (int i = 0; i < 512; i++) { snprintf(tmp, sizeof tmp, i ? ",%.5f" : "%.5f", e[i]); s += tmp; }
    fprintf(json_out, "{\"frame\":%d,\"track\":%llu,\"try\":%d,\"name\":%s,\"score\":%.3f,\"bbox\":[%.0f,%.0f,%.0f,%.0f],\"det\":%.3f,\"rec_ms\":%.1f,\"emb\":[%s]}\n",
            j.frame, (unsigned long long)j.track, tries, json_str(m.first).c_str(), m.second, j.box[0], j.box[1], j.box[2],
            j.box[3], j.box[4], ms, s.c_str());
    fflush(json_out);
  }
}

// ---- probes ----
static int mux_w = 1920, mux_h = 1080;

static GstPadProbeReturn pgie_probe(GstPad *, GstPadProbeInfo *info, gpointer) {
  NvDsBatchMeta *bm = gst_buffer_get_nvds_batch_meta(GST_PAD_PROBE_INFO_BUFFER(info));
  float inv_scale = 1.f / std::min((float)NET_W / mux_w, (float)NET_H / mux_h);  // maintain-aspect-ratio, pad right/bottom
  for (NvDsMetaList *l = bm->frame_meta_list; l; l = l->next) {
    auto *fm = (NvDsFrameMeta *)l->data;
    for (NvDsMetaList *u = fm->frame_user_meta_list; u; u = u->next) {
      auto *um = (NvDsUserMeta *)u->data;
      if (um->base_meta.meta_type != NVDSINFER_TENSOR_OUTPUT_META) continue;
      fm->bInferDone = TRUE;  // network-type=100 leaves it 0, and nvtracker then ignores this frame's detections
      auto *tm = (NvDsInferTensorMeta *)um->user_meta_data;
      auto get = [tm](const char *name) -> const float * {
        for (unsigned i = 0; i < tm->num_output_layers; i++)
          if (!strcmp(tm->output_layers_info[i].layerName, name)) return (const float *)tm->out_buf_ptrs_host[i];
        return nullptr;
      };
      for (auto &f : decode_scrfd(get, inv_scale)) {
        float l = std::max(0.f, f.x1), t = std::max(0.f, f.y1);
        float w = std::min((float)mux_w, f.x2) - l, h = std::min((float)mux_h, f.y2) - t;
        if (w <= 0 || h <= 0) continue;
        NvDsObjectMeta *o = nvds_acquire_obj_meta_from_pool(bm);
        o->unique_component_id = 1;
        o->class_id = 0;
        o->object_id = UNTRACKED_OBJECT_ID;
        o->confidence = f.score;
        o->rect_params.left = l; o->rect_params.top = t; o->rect_params.width = w; o->rect_params.height = h;
        strncpy(o->obj_label, "face", MAX_LABEL_SIZE);
        pack_kps(o, f.kps);
        nvds_add_obj_meta_to_frame(fm, o, nullptr);
      }
    }
  }
  return GST_PAD_PROBE_OK;
}

// true if this track should get a (new) recognition attempt now; marks it pending
static bool want_attempt(guint64 id, int frame) {
  std::lock_guard<std::mutex> lk(st_mu);
  if (tracks.size() > 100000) tracks.clear();
  auto &s = tracks[id];
  if (s.done || s.pending || frame - s.last_frame < RETRY_GAP) return false;
  s.pending = true;
  s.tries++;
  s.last_frame = frame;
  return true;
}

// on-screen box + label for nvdsosd: green name, red Unknown, amber while recognizing
static void label(NvDsObjectMeta *o) {
  std::string txt = "Recognizing...";
  double r = 0.96, g = 0.62, b = 0.04;
  if (o->object_id != UNTRACKED_OBJECT_ID) {
    std::lock_guard<std::mutex> lk(st_mu);
    auto it = tracks.find(o->object_id);
    if (it != tracks.end() && it->second.done) {
      if (it->second.name == "unknown") { txt = "Unknown"; r = 0.85; g = 0.18; b = 0.15; }
      else {
        char sc[16];
        snprintf(sc, sizeof sc, "  %.2f", it->second.score);
        txt = it->second.name;
        std::replace(txt.begin(), txt.end(), '_', ' ');
        txt += sc;
        r = 0.1; g = 0.62; b = 0.33;
      }
    }
  }
  auto &rp = o->rect_params;
  rp.border_width = 3;
  rp.border_color = {r, g, b, 1.0};
  rp.has_bg_color = 0;
  auto &t = o->text_params;
  g_free(t.display_text);
  t.display_text = g_strdup(txt.c_str());
  t.x_offset = (unsigned)rp.left;
  t.y_offset = (unsigned)std::max(0.f, rp.top - 30);
  t.font_params.font_name = (char *)"Sans";
  t.font_params.font_size = 16;
  t.font_params.font_color = {1.0, 1.0, 1.0, 1.0};
  t.set_bg_clr = 1;
  t.text_bg_clr = {r, g, b, 0.9};
}

static GstPadProbeReturn tracker_probe(GstPad *, GstPadProbeInfo *info, gpointer) {
  GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
  NvDsBatchMeta *bm = gst_buffer_get_nvds_batch_meta(buf);
  NvBufSurface *surf = nullptr;
  bool mapped = false;
  GstMapInfo mi;
  for (NvDsMetaList *l = bm->frame_meta_list; l; l = l->next) {
    auto *fm = (NvDsFrameMeta *)l->data;
    n_frames++;
    for (NvDsMetaList *ol = fm->obj_meta_list; ol; ol = ol->next) {
      auto *o = (NvDsObjectMeta *)ol->data;
      n_faces++;
      label(o);
      float kps[10];
      if (o->object_id == UNTRACKED_OBJECT_ID || o->rect_params.width < MIN_FACE_PX || !unpack_kps(o, kps)) continue;
      if (!want_attempt(o->object_id, fm->frame_num)) continue;
      if (!surf) {
        if (!gst_buffer_map(buf, &mi, GST_MAP_READ)) return GST_PAD_PROBE_OK;
        surf = (NvBufSurface *)mi.data;
      }
      int b = fm->batch_id;
      if (!mapped) {
        if (NvBufSurfaceMap(surf, b, 0, NVBUF_MAP_READ) != 0) break;
        NvBufSurfaceSyncForCpu(surf, b, 0);
        mapped = true;
      }
      auto &p = surf->surfaceList[b];
      cv::Mat rgba(p.height, p.width, CV_8UC4, p.mappedAddr.addr[0], p.pitch), face;
      cv::cvtColor(align(rgba, kps), face, cv::COLOR_RGBA2RGB);
      Job j{o->object_id, fm->frame_num,
            {o->rect_params.left, o->rect_params.top, o->rect_params.left + o->rect_params.width,
             o->rect_params.top + o->rect_params.height, o->confidence}, face};
      bool queued = false;
      {
        std::lock_guard<std::mutex> lk(q_mu);
        if (q_jobs.size() < Q_MAX) { q_jobs.push_back(std::move(j)); queued = true; }
      }
      if (queued) q_cv.notify_one();
      else {
        n_dropped++;
        std::lock_guard<std::mutex> lk(st_mu);
        tracks[o->object_id].pending = false;  // let the track try again later
      }
    }
    if (mapped) { NvBufSurfaceUnMap(surf, fm->batch_id, 0); mapped = false; }
  }
  if (surf) gst_buffer_unmap(buf, &mi);
  return GST_PAD_PROBE_OK;
}

static guint frame_step();

static gboolean report(gpointer) {
  static long last = 0;
  static auto t = std::chrono::steady_clock::now();
  auto now = std::chrono::steady_clock::now();
  double dt = std::chrono::duration<double>(now - t).count();
  long f = n_frames;
  double fps = (f - last) / dt;
  fprintf(stderr, "[facepipe] fps %.1f frames %ld faces %ld known %ld unknown %ld dropped %ld\n", fps, f,
          (long)n_faces, (long)n_known, (long)n_unknown, (long)n_dropped);
  last = f; t = now;
  // status.json for the web page (written to a temp file, then renamed so readers never see half a file)
  std::string tmp = DATA_DIR + "/status.json.tmp";
  if (FILE *s = fopen(tmp.c_str(), "w")) {
    fprintf(s, "{\"version\":\"%s\",\"updated\":%ld,\"fps\":%.1f,\"frames\":%ld,\"known\":%ld,\"unknown\":%ld,\"step\":%u}\n",
            FACEPIPE_VERSION, (long)time(nullptr), fps, f, (long)n_known, (long)n_unknown, frame_step());
    fclose(s);
    rename(tmp.c_str(), (DATA_DIR + "/status.json").c_str());
  }
  return TRUE;
}

static void on_pad(GstElement *, GstPad *pad, gpointer mux_sink) {
  GstCaps *caps = gst_pad_get_current_caps(pad);
  const char *name = gst_structure_get_name(gst_caps_get_structure(caps, 0));
  if (g_str_has_prefix(name, "video") && !gst_pad_is_linked((GstPad *)mux_sink) &&
      gst_pad_link(pad, (GstPad *)mux_sink) != GST_PAD_LINK_OK)
    fprintf(stderr, "decoder link failed\n");
  gst_caps_unref(caps);
}

// RTSP over UDP lost packets on the Wi-Fi camera (4-5 fps average vs steady 30 over TCP), so force TCP.
static void on_source_setup(GstElement *, GstElement *src, gpointer) {
  if (g_object_class_find_property(G_OBJECT_GET_CLASS(src), "protocols"))
    g_object_set(src, "protocols", 4 /* GST_RTSP_LOWER_TRANS_TCP */, "latency", 200, nullptr);
}

// FACEPIPE_DROP=N: only every Nth frame from the source enters the pipeline (all source types).
// Default 2 (30 fps camera -> 15 fps processed). 1 = process every frame.
static guint frame_step() {
  static guint v = 0;
  if (!v) {
    const char *n = getenv("FACEPIPE_DROP");
    int i = n ? atoi(n) : 2;
    if (i < 1) { fprintf(stderr, "FACEPIPE_DROP must be >= 1, got '%s'\n", n); exit(1); }
    v = (guint)i;
  }
  return v;
}

static GstPadProbeReturn drop_probe(GstPad *, GstPadProbeInfo *, gpointer) {
  static guint64 n = 0;
  return (n++ % frame_step()) ? GST_PAD_PROBE_DROP : GST_PAD_PROBE_OK;
}

// Sources: rtsp://, file://, http(s):// -> uridecodebin (NVDEC);
//   csi:<sensor-id>       -> nvarguscamerasrc (CSI camera through the ISP), e.g. csi:0
//   usb:<device>          -> v4l2src, e.g. usb:/dev/video0. FACEPIPE_USB_FORMAT=mjpeg (default; decoded on NVJPG via
//                            nvv4l2decoder mjpeg=1) or raw (YUY2, converted by nvvideoconvert).
// Cameras capture at the mux size and FACEPIPE_CAM_FPS (default 30); FACEPIPE_DROP then thins frames as for streams.
static std::string camera_desc(const std::string &uri, int w, int h) {
  std::string caps = "width=" + std::to_string(w) + ",height=" + std::to_string(h) + ",framerate=" +
                     env("FACEPIPE_CAM_FPS", "30") + "/1";
  if (uri.rfind("csi:", 0) == 0)
    return "nvarguscamerasrc sensor-id=" + std::to_string(atoi(uri.c_str() + 4)) +
           " ! capsfilter caps=\"video/x-raw(memory:NVMM),format=NV12," + caps + "\"";
  std::string dev = uri.substr(4);
  if (env("FACEPIPE_USB_FORMAT", "mjpeg") == "raw")
    return "v4l2src device=" + dev + " extra-controls=\"c,exposure_auto_priority=0\" ! video/x-raw,format=YUY2," + caps +
           " ! nvvideoconvert ! capsfilter caps=\"video/x-raw(memory:NVMM),format=NV12\"";
  // exposure_auto_priority=0: UVC webcams otherwise drop their frame rate in dim light (a C270 fell from 30 to 15 fps)
  return "v4l2src device=" + dev + " io-mode=2 extra-controls=\"c,exposure_auto_priority=0\" ! image/jpeg," + caps +
         " ! jpegparse ! nvv4l2decoder mjpeg=1";
}

static gboolean on_bus(GstBus *, GstMessage *m, gpointer loop) {
  if (GST_MESSAGE_TYPE(m) == GST_MESSAGE_EOS) { fprintf(stderr, "EOS\n"); g_main_loop_quit((GMainLoop *)loop); }
  else if (GST_MESSAGE_TYPE(m) == GST_MESSAGE_ERROR) {
    GError *e; gchar *d;
    gst_message_parse_error(m, &e, &d);
    fprintf(stderr, "ERROR %s: %s\n%s\n", GST_OBJECT_NAME(m->src), e->message, d ? d : "");
    g_error_free(e); g_free(d);
    g_main_loop_quit((GMainLoop *)loop);
  }
  return TRUE;
}

static GstElement *make(const char *f, const char *n) {
  GstElement *e = gst_element_factory_make(f, n);
  if (!e) { fprintf(stderr, "cannot create %s\n", f); exit(1); }
  return e;
}

// ---- RTSP: rtsp://<nano>:8554/live, Basic auth from $FACEPIPE_RTSP_AUTH ("user:password", set by web.py
// --set-rtsp-password, mode 0600). Without that file the RTSP server stays off: faces must not stream without a login.
static void start_rtsp() {
  std::string path = env("FACEPIPE_RTSP_AUTH", env("FACEPIPE_DIR", ".") + "/rtsp_auth"), cred;
  std::ifstream f(path);
  if (!std::getline(f, cred) || cred.find(':') == std::string::npos) {
    fprintf(stderr, "[facepipe] RTSP off: no login in %s\n", path.c_str());
    return;
  }
  std::string user = cred.substr(0, cred.find(':')), pass = cred.substr(cred.find(':') + 1);
  GstRTSPServer *srv = gst_rtsp_server_new();
  g_object_set(srv, "service", env("FACEPIPE_RTSP_PORT", "8554").c_str(), nullptr);
  GstRTSPAuth *auth = gst_rtsp_auth_new();
  GstRTSPToken *tok = gst_rtsp_token_new("media.factory.role", G_TYPE_STRING, user.c_str(), nullptr);
  gchar *basic = gst_rtsp_auth_make_basic(user.c_str(), pass.c_str());
  gst_rtsp_auth_add_basic(auth, basic, tok);
  g_free(basic);
  gst_mini_object_unref((GstMiniObject *)tok);
  gst_rtsp_server_set_auth(srv, auth);
  g_object_unref(auth);
  GstRTSPMediaFactory *fac = gst_rtsp_media_factory_new();
  gst_rtsp_media_factory_set_launch(fac, "( udpsrc name=pay0 port=5400 buffer-size=524288 caps=\"application/x-rtp, "
                                         "media=video, clock-rate=90000, encoding-name=H264, payload=96\" )");
  gst_rtsp_media_factory_set_shared(fac, TRUE);
  gst_rtsp_media_factory_add_role(fac, user.c_str(), "media.factory.access", G_TYPE_BOOLEAN, TRUE,
                                  "media.factory.construct", G_TYPE_BOOLEAN, TRUE, nullptr);
  GstRTSPMountPoints *mounts = gst_rtsp_server_get_mount_points(srv);
  gst_rtsp_mount_points_add_factory(mounts, "/live", fac);
  g_object_unref(mounts);
  if (gst_rtsp_server_attach(srv, nullptr) == 0) fprintf(stderr, "[facepipe] RTSP: cannot listen\n");
  else fprintf(stderr, "[facepipe] RTSP on rtsp://<this device>:%s/live (login required)\n", env("FACEPIPE_RTSP_PORT", "8554").c_str());
}

#ifdef FACEPIPE_ENROLL  // separate binary: it needs OpenCV imgcodecs (imread), which the live binary must not load
// ---- enrollment: gallery_dir/<name>/<photo> -> GALLERY (written to a temp file, then renamed: live reload is safe) ----
static int enroll(const std::string &root) {
  Engine det, rec;
  det.load(DET_ENGINE.c_str(), nvinfer1::Dims4{1, 3, NET_H, NET_W});
  rec.load(REC_ENGINE.c_str(), nvinfer1::Dims4{1, 3, 112, 112});
  DIR *d = opendir(root.c_str());
  if (!d) { fprintf(stderr, "cannot open %s\n", root.c_str()); return 1; }
  std::vector<std::string> people;
  while (dirent *e = readdir(d)) if (e->d_name[0] != '.' && e->d_type == DT_DIR) people.push_back(e->d_name);
  closedir(d);
  std::string tmp = GALLERY + ".tmp";
  FILE *out = fopen(tmp.c_str(), "w");
  if (!out) { fprintf(stderr, "cannot write %s\n", tmp.c_str()); return 1; }
  int ok = 0, bad = 0;
  std::sort(people.begin(), people.end());
  for (auto &name : people) {
    if (name.find('\t') != std::string::npos || name.find('\n') != std::string::npos) continue;
    DIR *pd = opendir((root + "/" + name).c_str());
    if (!pd) { fprintf(stderr, "  skip %s: cannot open folder\n", name.c_str()); continue; }
    std::vector<std::string> files;
    while (dirent *e = readdir(pd)) {
      std::string f = e->d_name, lo = f;
      std::transform(lo.begin(), lo.end(), lo.begin(), ::tolower);
      if (lo.size() > 4 && (lo.rfind(".jpg") == lo.size() - 4 || lo.rfind(".png") == lo.size() - 4 || lo.rfind(".jpeg") == lo.size() - 5))
        files.push_back(f);
    }
    closedir(pd);
    std::sort(files.begin(), files.end());
    for (auto &f : files) {
      std::string path = root + "/" + name + "/" + f;
      cv::Mat bgr = cv::imread(path, cv::IMREAD_COLOR), rgb, lb(NET_H, NET_W, CV_8UC3, cv::Scalar(0, 0, 0));
      if (bgr.empty()) { fprintf(stderr, "  skip %s: unreadable\n", path.c_str()); bad++; continue; }
      cv::cvtColor(bgr, rgb, cv::COLOR_BGR2RGB);
      float sc = std::min((float)NET_W / rgb.cols, (float)NET_H / rgb.rows);  // letterbox, pad right/bottom like nvinfer
      cv::Mat rs;
      cv::resize(rgb, rs, cv::Size(std::max(1, (int)(rgb.cols * sc)), std::max(1, (int)(rgb.rows * sc))));
      rs.copyTo(lb(cv::Rect(0, 0, rs.cols, rs.rows)));
      float *in = det.host[0].data();
      for (int y = 0; y < NET_H; y++)
        for (int x = 0; x < NET_W; x++)
          for (int c = 0; c < 3; c++) in[c * NET_H * NET_W + y * NET_W + x] = (lb.at<cv::Vec3b>(y, x)[c] - 127.5f) / 128.f;
      det.run();
      auto faces = decode_scrfd([&det](const char *n) { return det.out(n); }, 1.f / sc);
      if (faces.empty()) { fprintf(stderr, "  skip %s: no face\n", path.c_str()); bad++; continue; }
      auto big = std::max_element(faces.begin(), faces.end(), [](const Face &a, const Face &b) {
        return (a.x2 - a.x1) * (a.y2 - a.y1) < (b.x2 - b.x1) * (b.y2 - b.y1); });
      auto e = embed(rec, align(rgb, big->kps));
      fprintf(out, "%s\t", name.c_str());
      for (int i = 0; i < 512; i++) fprintf(out, i ? ",%.6f" : "%.6f", e[i]);
      fprintf(out, "\n");
      fprintf(stderr, "  %s / %s: face %.0fx%.0f det %.2f%s\n", name.c_str(), f.c_str(), big->x2 - big->x1, big->y2 - big->y1,
              big->score, faces.size() > 1 ? " (largest of several)" : "");
      ok++;
    }
  }
  fclose(out);
  if (rename(tmp.c_str(), GALLERY.c_str()) != 0) { fprintf(stderr, "cannot replace %s\n", GALLERY.c_str()); return 1; }
  fprintf(stderr, "enrolled %d photos of %zu people into %s (%d skipped)\n", ok, people.size(), GALLERY.c_str(), bad);
  return ok ? 0 : 1;
}
#endif

int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--version")) { printf("%s\n", FACEPIPE_VERSION); return 0; }
  std::string app_dir = env("FACEPIPE_DIR", ".");
  DATA_DIR = env("FACEPIPE_DATA", app_dir + "/data");
  REC_ENGINE = app_dir + "/models/r50_b16.plan";
  DET_ENGINE = app_dir + "/models/scrfd_384x640_b1.plan";
  GALLERY = env("FACEPIPE_GALLERY", DATA_DIR + "/gallery.tsv");
  REC_DIR = env("FACEPIPE_REC", "/mnt/rec");
  MATCH = std::stof(env("FACEPIPE_MATCH", "0.5"));
  mkdir(DATA_DIR.c_str(), 0755);
#ifdef FACEPIPE_ENROLL
  if (argc == 2) return enroll(argv[1]);
  fprintf(stderr, "usage: %s <gallery_dir>\n", argv[0]);
  return 1;
#else
  if (argc < 2) {
    fprintf(stderr, "usage: %s <rtsp://...|file://...|csi:0|usb:/dev/video0> [width height]\n"
                    "enroll photos with facepipe-enroll <gallery_dir>\n", argv[0]);
    return 1;
  }
#endif
  if (argc >= 4) { mux_w = atoi(argv[2]); mux_h = atoi(argv[3]); }
  json_out = fdopen(dup(1), "w");
  dup2(2, 1);
  std::string dir = env("FACEPIPE_DIR", ".");
  std::string uri = argv[1];
  bool camera = uri.rfind("csi:", 0) == 0 || uri.rfind("usb:", 0) == 0;
  bool live = camera || uri.rfind("rtsp://", 0) == 0;
  fprintf(stderr, "[facepipe] version %s, processing every %u frame(s), match >= %.2f, data %s\n", FACEPIPE_VERSION,
          frame_step(), MATCH, DATA_DIR.c_str());
  gst_init(&argc, &argv);
  std::thread worker(rec_worker);

  GMainLoop *loop = g_main_loop_new(nullptr, FALSE);
  GstElement *pipe = gst_pipeline_new("facepipe");
  GstElement *src;
  if (camera) {
    std::string d = camera_desc(uri, mux_w, mux_h);
    fprintf(stderr, "[facepipe] camera: %s\n", d.c_str());
    GError *err = nullptr;
    src = gst_parse_bin_from_description(d.c_str(), TRUE, &err);
    if (!src) { fprintf(stderr, "camera source: %s\n", err ? err->message : "?"); return 1; }
  } else {
    src = make("uridecodebin", "src");
    g_object_set(src, "uri", uri.c_str(), nullptr);
    g_signal_connect(src, "source-setup", G_CALLBACK(on_source_setup), nullptr);
  }
  GstElement *mux = make("nvstreammux", "mux"),
             *conv = make("nvvideoconvert", "conv"), *caps = make("capsfilter", "caps"),
             *pgie = make("nvinfer", "pgie"), *trk = make("nvtracker", "trk"), *osd = make("nvdsosd", "osd"),
             *t = make("tee", "t"),
             // NVJPG branch: annotated JPEG for the live page and event evidence
             *qj = make("queue", "qj"), *cj = make("nvvideoconvert", "cj"), *fj = make("capsfilter", "fj"),
             *jenc = make("nvjpegenc", "jenc"), *sj = make("appsink", "sj"),
             // NVENC branch: annotated H.264 for RTSP and event clips
             *qh = make("queue", "qh"), *ch = make("nvvideoconvert", "ch"), *fh = make("capsfilter", "fh"),
             *henc = make("nvv4l2h264enc", "henc"), *hp = make("h264parse", "hp"), *fh2 = make("capsfilter", "fh2"),
             *te = make("tee", "te"), *qr = make("queue", "qr"), *pay = make("rtph264pay", "pay"), *us = make("udpsink", "us"),
             *qa = make("queue", "qa"), *sh = make("appsink", "sh");
  auto set_caps = [](GstElement *f, const char *s) {
    GstCaps *c = gst_caps_from_string(s);
    g_object_set(f, "caps", c, nullptr);
    gst_caps_unref(c);
  };
  g_object_set(qj, "leaky", 2, "max-size-buffers", 2, nullptr);  // the JPEG branch may drop frames, never stall
  // nvjpegenc (JetPack 4.6) crashes on DeepStream NvBufSurface buffers, so hand it I420 in system memory
  set_caps(fj, "video/x-raw, format=I420");
  g_object_set(jenc, "quality", 85, nullptr);
  g_object_set(sj, "emit-signals", TRUE, "sync", FALSE, "max-buffers", 2, "drop", TRUE, nullptr);
  g_signal_connect(sj, "new-sample", G_CALLBACK(on_jpeg), nullptr);
  g_object_set(qh, "leaky", 2, "max-size-buffers", 4, nullptr);
  set_caps(fh, "video/x-raw(memory:NVMM), format=NV12");
  g_object_set(henc, "bitrate", 4000000, "iframeinterval", 15, "insert-sps-pps", TRUE, "bufapi-version", TRUE, nullptr);
  g_object_set(hp, "config-interval", -1, nullptr);
  set_caps(fh2, "video/x-h264, stream-format=byte-stream, alignment=au");
  g_object_set(pay, "config-interval", 1, "pt", 96, nullptr);
  g_object_set(us, "host", "127.0.0.1", "port", 5400, "sync", FALSE, "async", FALSE, nullptr);
  g_object_set(sh, "emit-signals", TRUE, "sync", FALSE, "max-buffers", 60, "drop", TRUE, nullptr);
  g_signal_connect(sh, "new-sample", G_CALLBACK(on_h264), nullptr);
  mkdir(LIVE_DIR.c_str(), 0755);
  fprintf(stderr, "[facepipe] recordings %s: %s\n", REC_DIR.c_str(), rec_ok() ? "on" : "off (not mounted or not writable)");
  g_object_set(mux, "batch-size", 1, "width", mux_w, "height", mux_h, "live-source", live,
               "batched-push-timeout", 40000, nullptr);
  GstCaps *rgba = gst_caps_from_string("video/x-raw(memory:NVMM), format=RGBA");
  g_object_set(caps, "caps", rgba, nullptr);
  gst_caps_unref(rgba);
  g_object_set(pgie, "config-file-path", (dir + "/pgie_scrfd.txt").c_str(), nullptr);
  g_object_set(trk, "ll-lib-file", "/opt/nvidia/deepstream/deepstream-6.0/lib/libnvds_nvmultiobjecttracker.so",
               "ll-config-file", (dir + "/tracker.yml").c_str(), "tracker-width", 640, "tracker-height", 384, nullptr);
  gst_bin_add_many(GST_BIN(pipe), src, mux, conv, caps, pgie, trk, osd, t, qj, cj, fj, jenc, sj, qh, ch, fh, henc, hp, fh2,
                   te, qr, pay, us, qa, sh, nullptr);
  GstPad *msink = gst_element_get_request_pad(mux, "sink_0");
  gst_pad_add_probe(msink, GST_PAD_PROBE_TYPE_BUFFER, drop_probe, nullptr, nullptr);
  if (camera) {
    GstPad *sp = gst_element_get_static_pad(src, "src");
    if (gst_pad_link(sp, msink) != GST_PAD_LINK_OK) { fprintf(stderr, "camera link failed\n"); return 1; }
    gst_object_unref(sp);
  } else {
    g_signal_connect(src, "pad-added", G_CALLBACK(on_pad), msink);
  }
  if (!gst_element_link_many(mux, conv, caps, pgie, trk, osd, t, nullptr) ||
      !gst_element_link_many(t, qj, cj, fj, jenc, sj, nullptr) ||
      !gst_element_link_many(t, qh, ch, fh, henc, hp, fh2, te, nullptr) ||
      !gst_element_link_many(te, qr, pay, us, nullptr) || !gst_element_link_many(te, qa, sh, nullptr)) {
    fprintf(stderr, "link failed\n");
    return 1;
  }
  start_rtsp();

  GstPad *p = gst_element_get_static_pad(pgie, "src");
  gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, pgie_probe, nullptr, nullptr);
  gst_object_unref(p);
  p = gst_element_get_static_pad(trk, "src");
  gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, tracker_probe, nullptr, nullptr);
  gst_object_unref(p);

  GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(pipe));
  gst_bus_add_watch(bus, on_bus, loop);
  gst_object_unref(bus);
  g_timeout_add_seconds(5, report, nullptr);
  gst_element_set_state(pipe, GST_STATE_PLAYING);
  g_main_loop_run(loop);
  gst_element_set_state(pipe, GST_STATE_NULL);
  report(nullptr);

  { std::lock_guard<std::mutex> lk(q_mu); q_stop = true; }
  q_cv.notify_all();
  worker.join();
  gst_object_unref(pipe);
  g_main_loop_unref(loop);
  return 0;  // stream ended or failed; systemd (Restart=always) reconnects a live camera
}
