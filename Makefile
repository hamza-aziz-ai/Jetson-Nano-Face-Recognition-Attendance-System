DS ?= /opt/nvidia/deepstream/deepstream-6.0
CUDA ?= /usr/local/cuda-10.2
OPENCV_PC ?= /opt/opencv-4.9.0-cuda/lib/pkgconfig
OPENCV_PKG ?= opencv4
OCV = /opt/opencv-4.9.0-cuda/lib
# extra include dirs, e.g. TensorRT headers outside the default path (used by CI)
EXTRA_INC ?=
# CI uses -isystem so warnings inside NVIDIA headers do not fail -Werror (they are system headers on the device)
INC ?= -I

CXXFLAGS = -O2 -std=c++14 -Wall $(INC) $(DS)/sources/includes $(INC) $(CUDA)/include $(EXTRA_INC) \
  $(shell pkg-config --cflags gstreamer-1.0 gstreamer-app-1.0) $(shell PKG_CONFIG_PATH=$(OPENCV_PC) pkg-config --cflags $(OPENCV_PKG))
# gst-rtsp-server: runtime library only (no -dev package), linked by path; prototypes are in facepipe.cpp
LDLIBS = $(shell pkg-config --libs gstreamer-1.0 gstreamer-app-1.0) /usr/lib/aarch64-linux-gnu/libgstrtspserver-1.0.so.0 \
  -L$(DS)/lib -lnvdsgst_meta -lnvds_meta -lnvbufsurface -Wl,-rpath,$(DS)/lib \
  -L$(CUDA)/lib64 -lcudart -lnvinfer -lnvinfer_plugin \
  -L$(OCV) -lopencv_core -lopencv_imgproc -Wl,-rpath,$(OCV) -lpthread

all: facepipe facepipe-enroll tracker.yml

# live binary: no opencv_imgcodecs (its libjpeg clashes with the L4T libjpeg used by nvjpegenc)
facepipe: facepipe.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDLIBS)

facepipe-enroll: facepipe.cpp
	$(CXX) $(CXXFLAGS) -DFACEPIPE_ENROLL -o $@ $< $(LDLIBS) -lopencv_imgcodecs

tracker.yml:
	cp $(DS)/samples/configs/deepstream-app/config_tracker_IOU.yml $@

# compile both variants without linking (CI: headers only, no Jetson libraries); warnings are errors
check:
	$(CXX) $(CXXFLAGS) -Werror -fsyntax-only facepipe.cpp
	$(CXX) $(CXXFLAGS) -Werror -fsyntax-only -DFACEPIPE_ENROLL facepipe.cpp

.PHONY: all check
