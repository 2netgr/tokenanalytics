#!/usr/bin/env bash
#
# Runs INSIDE a linux/amd64 builder container (called by build-linux-bundle.sh).
# Assembles the self-contained TokenAnalytics Linux bundle and tars it. Native
# backend wheels (pydantic-core, uvloop, httptools, watchfiles) are installed
# with the BUNDLED linux python so they are linux binaries, never Mac ones.
#
#   /repo   repo root (read-only)
#   /build  scratch dir holding downloads/ + tokenanalytics.sh; gets the tarball
#
# The bundled python's lzma is used to unpack Node's .tar.xz, so the builder
# image needs nothing beyond base `tar` + `gzip` (no xz-utils, no apt).
set -euo pipefail

REPO=/repo
BUILD=/build
STAGE=/stage
BUNDLE="$STAGE/tokenanalytics"

rm -rf "$STAGE"
mkdir -p "$BUNDLE/runtime" "$BUNDLE/app"

echo "== [1/7] extract CPython (gzip) =="
tar -xzf "$BUILD/downloads/cpython-linux-x64.tar.gz" -C "$BUNDLE/runtime"   # -> runtime/python
PY="$BUNDLE/runtime/python/bin/python3"
[ -x "$PY" ] || { echo "python missing after extract"; exit 1; }
"$PY" -c 'import sys;print("  python", ".".join(map(str,sys.version_info[:3])))'

echo "== [2/7] extract Node (.tar.xz via bundled python lzma), keep only the binary =="
rm -rf /tmp/nodex && mkdir -p /tmp/nodex
"$PY" - <<'PY'
import tarfile
with tarfile.open('/build/downloads/node-linux-x64.tar.xz') as t:
    t.extractall('/tmp/nodex')
print("  node extracted")
PY
NODE_INNER="$(ls -d /tmp/nodex/node-v*-linux-x64)"
mkdir -p "$BUNDLE/runtime/node/bin"
cp -a "$NODE_INNER/bin/node" "$BUNDLE/runtime/node/bin/node"
chmod +x "$BUNDLE/runtime/node/bin/node"
"$BUNDLE/runtime/node/bin/node" -v | sed 's/^/  node /'

echo "== [3/7] backend source (exclude venv/pycache/tests) =="
( cd "$REPO/backend" && tar -cf - \
    --exclude=venv --exclude=__pycache__ --exclude='*.pyc' \
    --exclude='.pytest_cache' --exclude='test_*.py' --exclude=conftest.py . ) \
  | ( cd "$BUNDLE/app" && mkdir -p backend && cd backend && tar -xf - )

echo "== [4/7] frontend standalone bundle =="
cp -a "$REPO/frontend/.next/standalone/." "$BUNDLE/app/frontend/"
[ -f "$BUNDLE/app/frontend/server.js" ] || { echo "frontend server.js missing (run 'next build' first)"; exit 1; }

echo "== [5/7] launcher =="
cp "$BUILD/tokenanalytics.sh" "$BUNDLE/tokenanalytics.sh"
chmod +x "$BUNDLE/tokenanalytics.sh"

echo "== [6/7] install backend deps into bundled python (linux-native wheels) =="
"$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PY" -m pip install --no-cache-dir --quiet --root-user-action=ignore --upgrade pip
"$PY" -m pip install --no-cache-dir --quiet --root-user-action=ignore -r "$BUNDLE/app/backend/requirements.txt"
echo "  deps installed; verifying imports:"
"$PY" - <<'PY'
import fastapi, uvicorn, yaml, pydantic, pydantic_core
import uvloop, httptools, websockets
print("    fastapi", fastapi.__version__)
print("    uvicorn", uvicorn.__version__)
print("    pydantic", pydantic.__version__, "/ pydantic_core", pydantic_core.__version__)
print("    uvloop/httptools/websockets: OK (native linux)")
print("    pydantic_core file:", pydantic_core._pydantic_core.__file__)
PY

echo "== [7/7] tarball =="
cd "$STAGE"
tar -czf "$BUILD/tokenanalytics-linux-x64.tar.gz" tokenanalytics
echo "  bundle uncompressed: $(du -sh "$BUNDLE" | cut -f1)"
echo "  tarball: $(ls -la "$BUILD/tokenanalytics-linux-x64.tar.gz" | awk '{print $5}') bytes"
echo "ASSEMBLE DONE"
