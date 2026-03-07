#!/usr/bin/env bash
# Install the Rust stable toolchain (minimal profile — no docs or clippy).
set -euo pipefail

echo "==> Installing Rust stable"
curl -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain stable --profile minimal

source /root/.cargo/env

echo "==> Rust toolchain:"
rustc --version
cargo --version
