#!/usr/bin/env bash
#
# Build a self-contained TokenAnalytics Linux (x86_64) bundle and verify it on a
# clean container. Reproducible companion to desktop/build.sh (macOS).
#
# Produces:  desktop/dist/linux/tokenanalytics-linux-x64.tar.gz
#
# Requirements on the build host: docker (with linux/amd64 support), curl, and
# Node/npm to run `next build` for the frontend standalone output. The produced
# bundle itself needs NONE of node/python/git on the end-user's Linux box.
#
# Usage:  installer/linux/build-linux-bundle.sh [--skip-frontend] [--skip-verify]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"        # installer/linux
ROOT="$(cd "$HERE/../.." && pwd)"            # repo root
DIST="$ROOT/desktop/dist/linux"
WORK="$DIST/.work"
DL="$WORK/downloads"

NODE_VER="v22.23.1"
PY_TAG="20260623"
PY_ASSET="cpython-3.12.13+${PY_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
BUILDER_IMAGE="debian:12-slim"
VERIFY_IMAGE="ubuntu:22.04"

SKIP_FRONTEND=0
SKIP_VERIFY=0
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --skip-verify)   SKIP_VERIFY=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

mkdir -p "$DL"

echo "==> [1/5] Fetch linux runtimes (cached in $DL)"
if [ ! -f "$DL/node-linux-x64.tar.xz" ]; then
  curl -fsSL "https://nodejs.org/dist/${NODE_VER}/node-${NODE_VER}-linux-x64.tar.xz" -o "$DL/node-linux-x64.tar.xz"
fi
if [ ! -f "$DL/cpython-linux-x64.tar.gz" ]; then
  curl -fsSL "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_ASSET}" -o "$DL/cpython-linux-x64.tar.gz"
fi

echo "==> [2/5] Build frontend standalone (next build)"
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  ( cd "$ROOT/frontend"
    [ -d node_modules ] || npm install
    rm -rf .next
    npm run build >/dev/null
    cp -r public .next/standalone/ 2>/dev/null || true
    mkdir -p .next/standalone/.next
    cp -r .next/static .next/standalone/.next/ )
fi
[ -f "$ROOT/frontend/.next/standalone/server.js" ] || { echo "frontend standalone missing — run without --skip-frontend"; exit 1; }

echo "==> [3/5] Assemble bundle in $BUILDER_IMAGE (linux/amd64)"
cp "$HERE/tokenanalytics.sh" "$WORK/tokenanalytics.sh"
docker run --rm --platform linux/amd64 \
  -v "$ROOT":/repo:ro \
  -v "$WORK":/build \
  -v "$HERE/assemble.sh":/assemble.sh:ro \
  "$BUILDER_IMAGE" bash /assemble.sh
mv -f "$WORK/tokenanalytics-linux-x64.tar.gz" "$DIST/tokenanalytics-linux-x64.tar.gz"
echo "==> artifact: $DIST/tokenanalytics-linux-x64.tar.gz ($(du -h "$DIST/tokenanalytics-linux-x64.tar.gz" | cut -f1))"
shasum -a 256 "$DIST/tokenanalytics-linux-x64.tar.gz" 2>/dev/null || sha256sum "$DIST/tokenanalytics-linux-x64.tar.gz"

echo "==> [4/5] Verify on a CLEAN $VERIFY_IMAGE box (no node/python/git)"
if [ "$SKIP_VERIFY" -eq 0 ]; then
  docker run --rm --platform linux/amd64 \
    -v "$DIST":/dist:ro \
    -v "$HERE/verify.sh":/verify.sh:ro \
    "$VERIFY_IMAGE" bash /verify.sh
fi

echo "==> [5/5] Done."
