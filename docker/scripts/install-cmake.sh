#!/usr/bin/env bash
# Download and install a pre-built cmake binary.
# cmake 3.22+ is required by protobuf 34.x; the version bundled with
# CentOS 7 (cmake3 3.17) is too old.
set -euo pipefail

# https://github.com/Kitware/CMake/releases/download/v4.2.3/cmake-4.2.3.tar.gz
CMAKE_VERSION="${CMAKE_VERSION:-4.2.3}"
ARCHIVE="cmake-${CMAKE_VERSION}-linux-x86_64.sh"
URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${ARCHIVE}"

echo "==> Downloading cmake ${CMAKE_VERSION}"
wget -q "${URL}" -O "/tmp/${ARCHIVE}"

echo "==> Installing cmake ${CMAKE_VERSION} to /usr/local"
yes | bash /tmp/${ARCHIVE} || true
cp -r "/cmake-${CMAKE_VERSION}-linux-x86_64/bin/"*   /usr/local/bin/
cp -r "/cmake-${CMAKE_VERSION}-linux-x86_64/share/"* /usr/local/share/
