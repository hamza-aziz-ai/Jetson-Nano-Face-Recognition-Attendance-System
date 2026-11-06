#!/bin/sh
# Inside ubuntu:18.04: install the toolchain, fetch JetPack headers into .jp (cached by CI), compile both
# binaries without linking, then run static analysis. Usage: docker run --rm -v "$PWD:/w" -w /w ubuntu:18.04 sh ci/compile-check.sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends g++ make pkg-config curl ca-certificates \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libopencv-dev cppcheck >/dev/null
g++ --version | head -1
[ -d .jp/ds ] || sh ci/jetpack-headers.sh .jp
make check DS=.jp/ds CUDA=.jp/cuda INC=-isystem EXTRA_INC="-isystem .jp/trt" OPENCV_PC= OPENCV_PKG=opencv
echo "compile ok: facepipe and facepipe-enroll"
cppcheck --error-exitcode=1 --enable=warning --inline-suppr -q --std=c++14 facepipe.cpp
echo "cppcheck ok"
