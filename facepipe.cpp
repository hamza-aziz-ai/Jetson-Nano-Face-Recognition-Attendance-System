// Standalone face attendance for the Jetson Nano (DeepStream 6.0.1, TensorRT 8.2, JetPack 4.6.6). No server needed.
// Live:   uridecodebin -> nvstreammux -> nvvideoconvert(RGBA)
//           -> [probe: SCRFD-10G 640x384 on chosen frames (VIC letterbox + own TensorRT engine), decode + NMS,
//               adds face objects, landmarks packed in misc_obj_info; every frame while faces are around, every
//               FACEPIPE_IDLE_STEP-th after FACEPIPE_IDLE_AFTER s without one]
//           -> nvtracker -> [probe: per track, Umeyama align 112x112, queue] -> fakesink.
//         A worker runs w600k_r50, matches the gallery (cosine, like CCTV COSINE_THRESHOLD) and appends an event to
//         $FACEPIPE_DATA/events-YYYY-MM-DD.jsonl with a face snapshot, at most once per person per 5 minutes (D-038).
//         Unknown tracks are retried a few times.
// Video:  tracker -> nvdsosd (boxes + names) -> tee: NVJPG 1080p JPEG (live web view, event evidence image) and
//         NVENC H.264 (RTSP rtsp://<nano>:8554/live with login; event clips 3 s before to 5 s after, in $FACEPIPE_REC).
// Sources: rtsp:// file:// http:// (NVDEC), csi:<sensor-id> (Argus/ISP), usb:/dev/videoN (MJPEG on NVJPG, or raw).
// Enroll: facepipe-enroll <gallery_dir>  (same source, -DFACEPIPE_ENROLL; gallery_dir/<person name>/*.jpg|png)
//         -> $FACEPIPE_GALLERY, reloaded live.
// Preprocessing and alignment match InsightFace-REST (SCRFD (x-127.5)/128 RGB, ArcFace x/127.5-1 RGB,
// ArcFace 5-point template, det 0.6, NMS 0.4) so embeddings compare with the CCTV gallery too.
// Usage: facepipe <source> [width height]   e.g. facepipe rtsp://cam/stream 1920 1080 | facepipe csi:0 1920 1080
// Env: FACEPIPE_DIR (app dir with configs and models/, default .), FACEPIPE_DATA (default $FACEPIPE_DIR/data),
//      FACEPIPE_GALLERY (default $FACEPIPE_DATA/gallery.tsv), FACEPIPE_METRIC (cosine or l2, default cosine),
//      FACEPIPE_MATCH (cosine threshold, default 0.5), FACEPIPE_L2_MATCH (L2 distance threshold, default 0.95),
//      FACEPIPE_DROP=N (process every Nth decoded frame, default 2; 1 = all frames), FACEPIPE_REC (default /mnt/rec),
//      FACEPIPE_RTSP_AUTH (default $FACEPIPE_DIR/rtsp_auth), FACEPIPE_RTSP_PORT (default 8554),
//      FACEPIPE_WATCHDOG (seconds without frames before exiting, default 60, 0 = off),
//      FACEPIPE_LOGS (log folder, default $FACEPIPE_DIR/logs), FACEPIPE_IDLE_AFTER (seconds without a face before
//      detecting less often, default 10, 0 = always every frame), FACEPIPE_IDLE_STEP (detect every Nth frame while
//      idle, default 6).
// stdout: one JSON line per recognition attempt (with the embedding); logs: $FACEPIPE_LOGS/<year>/<Month>/facepipe.log
// (enroll.log for facepipe-enroll), also on stderr for the journal.

#include <gst/gst.h>
#include <glib-unix.h>
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
#include "nvbufsurftransform.h"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cstdarg>
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
#include <sys/time.h>
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

// ---- logs (owner's layout): $FACEPIPE_LOGS/<year>/<Month>/<module>.log, default $FACEPIPE_DIR/logs. With the first line
// after local midnight the day's file becomes <module>.log.DD-Mon-YYYY in its own month folder, so a month folder holds
// at most 31 backups per module. Line: [DD-MM-YYYY] hh:mm:ss.fff AM/PM, Weekday [LEVEL] message.
// Every line also goes to stderr (the journal). Prints from libraries (GStreamer, DeepStream) reach only the journal.
static const char *MONTHS[] = {"January", "February", "March",     "April",   "May",      "June",
                               "July",    "August",   "September", "October", "November", "December"};
static const char *WEEKDAYS[] = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
static std::string LOG_ROOT, LOG_MODULE = "facepipe";  // LOG_ROOT empty: stderr only (self-test, before setup)

static std::string log_stamp(const struct tm &t, int ms) {
  char b[64];
  int h = t.tm_hour % 12 ? t.tm_hour % 12 : 12;
  snprintf(b, sizeof b, "[%02d-%02d-%04d] %02d:%02d:%02d.%03d %s, %s", t.tm_mday, t.tm_mon + 1, t.tm_year + 1900, h,
           t.tm_min, t.tm_sec, ms, t.tm_hour < 12 ? "AM" : "PM", WEEKDAYS[t.tm_wday]);
  return b;
}
static int ymd(const struct tm &t) { return (t.tm_year + 1900) * 10000 + (t.tm_mon + 1) * 100 + t.tm_mday; }
static std::string log_path(const struct tm &t) {
  return LOG_ROOT + "/" + std::to_string(t.tm_year + 1900) + "/" + MONTHS[t.tm_mon] + "/" + LOG_MODULE + ".log";
}
static std::string log_backup_name(const std::string &path, const struct tm &day) {
  char b[24];
  snprintf(b, sizeof b, ".%02d-%.3s-%04d", day.tm_mday, MONTHS[day.tm_mon], day.tm_year + 1900);
  return path + b;
}
// path becomes its dated backup; appended to it if that day's backup already exists (a restart around midnight)
static void log_backup(const std::string &path, const struct tm &day) {
  std::string dst = log_backup_name(path, day);
  if (access(dst.c_str(), F_OK) != 0) { rename(path.c_str(), dst.c_str()); return; }
  FILE *in = fopen(path.c_str(), "rb"), *out = fopen(dst.c_str(), "ab");
  if (in && out) {
    char b[65536];
    size_t n;
    while ((n = fread(b, 1, sizeof b, in)) > 0) fwrite(b, 1, n, out);
  }
  if (in) fclose(in);
  if (out) fclose(out);
  if (in && out) unlink(path.c_str());
}
static std::mutex log_mu;
static FILE *log_f = nullptr;
static int log_day = 0;
static struct tm log_tm;  // the day of the open file

static void log_open(const struct tm &now) {
  // a file from an earlier day, left by a run that ended before midnight, in this or the previous month's folder
  for (int back = 0; back < 2; back++) {
    struct tm m = now;
    m.tm_mday = 1; m.tm_mon -= back; m.tm_isdst = -1;
    mktime(&m);
    std::string p = log_path(m);
    struct stat st;
    struct tm ft;
    if (stat(p.c_str(), &st) == 0 && localtime_r(&st.st_mtime, &ft) && ymd(ft) != ymd(now)) log_backup(p, ft);
  }
  std::string y = LOG_ROOT + "/" + std::to_string(now.tm_year + 1900);
  mkdir(LOG_ROOT.c_str(), 0755);
  mkdir(y.c_str(), 0755);
  mkdir((y + "/" + MONTHS[now.tm_mon]).c_str(), 0755);
  log_f = fopen(log_path(now).c_str(), "a");
  log_day = ymd(now);
  log_tm = now;
}

