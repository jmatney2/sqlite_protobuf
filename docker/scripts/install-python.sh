#!/usr/bin/env bash
# Install a standalone Python build from astral-sh/python-build-standalone.
# These builds explicitly target glibc >= 2.17, making them compatible with
# CentOS 7 and all newer Linux distributions.
# https://github.com/astral-sh/python-build-standalone
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.12.8}"
PYTHON_BUILD="${PYTHON_BUILD:-20241219}"
ARCHIVE="cpython-${PYTHON_VERSION}+${PYTHON_BUILD}-x86_64-unknown-linux-gnu-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD}/${ARCHIVE}"

echo "==> Downloading Python ${PYTHON_VERSION} (standalone, glibc 2.17 compatible)"
wget -q "${URL}" -O "/tmp/${ARCHIVE}"

echo "==> Extracting to /usr/local"
tar -xf "/tmp/${ARCHIVE}" -C /usr/local
rm "/tmp/${ARCHIVE}"

# The archive extracts to /usr/local/python/
ln -sf /usr/local/python/bin/python3   /usr/local/bin/python3
ln -sf /usr/local/python/bin/pip3      /usr/local/bin/pip3

echo "==> Python version:"
python3 --version
