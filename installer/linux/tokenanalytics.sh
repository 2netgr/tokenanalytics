#!/usr/bin/env bash
#
# TokenAnalytics — self-contained Linux launcher (shipped INSIDE the bundle).
#
# Everything the app needs ships in the bundle; nothing has to be installed on
# the host (no system node / python / git required):
#   runtime/python/bin/python3   self-contained CPython + backend deps
#   runtime/node/bin/node        self-contained Node binary
#   app/backend/                 FastAPI source (main.py + modules)
#   app/frontend/                Next.js `output: standalone` (server.js + deps)
#
# Ports/host can be overridden by env: PORT (frontend, default 3000),
# API_PORT (backend, default 8000), HOST (bind address, default 127.0.0.1).
# Data lives in $HOME/.tokenanalytics (override with TOKENANALYTICS_DATA_DIR).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HERE/runtime"
APP="$HERE/app"
PY="$RUNTIME/python/bin/python3"
NODE="$RUNTIME/node/bin/node"

FRONT_PORT="${PORT:-3000}"
API_PORT="${API_PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
DATA_DIR="${TOKENANALYTICS_DATA_DIR:-$HOME/.tokenanalytics}"

# Strip any inherited Python/venv pollution so the bundled interpreter always
# resolves its own stdlib + site-packages deterministically.
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONEXECUTABLE VIRTUAL_ENV CONDA_PREFIX 2>/dev/null || true

[ -x "$PY" ]   || { echo "ERROR: bundled python missing at $PY" >&2; exit 1; }
[ -x "$NODE" ] || { echo "ERROR: bundled node missing at $NODE" >&2; exit 1; }

mkdir -p "$DATA_DIR"

echo "TokenAnalytics (self-contained Linux bundle)"
echo "--------------------------------------------"
echo "  Dashboard: http://${HOST}:${FRONT_PORT}"
echo "  API:       http://${HOST}:${API_PORT}"
echo "  Data dir:  ${DATA_DIR}"
echo "  Python:    $("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
echo "  Node:      $("$NODE" -v)"
echo "  Press Ctrl+C to stop."
echo ""

# Backend (FastAPI/uvicorn). TOKENANALYTICS_DATA_DIR is read by tt_paths.
( cd "$APP/backend" && TOKENANALYTICS_DATA_DIR="$DATA_DIR" exec "$PY" main.py --port "$API_PORT" --host "$HOST" ) &
BACK_PID=$!

# Frontend (Next.js standalone server). Reads PORT + HOSTNAME. The browser
# derives the API base from window.location + the API port at runtime.
( cd "$APP/frontend" && PORT="$FRONT_PORT" HOSTNAME="$HOST" NEXT_PUBLIC_API_PORT="$API_PORT" exec "$NODE" server.js ) &
FRONT_PID=$!

cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "-> stopping services..."
  kill "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
  wait "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Exit as soon as either service dies (so a crash doesn't leave a half-up app).
wait -n "$BACK_PID" "$FRONT_PID"
