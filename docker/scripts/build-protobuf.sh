#!/usr/bin/env bash
# Build protobuf from source and install protoc to /usr/local.
# If abseil-cpp is not already installed, cmake's FetchContent will
# download and build it automatically during the configure step.
set -euo pipefail

PROTOBUF_VERSION="${PROTOBUF_VERSION:-34.0}"
ARCHIVE="protobuf-${PROTOBUF_VERSION}.tar.gz"
URL="https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOBUF_VERSION}/${ARCHIVE}"
SRC="/tmp/protobuf-${PROTOBUF_VERSION}"
BUILD="/tmp/protobuf-build"

echo "==> Downloading protobuf ${PROTOBUF_VERSION}"
wget -q "${URL}" -O "/tmp/${ARCHIVE}"
tar -xf "/tmp/${ARCHIVE}" -C /tmp

echo "==> Configuring"
cmake -S "${SRC}" -B "${BUILD}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -Dprotobuf_BUILD_TESTS=OFF \
    -Dprotobuf_BUILD_SHARED_LIBS=OFF \
    -Dprotobuf_ABSL_PROVIDER=module

echo "==> Building ($(nproc) jobs)"
cmake --build "${BUILD}" --parallel "$(nproc)"

echo "==> Installing"
cmake --install "${BUILD}"
ldconfig

echo "==> protoc version:"
protoc --version

rm -rf "/tmp/${ARCHIVE}" "${SRC}" "${BUILD}"
