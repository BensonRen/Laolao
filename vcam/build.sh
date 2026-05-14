#!/bin/bash
# Build the virtual camera dylib (macOS only, requires Xcode Command Line Tools)
set -e
cd "$(dirname "$0")"
clang++ \
  -arch arm64 \
  -dynamiclib \
  -fobjc-arc \
  -std=c++17 \
  -framework Foundation \
  -framework CoreVideo \
  -framework CoreMedia \
  -framework IOSurface \
  -o libvcam.dylib \
  vcam_wrapper.mm OBSDALMachServer.mm
echo "Built libvcam.dylib (arm64)"
