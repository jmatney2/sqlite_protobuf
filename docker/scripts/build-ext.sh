#!/usr/bin/env bash
# Compile the extension inside the builder container, then restore ownership
# so the output .so is not owned by root on the host filesystem.
set -euo pipefail

EXT="target/release/libsqlite_protobuf.so"

echo "==> Building libsqlite_protobuf (release)"
scl enable llvm-toolset-7 "cargo build --release"

echo "==> Build complete: ${EXT}"

if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ]; then
    echo "==> Restoring ownership to ${HOST_UID}:${HOST_GID}"
    chown -R "${HOST_UID}:${HOST_GID}" target/
fi