static void log_line(const char *level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void log_line(const char *level, const char *fmt, ...) {
  char msg[2048];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(msg, sizeof msg, fmt, ap);
  va_end(ap);
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  struct tm t;
  localtime_r(&tv.tv_sec, &t);
  std::lock_guard<std::mutex> lk(log_mu);
  fprintf(stderr, "[%s] %s\n", level, msg);
  if (LOG_ROOT.empty()) return;
  if (ymd(t) != log_day) {
    if (log_f) { fclose(log_f); log_f = nullptr; log_backup(log_path(log_tm), log_tm); }
    log_open(t);
  }
  if (log_f) { fprintf(log_f, "%s [%s] %s\n", log_stamp(t, (int)(tv.tv_usec / 1000)).c_str(), level, msg); fflush(log_f); }
}
#define LOGI(...) log_line("INFO", __VA_ARGS__)
#define LOGW(...) log_line("WARNING", __VA_ARGS__)
#define LOGE(...) log_line("ERROR", __VA_ARGS__)

// ---- shutdown (SIGTERM/SIGINT, end of stream or error): one path, bounded by STOP_LIMIT (enforced by the watchdog) ----
static const int STOP_LIMIT = 10;
static std::atomic<bool> stopping{false};  // set first: no new clips or evidence waits from then on
static std::atomic<long> stop_at{0};       // steady-clock second when the shutdown began
static long steady_s() {
  return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static void begin_stop(const char *why) {
  if (stopping.exchange(true)) return;
  stop_at = steady_s();
  LOGI("stopping (%s): releasing the camera, finishing recognition and recordings, at most %d s", why, STOP_LIMIT);
}
static std::string DATA_DIR, GALLERY, REC_DIR;
static const std::string LIVE_DIR = "/dev/shm/facepipe";  // live.jpg for the web page's MJPEG view (RAM, no SSD wear)
static float MATCH = 0.5f;      // cosine threshold (FACEPIPE_MATCH), used when the metric is cosine
static float L2_MATCH = 0.95f;  // L2 distance threshold (FACEPIPE_L2_MATCH), used when the metric is l2
static bool METRIC_L2 = false;  // FACEPIPE_METRIC=cosine (default) or l2
// Embeddings are L2-normalised, so L2 distance = sqrt(2 - 2 cosine): cosine 0.5 is L2 1.0, L2 0.95 is cosine 0.549.
// The best candidate is the same for both metrics; only the threshold rule differs.
static float l2_of(float cosine) { return std::sqrt(std::max(0.f, 2.f - 2.f * cosine)); }
static bool passes(float cosine) { return METRIC_L2 ? l2_of(cosine) <= L2_MATCH : cosine >= MATCH; }

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
  void log(Severity s, const char *msg) noexcept override { if (s <= Severity::kWARNING) LOGW("TensorRT: %s", msg); }
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
    if (!eng) { LOGE("cannot load %s", path); exit(1); }
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
  bool run() {
    bool ok = cudaMemcpy(dev[0], host[0].data(), host[0].size() * sizeof(float), cudaMemcpyHostToDevice) == cudaSuccess &&
              ctx->executeV2(dev.data());
    for (size_t i = 1; ok && i < dev.size(); i++)
      ok = cudaMemcpy(host[i].data(), dev[i], host[i].size() * sizeof(float), cudaMemcpyDeviceToHost) == cudaSuccess;
    return ok;
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
  // identity of the loaded file: enrollment publishes by rename (new inode); second-resolution mtime missed quick updates
  ino_t ino = 0;
  long long mtime_ns = -1;
  off_t size = -1;
  bool reload_if_changed() {  // true when a new gallery was loaded
    struct stat st;
    if (stat(GALLERY.c_str(), &st) != 0) return false;
    long long ns = (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
    if (st.st_ino == ino && ns == mtime_ns && st.st_size == size) return false;
    ino = st.st_ino; mtime_ns = ns; size = st.st_size;
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
    LOGI("gallery %s: %zu photos", GALLERY.c_str(), names.size());
    return true;
  }
  std::pair<std::string, float> match(const std::vector<float> &e) const {
    float best = -1;
    size_t bi = 0;
    for (size_t i = 0; i < embs.size(); i++) {
      float s = 0;
      for (int k = 0; k < 512; k++) s += e[k] * embs[i][k];
      if (s > best) { best = s; bi = i; }
    }
    return {!embs.empty() && passes(best) ? names[bi] : "unknown", best};
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
static GstClockTime jpeg_pts = GST_CLOCK_TIME_NONE;  // timestamp of that frame

static GstFlowReturn on_jpeg(GstAppSink *s, gpointer) {
  GstSample *smp = gst_app_sink_pull_sample(s);
  if (!smp) return GST_FLOW_OK;
  GstMapInfo m;
  GstBuffer *b = gst_sample_get_buffer(smp);
  if (gst_buffer_map(b, &m, GST_MAP_READ)) {
    {
      std::lock_guard<std::mutex> lk(j_mu);
      jpeg.assign(m.data, m.data + m.size);
      jpeg_pts = GST_BUFFER_PTS(b);
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

// first annotated JPEG of a frame labelled after `after` (so the decided name is drawn), or empty after 1.5 s
static std::vector<unsigned char> jpeg_after(GstClockTime after) {
  std::unique_lock<std::mutex> lk(j_mu);
  auto ready = [after] { return GST_CLOCK_TIME_IS_VALID(jpeg_pts) && jpeg_pts > after; };
  j_cv.wait_for(lk, std::chrono::milliseconds(1500), ready);
  return ready() ? jpeg : std::vector<unsigned char>();
}

static const GstClockTime CLIP_PRE = 3 * GST_SECOND, CLIP_POST = 5 * GST_SECOND;
struct AU { GstBuffer *buf; GstClockTime pts; bool key; };
static std::mutex v_mu;
static std::deque<AU> ring;          // last ~CLIP_PRE s of H.264, starting at a keyframe
static GstCaps *h264_caps = nullptr;
static GstClockTime last_pts = 0;
struct Clip { GstElement *pipe = nullptr, *src = nullptr; GstClockTime base = 0, until = 0; std::string rel; };
static Clip clip;
static std::string clip_pending;  // a clip asked for before the encoder had a keyframe; opened at the first one (v_mu)
static bool clip_open(const std::string &rel);

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
    if (d < cut && d != today) { rm_day(REC_DIR + "/" + d); LOGW("storage full: deleted %s", d.c_str()); }
}

static void clip_push(GstBuffer *b, GstClockTime pts) {  // v_mu held
  GstBuffer *c = gst_buffer_copy(b);  // shares the memory, own timestamps
  GST_BUFFER_PTS(c) = GST_BUFFER_DTS(c) = pts - clip.base;
  GST_BUFFER_DURATION(c) = GST_CLOCK_TIME_NONE;
  gst_app_src_push_buffer(GST_APP_SRC(clip.src), c);
}

static std::atomic<int> clips_finalizing{0};  // clips whose MP4 index is still being written (shutdown waits for them)

static void clip_finish() {  // v_mu held; mp4mux writes the index on EOS, so wait for it off the streaming thread
  GstElement *p = clip.pipe, *s = clip.src;
  std::string rel = clip.rel;
  clip = Clip();
  clips_finalizing++;
  gst_app_src_end_of_stream(GST_APP_SRC(s));
  std::thread([p, rel] {
    GstBus *bus = gst_element_get_bus(p);
    // 8 s: inside the shutdown budget, so a stop still reports how the clip ended
    GstMessage *m = gst_bus_timed_pop_filtered(bus, 8 * GST_SECOND, (GstMessageType)(GST_MESSAGE_EOS | GST_MESSAGE_ERROR));
    if (!m) LOGE("clip %s: not finalized within 8 s, the file may be empty", rel.c_str());
    else if (GST_MESSAGE_TYPE(m) == GST_MESSAGE_ERROR) {
      GError *e;
      gchar *d;
      gst_message_parse_error(m, &e, &d);
      LOGE("clip %s: %s: %s (%s)", rel.c_str(), GST_OBJECT_NAME(m->src), e->message, d ? d : "");
      g_error_free(e);
      g_free(d);
    } else {
      struct stat st;
      LOGI("clip %s: saved, %lld bytes", rel.c_str(), stat((REC_DIR + "/" + rel).c_str(), &st) == 0 ? (long long)st.st_size : -1LL);
    }
    if (m) gst_message_unref(m);
    gst_object_unref(bus);
    gst_element_set_state(p, GST_STATE_NULL);
    gst_object_unref(p);
    rotate_storage();
    clips_finalizing--;
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
    } else if (!clip_pending.empty() && key) {  // an event came before the first keyframe: start its clip now
      while (!ring.empty() && !ring.front().key) { gst_buffer_unref(ring.front().buf); ring.pop_front(); }
      std::string rel;
      rel.swap(clip_pending);
      clip_open(rel);
    }
  }
  gst_sample_unref(smp);
  return GST_FLOW_OK;
}

// start (or extend) the event clip; returns its path relative to REC_DIR, or "" if recording is unavailable
// opens the MP4 writer for clip `rel`, starting with the buffered pre-roll (v_mu held; the ring starts at a keyframe)
static bool clip_open(const std::string &rel) {
  GError *err = nullptr;
  // A plain file writer: the pre-roll arrives as one burst with past timestamps, so nothing here needs a clock.
  GstElement *p = gst_parse_launch(("appsrc name=s is-live=false format=time max-bytes=20000000 ! h264parse ! "
                                    "mp4mux faststart=true ! filesink sync=false async=false location=" + REC_DIR + "/" +
                                    rel).c_str(), &err);
  if (!p) { LOGE("clip pipeline: %s", err ? err->message : "?"); if (err) g_error_free(err); return false; }
  clip.pipe = p;
  clip.src = gst_bin_get_by_name(GST_BIN(p), "s");
  gst_object_unref(clip.src);  // the pipeline keeps it alive
  g_object_set(clip.src, "caps", h264_caps, nullptr);
  gst_element_set_state(p, GST_STATE_PLAYING);
  clip.base = ring.front().pts;
  clip.until = last_pts + CLIP_POST;
  clip.rel = rel;
  for (auto &a : ring) clip_push(a.buf, a.pts);
  LOGI("clip %s: started with %.1f s before the event (%zu frames buffered)", rel.c_str(),
       (double)(last_pts - clip.base) / GST_SECOND, ring.size());
  return true;
}

// start (or extend) the event clip; returns its path relative to REC_DIR, or "" if recording is unavailable.
// Right after a (re)start the encoder needs a few seconds before its first frame; a person recognized then (seen when the
// owner was recognized 3 s after switching cameras) gets a clip that starts at the first keyframe, without pre-roll.
static std::string clip_trigger() {
  if (stopping || !rec_ok()) return "";
  std::lock_guard<std::mutex> lk(v_mu);
  while (!ring.empty() && !ring.front().key) { gst_buffer_unref(ring.front().buf); ring.pop_front(); }
  if (clip.pipe) { clip.until = last_pts + CLIP_POST; return clip.rel; }
  if (!clip_pending.empty()) return clip_pending;
  std::string day, hms;
  stamp(day, hms);
  mkdir((REC_DIR + "/" + day).c_str(), 0755);
  std::string rel = day + "/" + hms + "_clip.mp4";
  if (!h264_caps || ring.empty()) {
    clip_pending = rel;
    LOGI("clip %s: waiting for the video encoder's first keyframe", rel.c_str());
    return rel;
  }
  return clip_open(rel) ? rel : "";
}

static std::string save_evidence(guint64 track, GstClockTime after) {
  if (stopping || !rec_ok()) return "";
  auto j = jpeg_after(after);
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
  if (!f) { LOGE("cannot write events in %s", DATA_DIR.c_str()); return; }
  // score is always the best cosine; metric and l2 record which rule decided and the matching L2 distance
  fprintf(f, "{\"time\":\"%s\",\"ts\":%.3f,\"track\":%llu,\"name\":%s,\"score\":%.3f,\"metric\":\"%s\",\"l2\":%.3f,\"det\":%.3f,"
             "\"snap\":%s,\"frame\":%s,\"clip\":%s}\n",
          iso, std::chrono::duration<double>(now.time_since_epoch()).count(), (unsigned long long)track,
          json_str(name).c_str(), score, METRIC_L2 ? "l2" : "cosine", l2_of(score), det, json_str(snap).c_str(),
          json_str(frame).c_str(), json_str(clip_rel).c_str());
  fclose(f);
}

// ---- repeat rule (D-038), the CCTV system's SAME_CAM_TIME_ENTRY_THRESHOLD: a person logged in the last REPEAT_S is
// not logged again (no event, snapshot, frame or clip), so someone who stays in view is logged once every REPEAT_S, not
// once per tracker id. Unknown faces have no name: one repeats an unknown logged in the window whose face it would match
// under the active metric. Seeded from today's events file at start, so a restart or camera reconnect does not log
// everyone again (unknowns are not: the file keeps no embeddings). Worker thread only.
static const double REPEAT_S = 300;
struct Logged { std::string name; double ts; std::vector<float> emb; };
static std::deque<Logged> logged;
static bool repeat(const std::string &name, const std::vector<float> &e, double now) {
  while (!logged.empty() && now - logged.front().ts >= REPEAT_S) logged.pop_front();
  for (auto &r : logged) {
    if (r.name != name) continue;
    if (name != "unknown") return true;
    float s = 0;
    for (int k = 0; k < 512; k++) s += e[k] * r.emb[k];
    if (passes(s)) return true;
  }
  logged.push_back({name, now, name == "unknown" ? e : std::vector<float>()});
  return false;
}
static void seed_repeats() {
  std::string day, hms;
  stamp(day, hms);
  std::ifstream f(DATA_DIR + "/events-" + day + ".jsonl");
  std::string line;
  while (std::getline(f, line)) {
    size_t t = line.find("\"ts\":"), n = line.find("\"name\":\"");
    if (t == std::string::npos || n == std::string::npos) continue;
    std::string name = line.substr(n + 8, line.find('"', n + 8) - n - 8);
    if (name != "unknown") logged.push_back({name, std::atof(line.c_str() + t + 5), {}});
  }
}

// ---- per-track state, shared by the tracker probe (producer) and the worker ----
static GstClockTime last_label_pts = 0;  // PTS of the last frame labelled (guarded by st_mu)
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
static std::atomic<long> n_frames{0}, n_faces{0}, n_known{0}, n_unknown{0}, n_dropped{0}, n_repeats{0};

// Watchdog thread, always running. (1) No frame for FACEPIPE_WATCHDOG seconds (default 60, 0 = off): exit 3 so systemd
// restarts us. A thread, not a GLib timer: the CSI stall of 2026-11-06 happened before the main loop ran, and a stalled
// process otherwise looks "active" to systemd forever. (2) A shutdown not finished within STOP_LIMIT: exit 4, even with
// the frame rule off.
static void start_watchdog() {
  int limit = std::atoi(env("FACEPIPE_WATCHDOG", "60").c_str());
  std::thread([limit] {
    long last = n_frames, since = steady_s();  // counted from start: the first exit is at `limit` s, not 5 s later
    while (true) {
      std::this_thread::sleep_for(std::chrono::seconds(1));
      long now = steady_s();
      if (stopping) {
        if (now - stop_at >= STOP_LIMIT) {
          LOGE("shutdown did not finish within %d s, forcing exit", STOP_LIMIT);
          _exit(4);
        }
        continue;
      }
      long f = n_frames;
      if (f != last) { last = f; since = now; }
      else if (limit > 0 && now - since >= limit) {
        LOGE("watchdog: no frames for %d s, exiting so the service restarts", limit);
        _exit(3);
      }
    }
  }).detach();
}

static void rec_worker() {
  Engine rec;
  rec.load(REC_ENGINE.c_str(), nvinfer1::Dims4{1, 3, 112, 112});
  Gallery gal;
  seed_repeats();
  while (true) {
    Job j;
    {
      std::unique_lock<std::mutex> lk(q_mu);
      q_cv.wait(lk, [] { return q_stop || !q_jobs.empty(); });
      if (q_jobs.empty()) break;
      j = std::move(q_jobs.front());
      q_jobs.pop_front();
    }
    if (gal.reload_if_changed()) {  // people added, updated or removed: names cached on live tracks may be stale
      std::lock_guard<std::mutex> lk(st_mu);
      tracks.clear();
    }
    auto t0 = std::chrono::steady_clock::now();
    auto e = embed(rec, j.face);
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    auto m = gal.match(e);
    bool known = m.first != "unknown";
    int tries;
    GstClockTime decided_after;  // frames labelled up to this timestamp may still show the old label
    {
      std::lock_guard<std::mutex> lk(st_mu);
      decided_after = last_label_pts;
      auto &s = tracks[j.track];
      s.pending = false;
      tries = s.tries;
      if (known || s.tries >= MAX_TRIES) { s.done = true; s.name = m.first; s.score = m.second; }  // label shows from now
    }
    double now = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    if ((known || tries >= MAX_TRIES) && repeat(m.first, e, now)) n_repeats++;
    else if (known || tries >= MAX_TRIES) {
      std::string c = clip_trigger();       // clip starts CLIP_PRE before now
      std::string fr = save_evidence(j.track, decided_after);  // first frame labelled after the decision
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

// ---- detection (D-028): SCRFD in a probe before the tracker instead of nvinfer, whose `interval` cannot change while
// playing. Every frame while faces are around; after IDLE_AFTER s without one, every IDLE_STEP-th frame. Video buffers
// always pass, only inference is skipped; a frame without inference keeps bInferDone=FALSE and the tracker only tracks it.
static int IDLE_AFTER = 10, IDLE_STEP = 6;
static Engine live_det;
static NvBufSurface *det_in = nullptr;  // 640x384 RGBA letterbox target; its padding is zeroed once and never written
static long last_face_s = 0;            // steady second of the last frame with a face (streaming thread only)
static std::atomic<long> n_detects{0};

// the policy, separate so the self-test can check it: start active; idle after IDLE_AFTER s without a face
static bool detect_due(long now, long last_face, unsigned long long &idle_n) {
  if (IDLE_AFTER <= 0 || now - last_face < IDLE_AFTER) { idle_n = 0; return true; }
  return idle_n++ % IDLE_STEP == 0;
}

static bool det_setup() {
  live_det.load(DET_ENGINE.c_str(), nvinfer1::Dims4{1, 3, NET_H, NET_W});
  NvBufSurfaceCreateParams cp = {};
  cp.gpuId = 0;
  cp.width = NET_W;
  cp.height = NET_H;
  cp.colorFormat = NVBUF_COLOR_FORMAT_RGBA;
  cp.layout = NVBUF_LAYOUT_PITCH;
  cp.memType = NVBUF_MEM_DEFAULT;
  if (NvBufSurfaceCreate(&det_in, 1, &cp) != 0) return false;
  det_in->numFilled = 1;
  last_face_s = steady_s();  // start active
  return NvBufSurfaceMemSet(det_in, 0, 0, 0) == 0;
}

// Letterbox frame b into det_in on the VIC (scale by the smaller ratio, top-left, padding right/bottom: what nvinfer did
// with maintain-aspect-ratio=1, symmetric-padding=0), then SCRFD's input: RGB CHW, (x - 127.5) / 128.
static bool det_prepare(NvBufSurface *surf, int b, float scale) {
  NvBufSurfTransformConfigParams cfg = {};
  cfg.compute_mode = NvBufSurfTransformCompute_Default;  // VIC on Jetson
  if (NvBufSurfTransformSetSessionParams(&cfg) != NvBufSurfTransformError_Success) return false;
  NvBufSurface one = *surf;  // just frame b
  one.surfaceList = &surf->surfaceList[b];
  one.batchSize = one.numFilled = 1;
  NvBufSurfTransformRect src = {0, 0, surf->surfaceList[b].width, surf->surfaceList[b].height};
  NvBufSurfTransformRect dst = {0, 0, (guint)lrintf(src.width * scale), (guint)lrintf(src.height * scale)};
  NvBufSurfTransformParams p = {};
  p.transform_flag = NVBUFSURF_TRANSFORM_FILTER | NVBUFSURF_TRANSFORM_CROP_SRC | NVBUFSURF_TRANSFORM_CROP_DST;
  p.transform_filter = NvBufSurfTransformInter_Bilinear;
  p.src_rect = &src;
  p.dst_rect = &dst;
  if (NvBufSurfTransform(&one, det_in, &p) != NvBufSurfTransformError_Success) return false;
  if (NvBufSurfaceMap(det_in, 0, 0, NVBUF_MAP_READ) != 0) return false;
  NvBufSurfaceSyncForCpu(det_in, 0, 0);
  const NvBufSurfaceParams &d = det_in->surfaceList[0];
  float *in = live_det.host[0].data();
  const int plane = NET_W * NET_H;
  for (int y = 0; y < NET_H; y++) {
    const unsigned char *row = (const unsigned char *)d.mappedAddr.addr[0] + (size_t)y * d.pitch;
    for (int x = 0; x < NET_W; x++) {
      const unsigned char *q = row + 4 * x;
      int i = y * NET_W + x;
      in[i] = (q[0] - 127.5f) * 0.0078125f;
      in[plane + i] = (q[1] - 127.5f) * 0.0078125f;
      in[2 * plane + i] = (q[2] - 127.5f) * 0.0078125f;
    }
  }
  NvBufSurfaceUnMap(det_in, 0, 0);
  return true;
}

static void add_faces(NvDsBatchMeta *bm, NvDsFrameMeta *fm, const std::vector<Face> &faces) {
  for (auto &f : faces) {
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

static GstPadProbeReturn detect_probe(GstPad *, GstPadProbeInfo *info, gpointer) {
  static unsigned long long idle_n = 0;
  static long fails = 0;
  static bool was_idle = false;
  GstBuffer *buf = GST_PAD_PROBE_INFO_BUFFER(info);
  NvDsBatchMeta *bm = gst_buffer_get_nvds_batch_meta(buf);
  long now = steady_s();
  bool idle = IDLE_AFTER > 0 && now - last_face_s >= IDLE_AFTER;
  if (idle != was_idle) {
    was_idle = idle;
    if (idle) LOGI("no face for %d s: detecting every %d frame(s)", IDLE_AFTER, IDLE_STEP);
    else LOGI("face seen: detecting every frame");
  }
  if (!bm || !detect_due(now, last_face_s, idle_n)) return GST_PAD_PROBE_OK;
  GstMapInfo mi;
  if (!gst_buffer_map(buf, &mi, GST_MAP_READ)) return GST_PAD_PROBE_OK;
  auto *surf = (NvBufSurface *)mi.data;
  float scale = std::min((float)NET_W / mux_w, (float)NET_H / mux_h);
  for (NvDsMetaList *l = bm->frame_meta_list; l; l = l->next) {
    auto *fm = (NvDsFrameMeta *)l->data;
    if (!det_prepare(surf, fm->batch_id, scale) || !live_det.run()) {
      if (fails++ % 1000 == 0) LOGW("detection failed (%ld frames so far); those frames pass without faces", fails);
      continue;
    }
    n_detects++;
    fm->bInferDone = TRUE;  // without it nvtracker ignores this frame's detections
    auto faces = decode_scrfd([](const char *name) { return live_det.out(name); }, 1.f / scale);
    if (!faces.empty()) {
      last_face_s = now;
      if (was_idle) { was_idle = false; LOGI("face seen: detecting every frame"); }
    }
    add_faces(bm, fm, faces);
  }
  gst_buffer_unmap(buf, &mi);
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
static void label(NvDsObjectMeta *o, GstClockTime pts) {
  std::string txt = "Recognizing...";
  double r = 0.96, g = 0.62, b = 0.04;
  {
    std::lock_guard<std::mutex> lk(st_mu);
    if (GST_CLOCK_TIME_IS_VALID(pts)) last_label_pts = pts;
    auto it = o->object_id == UNTRACKED_OBJECT_ID ? tracks.end() : tracks.find(o->object_id);
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
      label(o, GST_BUFFER_PTS(buf));
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
  static long last = 0, last_det = 0;
  static auto t = std::chrono::steady_clock::now();
  auto now = std::chrono::steady_clock::now();
  double dt = std::chrono::duration<double>(now - t).count();
  long f = n_frames;
  if (dt < 1) { last = f; last_det = n_detects; return TRUE; }  // first call: start the interval
  double fps = (f - last) / dt, dps = (n_detects - last_det) / dt;
  last_det = n_detects;
  static int calls = 0;
  if (calls++ % 12 == 0)  // one log line a minute; status.json every 5 s
    LOGI("fps %.1f detections/s %.1f frames %ld faces %ld known %ld unknown %ld dropped %ld repeats %ld", fps, dps, f,
         (long)n_faces, (long)n_known, (long)n_unknown, (long)n_dropped, (long)n_repeats);
  last = f; t = now;
  // status.json for the web page (written to a temp file, then renamed so readers never see half a file)
  std::string tmp = DATA_DIR + "/status.json.tmp";
  if (FILE *s = fopen(tmp.c_str(), "w")) {
    fprintf(s, "{\"version\":\"%s\",\"updated\":%ld,\"fps\":%.1f,\"det_fps\":%.1f,\"frames\":%ld,\"known\":%ld,\"unknown\":%ld,"
               "\"step\":%u}\n",
            FACEPIPE_VERSION, (long)time(nullptr), fps, dps, f, (long)n_known, (long)n_unknown, frame_step());
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
    LOGE("decoder link failed");
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
    if (i < 1) { LOGE("FACEPIPE_DROP must be >= 1, got '%s'", n); exit(1); }
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
    // bufapi-version=1: DeepStream needs NvBufSurface; the JetPack 4 default (legacy NvBuffer) gives nvstreammux
    // "Input buffer number of surfaces (0) must be equal to ... (1)"
    return "nvarguscamerasrc bufapi-version=1 sensor-id=" + std::to_string(atoi(uri.c_str() + 4)) +
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
  if (GST_MESSAGE_TYPE(m) == GST_MESSAGE_EOS) { LOGI("end of stream"); g_main_loop_quit((GMainLoop *)loop); }
  else if (GST_MESSAGE_TYPE(m) == GST_MESSAGE_ERROR) {
    GError *e; gchar *d;
    gst_message_parse_error(m, &e, &d);
    LOGE("%s: %s (%s)", GST_OBJECT_NAME(m->src), e->message, d ? d : "");
    g_error_free(e); g_free(d);
    g_main_loop_quit((GMainLoop *)loop);
  }
  return TRUE;
}

static gboolean on_signal(gpointer loop) {
  begin_stop("signal");
  g_main_loop_quit((GMainLoop *)loop);
  return G_SOURCE_CONTINUE;
}

static GstElement *make(const char *f, const char *n) {
  GstElement *e = gst_element_factory_make(f, n);
  if (!e) { LOGE("cannot create %s", f); exit(1); }
  return e;
}

// ---- RTSP: rtsp://<nano>:8554/live, Basic auth from $FACEPIPE_RTSP_AUTH ("user:password", set by web.py
// --set-rtsp-password, mode 0600). Without that file the RTSP server stays off: faces must not stream without a login.
static void start_rtsp() {
  std::string path = env("FACEPIPE_RTSP_AUTH", env("FACEPIPE_DIR", ".") + "/rtsp_auth"), cred;
  std::ifstream f(path);
  if (!std::getline(f, cred) || cred.find(':') == std::string::npos) {
    LOGW("RTSP off: no login in %s", path.c_str());
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
  if (gst_rtsp_server_attach(srv, nullptr) == 0) LOGE("RTSP: cannot listen");
  else LOGI("RTSP on rtsp://<this device>:%s/live (login required)", env("FACEPIPE_RTSP_PORT", "8554").c_str());
}

#ifdef FACEPIPE_ENROLL  // separate binary: it needs OpenCV imgcodecs (imread), which the live binary must not load
// ---- enrollment: gallery_dir/<name>/<photo> -> GALLERY (written to a temp file, then renamed: live reload is safe) ----
// facepipe-enroll --check <photo> <out.jpg>: decode an uploaded photo on the CPU (no TensorRT), refuse it unless it is a
// real image of a sensible size, and write it again as a plain JPEG at most 1600 px wide (drops metadata and anything
// hidden in the file). The website runs this on every registration photo before it is stored for review.
// Decoding is bounded by CV_IO_MAX_IMAGE_PIXELS, which the caller sets.
static int check_photo(const char *in, const char *out) {
  cv::Mat img = cv::imread(in, cv::IMREAD_COLOR);
  if (img.empty()) { fprintf(stderr, "not a readable image\n"); return 2; }
  if (std::min(img.cols, img.rows) < 200 || std::max(img.cols, img.rows) > 8000) {
    fprintf(stderr, "image is %dx%d; it must be 200 to 8000 pixels on each side\n", img.cols, img.rows);
    return 3;
  }
  double s = std::min(1.0, 1600.0 / std::max(img.cols, img.rows));
  if (s < 1.0) cv::resize(img, img, cv::Size((int)lrint(img.cols * s), (int)lrint(img.rows * s)), 0, 0, cv::INTER_AREA);
  if (!cv::imwrite(out, img, {cv::IMWRITE_JPEG_QUALITY, 92})) { fprintf(stderr, "cannot write %s\n", out); return 4; }
  printf("%dx%d\n", img.cols, img.rows);
  return 0;
}

static int enroll(const std::string &root) {
  Engine det, rec;
  det.load(DET_ENGINE.c_str(), nvinfer1::Dims4{1, 3, NET_H, NET_W});
  rec.load(REC_ENGINE.c_str(), nvinfer1::Dims4{1, 3, 112, 112});
  DIR *d = opendir(root.c_str());
  if (!d) { LOGE("cannot open %s", root.c_str()); return 1; }
  std::vector<std::string> people;
  while (dirent *e = readdir(d)) if (e->d_name[0] != '.' && e->d_type == DT_DIR) people.push_back(e->d_name);
  closedir(d);
  std::string tmp = GALLERY + ".tmp";
  FILE *out = fopen(tmp.c_str(), "w");
  if (!out) { LOGE("cannot write %s", tmp.c_str()); return 1; }
  int ok = 0, bad = 0;
  std::sort(people.begin(), people.end());
  for (auto &name : people) {
    if (name.find('\t') != std::string::npos || name.find('\n') != std::string::npos) continue;
    DIR *pd = opendir((root + "/" + name).c_str());
    if (!pd) { LOGW("skip %s: cannot open folder", name.c_str()); continue; }
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
      if (bgr.empty()) { LOGW("skip %s: unreadable", path.c_str()); bad++; continue; }
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
      if (faces.empty()) { LOGW("skip %s: no face", path.c_str()); bad++; continue; }
      auto big = std::max_element(faces.begin(), faces.end(), [](const Face &a, const Face &b) {
        return (a.x2 - a.x1) * (a.y2 - a.y1) < (b.x2 - b.x1) * (b.y2 - b.y1); });
      auto e = embed(rec, align(rgb, big->kps));
      fprintf(out, "%s\t", name.c_str());
      for (int i = 0; i < 512; i++) fprintf(out, i ? ",%.6f" : "%.6f", e[i]);
      fprintf(out, "\n");
      LOGI("%s / %s: face %.0fx%.0f det %.2f%s", name.c_str(), f.c_str(), big->x2 - big->x1, big->y2 - big->y1,
              big->score, faces.size() > 1 ? " (largest of several)" : "");
      ok++;
    }
  }
  fclose(out);
  if (ok == 0 && !people.empty()) {  // keep the previous gallery: an empty result here means every photo failed
    unlink(tmp.c_str());
    LOGE("no usable photo in %s: %s left unchanged", root.c_str(), GALLERY.c_str());
    return 1;
  }
  if (rename(tmp.c_str(), GALLERY.c_str()) != 0) { LOGE("cannot replace %s", GALLERY.c_str()); return 1; }
  LOGI("enrolled %d photos of %zu people into %s (%d skipped)", ok, people.size(), GALLERY.c_str(), bad);
  return 0;
}
#endif

// facepipe --selftest: checks the match rule for both metrics (no camera, GPU or models needed)
static int selftest() {
  bool m = METRIC_L2;
  float c = MATCH, l = L2_MATCH;
  MATCH = 0.5f; L2_MATCH = 0.95f;
  assert(std::fabs(l2_of(1.f)) < 1e-6f && std::fabs(l2_of(0.5f) - 1.f) < 1e-6f && std::fabs(l2_of(-1.f) - 2.f) < 1e-6f);
  METRIC_L2 = false;
  assert(passes(0.5f) && passes(0.9f) && !passes(0.49f));
  METRIC_L2 = true;  // L2 0.95 is cosine 0.54875
  assert(passes(0.56f) && !passes(0.54f) && !passes(0.5f));
  Gallery g;  // same best candidate for both metrics
  g.names = {"a", "b"};
  g.embs = {std::vector<float>(512, 0.f), std::vector<float>(512, 0.f)};
  g.embs[0][0] = 1.f;
  g.embs[1][1] = 1.f;
  std::vector<float> q(512, 0.f);
  q[0] = 0.8f; q[1] = 0.6f;  // cosine 0.8 with a, 0.6 with b
  METRIC_L2 = false;
  assert(g.match(q).first == "a");
  METRIC_L2 = true;
  assert(g.match(q).first == "a");
  q[0] = 0.5f; q[1] = std::sqrt(0.75f);  // cosine 0.866 with b, 0.5 with a
  assert(g.match(q).first == "b");
  assert(Gallery().match(q).first == "unknown");
  {  // repeat rule (D-038): a name once per REPEAT_S; an unknown repeats only an unknown it would match
    std::vector<float> u(512, 0.f), v(512, 0.f);
    u[0] = 1.f; v[1] = 1.f;
    METRIC_L2 = false;
    assert(!repeat("a", u, 1000) && repeat("a", v, 1299) && !repeat("b", u, 1299) && !repeat("a", u, 1300));
    assert(!repeat("unknown", u, 2000) && repeat("unknown", u, 2100) && !repeat("unknown", v, 2100));
    logged.clear();
  }
  METRIC_L2 = m; MATCH = c; L2_MATCH = l;
  struct tm t = {};  // Friday 6 November 2026, 21:41:07
  t.tm_year = 126; t.tm_mon = 10; t.tm_mday = 6; t.tm_hour = 21; t.tm_min = 41; t.tm_sec = 7; t.tm_wday = 5;
  assert(log_stamp(t, 123) == "[06-11-2026] 09:41:07.123 PM, Friday");
  t.tm_hour = 0;
  assert(log_stamp(t, 5) == "[06-11-2026] 12:41:07.005 AM, Friday");
  t.tm_hour = 12;
  assert(log_stamp(t, 5) == "[06-11-2026] 12:41:07.005 PM, Friday");
  LOG_ROOT = "/logs";
  assert(log_path(t) == "/logs/2026/November/facepipe.log");
  assert(log_backup_name(log_path(t), t) == "/logs/2026/November/facepipe.log.06-Nov-2026");
  char tmpl[] = "/tmp/facepipe-selftest-XXXXXX";
  assert(mkdtemp(tmpl));
  std::string f = std::string(tmpl) + "/a.log";
  auto put = [&f](const char *txt) { FILE *o = fopen(f.c_str(), "w"); fputs(txt, o); fclose(o); };
  put("one\n"); log_backup(f, t);
  put("two\n"); log_backup(f, t);  // a second run on the same day appends to that day's backup
  std::ifstream bk(log_backup_name(f, t));
  std::string all((std::istreambuf_iterator<char>(bk)), std::istreambuf_iterator<char>());
  assert(all == "one\ntwo\n" && access(f.c_str(), F_OK) != 0);
  unlink(log_backup_name(f, t).c_str());
  rmdir(tmpl);
  LOG_ROOT.clear();
  int ia = IDLE_AFTER, is = IDLE_STEP;  // detection policy: active until IDLE_AFTER s without a face, then every IDLE_STEP
  IDLE_AFTER = 10; IDLE_STEP = 6;
  unsigned long long n = 0;
  auto count = [&n](int frames, long now, long last_face) {
    int due = 0;
    for (int i = 0; i < frames; i++) due += detect_due(now, last_face, n);
    return due;
  };
  int active = count(20, 100, 95);  // a face 5 s ago: every frame
  int idle = count(60, 100, 80);    // 20 s ago: every 6th
  int back = count(1, 100, 100);    // a new face: every frame again
  assert(active == 20 && idle == 10 && back == 1 && n == 0);
  IDLE_AFTER = 0;
  int off = count(20, 1000, 0);     // idle mode off
  assert(off == 20);
  IDLE_AFTER = ia; IDLE_STEP = is;
  printf("selftest ok\n");
  return 0;
}

int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--version")) { printf("%s\n", FACEPIPE_VERSION); return 0; }
  std::string app_dir = env("FACEPIPE_DIR", ".");
  DATA_DIR = env("FACEPIPE_DATA", app_dir + "/data");
  REC_ENGINE = app_dir + "/models/r50_b16.plan";
  DET_ENGINE = app_dir + "/models/scrfd_384x640_b1.plan";
  GALLERY = env("FACEPIPE_GALLERY", DATA_DIR + "/gallery.tsv");
  REC_DIR = env("FACEPIPE_REC", "/mnt/rec");
  MATCH = std::stof(env("FACEPIPE_MATCH", "0.5"));
  L2_MATCH = std::stof(env("FACEPIPE_L2_MATCH", "0.95"));
  std::string metric = env("FACEPIPE_METRIC", "cosine");
  std::transform(metric.begin(), metric.end(), metric.begin(), ::tolower);
  if (metric != "cosine" && metric != "l2") { LOGE("FACEPIPE_METRIC must be cosine or l2, got '%s'", metric.c_str()); return 1; }
  METRIC_L2 = metric == "l2";
  if (argc == 2 && !strcmp(argv[1], "--selftest")) return selftest();
  mkdir(DATA_DIR.c_str(), 0755);
  {  // face matching chosen on the website (System page): metric=cosine|l2, cosine=<min similarity>, l2=<max distance>
    std::ifstream mf(env("FACEPIPE_MATCHING", DATA_DIR + "/matching.conf"));
    std::string line;
    while (mf && std::getline(mf, line)) {
      size_t eq = line.find('=');
      if (eq == std::string::npos) continue;
      std::string k = line.substr(0, eq), v = line.substr(eq + 1);
      float f = (float)std::atof(v.c_str());
      if (k == "metric" && (v == "cosine" || v == "l2")) METRIC_L2 = v == "l2";
      else if (k == "cosine" && f >= 0.2f && f <= 0.95f) MATCH = f;
      else if (k == "l2" && f >= 0.3f && f <= 1.3f) L2_MATCH = f;
    }
  }
  LOG_ROOT = env("FACEPIPE_LOGS", app_dir + "/logs");
  IDLE_AFTER = std::atoi(env("FACEPIPE_IDLE_AFTER", "10").c_str());
  IDLE_STEP = std::max(1, std::atoi(env("FACEPIPE_IDLE_STEP", "6").c_str()));
#ifdef FACEPIPE_ENROLL
  LOG_MODULE = "enroll";
#endif
  std::string cam_uri;
#ifdef FACEPIPE_ENROLL
  if (argc == 4 && !strcmp(argv[1], "--check")) return check_photo(argv[2], argv[3]);
  if (argc == 2) return enroll(argv[1]);
  fprintf(stderr, "usage: %s <gallery_dir> | --check <photo> <out.jpg>\n", argv[0]);
  return 1;
#else
  // The camera chosen on the website's Cameras page (D-034): $FACEPIPE_CAMERAS, default data/cameras.active, one line
  // "<uri> <width> <height>". It overrides the command line, so FACEPIPE_URI in facepipe.env is only the fallback.
  {
    std::ifstream cf(env("FACEPIPE_CAMERAS", DATA_DIR + "/cameras.active"));
    std::string line;
    if (cf && std::getline(cf, line)) {
      std::istringstream ls(line);
      int w = 0, h = 0;
      ls >> cam_uri >> w >> h;
      if (!cam_uri.empty() && w > 0 && h > 0) { mux_w = w; mux_h = h; }
    }
  }
  if (argc < 2 && cam_uri.empty()) {
    fprintf(stderr, "usage: %s <rtsp://...|file://...|csi:0|usb:/dev/video0> [width height]\n"
                    "enroll photos with facepipe-enroll <gallery_dir>\n", argv[0]);
    return 1;
  }
#endif
  if (cam_uri.empty() && argc >= 4) { mux_w = atoi(argv[2]); mux_h = atoi(argv[3]); }
  json_out = fdopen(dup(1), "w");
  dup2(2, 1);
  std::string dir = env("FACEPIPE_DIR", ".");
  std::string uri = cam_uri.empty() ? argv[1] : cam_uri;
  if (!cam_uri.empty()) LOGI("camera chosen on the website, %dx%d", mux_w, mux_h);  // the URI may hold a password: not logged
  bool camera = uri.rfind("csi:", 0) == 0 || uri.rfind("usb:", 0) == 0;
  bool live = camera || uri.rfind("rtsp://", 0) == 0;
  LOGI("version %s, processing every %u frame(s), match %s %s %.2f, data %s", FACEPIPE_VERSION,
          frame_step(), METRIC_L2 ? "L2" : "cosine", METRIC_L2 ? "<=" : ">=", METRIC_L2 ? L2_MATCH : MATCH, DATA_DIR.c_str());
  start_watchdog();
  gst_init(&argc, &argv);
  if (!det_setup()) { LOGE("detector setup failed (engine %s or the 640x384 surface)", DET_ENGINE.c_str()); return 1; }
  LOGI("detection: every frame; after %d s without a face every %d frame(s)%s", IDLE_AFTER, IDLE_STEP,
       IDLE_AFTER > 0 ? "" : " (idle mode off)");
  std::thread worker(rec_worker);

  GMainLoop *loop = g_main_loop_new(nullptr, FALSE);
  GstElement *pipe = gst_pipeline_new("facepipe");
  GstElement *src;
  if (camera) {
    std::string d = camera_desc(uri, mux_w, mux_h);
    LOGI("camera: %s", d.c_str());
    GError *err = nullptr;
    src = gst_parse_bin_from_description(d.c_str(), TRUE, &err);
    if (!src) { LOGE("camera source: %s", err ? err->message : "?"); return 1; }
  } else {
    src = make("uridecodebin", "src");
    g_object_set(src, "uri", uri.c_str(), nullptr);
    g_signal_connect(src, "source-setup", G_CALLBACK(on_source_setup), nullptr);
  }
  GstElement *mux = make("nvstreammux", "mux"),
             *conv = make("nvvideoconvert", "conv"), *caps = make("capsfilter", "caps"),
             *trk = make("nvtracker", "trk"), *osd = make("nvdsosd", "osd"),
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
  // idrinterval = iframeinterval: every keyframe is an IDR with SPS/PPS in front. A clip starts at the newest keyframe in
  // the ring; with the default IDR interval (256 frames) that was usually a plain I-frame, h264parse dropped every frame
  // until the next IDR ("broken/invalid nal"), and clips came out short or empty (docs/bugs/clips-empty-or-short.md).
  g_object_set(henc, "bitrate", 4000000, "iframeinterval", 15, "idrinterval", 15, "insert-sps-pps", TRUE,
               "bufapi-version", TRUE, nullptr);
  g_object_set(hp, "config-interval", -1, nullptr);
  set_caps(fh2, "video/x-h264, stream-format=byte-stream, alignment=au");
  g_object_set(pay, "config-interval", 1, "pt", 96, nullptr);
  g_object_set(us, "host", "127.0.0.1", "port", 5400, "sync", FALSE, "async", FALSE, nullptr);
  g_object_set(sh, "emit-signals", TRUE, "sync", FALSE, "max-buffers", 60, "drop", TRUE, nullptr);
  g_signal_connect(sh, "new-sample", G_CALLBACK(on_h264), nullptr);
  mkdir(LIVE_DIR.c_str(), 0755);
  LOGI("recordings %s: %s", REC_DIR.c_str(), rec_ok() ? "on" : "off (not mounted or not writable)");
  g_object_set(mux, "batch-size", 1, "width", mux_w, "height", mux_h, "live-source", live,
               "batched-push-timeout", 40000, nullptr);
  GstCaps *rgba = gst_caps_from_string("video/x-raw(memory:NVMM), format=RGBA");
  g_object_set(caps, "caps", rgba, nullptr);
  gst_caps_unref(rgba);
  g_object_set(trk, "ll-lib-file", "/opt/nvidia/deepstream/deepstream-6.0/lib/libnvds_nvmultiobjecttracker.so",
               "ll-config-file", (dir + "/tracker.yml").c_str(), "tracker-width", 640, "tracker-height", 384, nullptr);
  gst_bin_add_many(GST_BIN(pipe), src, mux, conv, caps, trk, osd, t, qj, cj, fj, jenc, sj, qh, ch, fh, henc, hp, fh2,
                   te, qr, pay, us, qa, sh, nullptr);
  GstPad *msink = gst_element_get_request_pad(mux, "sink_0");
  gst_pad_add_probe(msink, GST_PAD_PROBE_TYPE_BUFFER, drop_probe, nullptr, nullptr);
  if (camera) {
    GstPad *sp = gst_element_get_static_pad(src, "src");
    if (gst_pad_link(sp, msink) != GST_PAD_LINK_OK) { LOGE("camera link failed"); return 1; }
    gst_object_unref(sp);
  } else {
    g_signal_connect(src, "pad-added", G_CALLBACK(on_pad), msink);
  }
  if (!gst_element_link_many(mux, conv, caps, trk, osd, t, nullptr) ||
      !gst_element_link_many(t, qj, cj, fj, jenc, sj, nullptr) ||
      !gst_element_link_many(t, qh, ch, fh, henc, hp, fh2, te, nullptr) ||
      !gst_element_link_many(te, qr, pay, us, nullptr) || !gst_element_link_many(te, qa, sh, nullptr)) {
    LOGE("link failed");
    return 1;
  }
  start_rtsp();

  GstPad *p = gst_element_get_static_pad(caps, "src");
  gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, detect_probe, nullptr, nullptr);
  gst_object_unref(p);
  p = gst_element_get_static_pad(trk, "src");
  gst_pad_add_probe(p, GST_PAD_PROBE_TYPE_BUFFER, tracker_probe, nullptr, nullptr);
  gst_object_unref(p);

  GstBus *bus = gst_pipeline_get_bus(GST_PIPELINE(pipe));
  gst_bus_add_watch(bus, on_bus, loop);
  gst_object_unref(bus);
  g_timeout_add_seconds(5, report, nullptr);
  gst_element_set_state(pipe, GST_STATE_PLAYING);
  // systemctl stop (SIGTERM) and Ctrl-C end the main loop, so the camera is released cleanly (an Argus session is
  // closed, not killed) and the open clip is finalized. Installed only here: a stall before this point (seen with the
  // CSI camera) still dies on SIGTERM at once instead of waiting for systemd's stop timeout.
  g_unix_signal_add(SIGTERM, on_signal, loop);
  g_unix_signal_add(SIGINT, on_signal, loop);
  g_main_loop_run(loop);

  // one shutdown path for signals, end of stream and errors; the watchdog forces an exit after STOP_LIMIT s
  begin_stop("main loop ended");  // no-op when a signal already began it
  gst_element_set_state(pipe, GST_STATE_NULL);
  report(nullptr);
  { std::lock_guard<std::mutex> lk(q_mu); q_stop = true; }
  q_cv.notify_all();
  worker.join();  // queued faces are still matched and logged as attendance; no new clips or evidence (stopping)
  { std::lock_guard<std::mutex> lk(v_mu); if (clip.pipe) clip_finish(); }
  while (clips_finalizing > 0) std::this_thread::sleep_for(std::chrono::milliseconds(100));
  LOGI("stopped");
  gst_object_unref(pipe);
  g_main_loop_unref(loop);
  return 0;  // stream ended or failed; systemd (Restart=always) reconnects a live camera
}
