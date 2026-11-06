#!/bin/sh
# Fetch the JetPack 4.6 (L4T r32.7) headers needed to compile facepipe on a normal CI runner.
# Downloads NVIDIA's own arm64 .deb packages, verifies their SHA-256 against the r32.7 Packages index,
# unpacks them without installing (no maintainer scripts run) and keeps only the headers.
# Usage: sh ci/jetpack-headers.sh <out_dir>   -> <out_dir>/{ds,cuda,trt}
set -eu
OUT=$1
BASE=https://repo.download.nvidia.com/jetson/common
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# file path in the repo pool | sha256 from dists/r32.7/main/binary-arm64/Packages
cat > "$TMP/list" <<'EOF'
pool/main/d/deepstream-6.0/deepstream-6.0_6.0.1-1_arm64.deb 5520f52b30f02e4e6535d11229b758a9c46fb6004528ea9eda0952b9bbf2d5f5
pool/main/t/tensorrt/libnvinfer-dev_8.2.1-1+cuda10.2_arm64.deb e2314b9eaad7324fc5fe08bb42160026f6f4332781248658ae261d65a8cb94df
pool/main/t/tensorrt/libnvinfer-plugin-dev_8.2.1-1+cuda10.2_arm64.deb 856f571e4e7c478cabf24e47f7274b4a6893a9640b0712d7790833c40e4bc0ed
pool/main/c/cuda-cudart/cuda-cudart-dev-10-2_10.2.300-1_arm64.deb d37a94a3fb858db2cf41cde1bcbe1042b9a66d4fd3fd30882805a478523acb18
pool/main/c/cuda-cudart/cuda-driver-dev-10-2_10.2.300-1_arm64.deb 5ea760773de2685acfac3931bcdc2eff3a18eceb28fc86f80061e215e9e81456
pool/main/c/cuda-nvcc/cuda-nvcc-10-2_10.2.300-1_arm64.deb 86c138d6903fa35bb512414bbb98dfd519d3e5ee743dc846e9fb1aa42f7e0391
EOF

mkdir -p "$TMP/root"
while read -r path sha; do
  f="$TMP/$(basename "$path")"
  curl -fsSL --retry 3 -o "$f" "$BASE/$path"
  echo "$sha  $f" | sha256sum -c - >/dev/null || { echo "checksum mismatch: $path"; exit 1; }
  dpkg-deb -x "$f" "$TMP/root"
  rm -f "$f"
  echo "ok $(basename "$path")"
done < "$TMP/list"

R=$TMP/root
mkdir -p "$OUT/ds/sources" "$OUT/cuda" "$OUT/trt"
cp -r "$R/opt/nvidia/deepstream/deepstream-6.0/sources/includes" "$OUT/ds/sources/"
cp -rL "$R/usr/local/cuda-10.2/targets/aarch64-linux/include" "$OUT/cuda/"
cp "$R"/usr/include/aarch64-linux-gnu/NvInfer*.h "$OUT/trt/"
echo "headers in $OUT: $(find "$OUT" -name '*.h' | wc -l) files, $(du -sh "$OUT" | cut -f1)"
