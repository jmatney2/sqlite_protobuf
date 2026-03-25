#!/usr/bin/env bash
# Build the custom frser-sqlite-datasource backend and package the plugin as a
# zip suitable for manual installation on an airgapped Grafana instance.
#
# Usage (run from repo root):
#   bash grafana_demo/build_plugin_zip.sh [output-dir]
#
# Output: <output-dir>/frser-sqlite-datasource.zip  (default: grafana_demo/dist)
#
# On the target system:
#   1. Unzip to the Grafana plugins directory:
#        unzip frser-sqlite-datasource.zip -d /var/lib/grafana/plugins/
#   2. Allow the unsigned plugin in grafana.ini or via env var:
#        GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=frser-sqlite-datasource
#   3. Restart Grafana.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${1:-$SCRIPT_DIR/dist}"
PLUGIN_ID="frser-sqlite-datasource"
IMAGE_TAG="grafana-sqlite-datasource-builder:$$"
CONTAINER_NAME="grafana-plugin-extract-$$"

cleanup() {
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    docker rmi -f "$IMAGE_TAG"    2>/dev/null || true
}
trap cleanup EXIT

echo "==> Building plugin image..."
docker build \
    -f "$SCRIPT_DIR/Dockerfile" \
    -t "$IMAGE_TAG" \
    "$REPO_ROOT"

echo "==> Extracting plugin from image..."
docker create --name "$CONTAINER_NAME" "$IMAGE_TAG" >/dev/null

mkdir -p "$OUT_DIR"
TMP_DIR="$(mktemp -d)"
docker cp "$CONTAINER_NAME:/var/lib/grafana/plugins/$PLUGIN_ID" "$TMP_DIR/"

echo "==> Creating zip..."
ZIP_PATH="$OUT_DIR/$PLUGIN_ID.zip"
(cd "$TMP_DIR" && zip -r "$ZIP_PATH" "$PLUGIN_ID/")
rm -rf "$TMP_DIR"

echo "==> Done: $ZIP_PATH"
echo ""
echo "Install on target:"
echo "  unzip $PLUGIN_ID.zip -d /var/lib/grafana/plugins/"
echo "  # then set GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=$PLUGIN_ID"
