#!/usr/bin/env bash
#
# Runs INSIDE a CLEAN ubuntu:22.04 container (called by build-linux-bundle.sh)
# to PROVE the bundle is self-contained. Asserts no system node/python/git
# exist, extracts the tarball, launches the app, and curls both services.
#
#   /dist   dir holding tokenanalytics-linux-x64.tar.gz (read-only)
set -uo pipefail

echo "=================================================================="
echo "STEP 0 — prove the box is clean (no preinstalled runtimes)"
echo "=================================================================="
echo -n "node   : "; command -v node    || echo "(absent)"
echo -n "python3: "; command -v python3 || echo "(absent)"
echo -n "python : "; command -v python  || echo "(absent)"
echo -n "git    : "; command -v git     || echo "(absent)"
echo -n "npm    : "; command -v npm     || echo "(absent)"

echo ""
echo "=================================================================="
echo "STEP 1 — install curl ONLY (test harness; not a runtime dep)"
echo "=================================================================="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq curl >/dev/null 2>&1
echo "curl installed: $(curl --version | head -1)"

echo ""
echo "=================================================================="
echo "STEP 2 — extract the bundle to /opt"
echo "=================================================================="
mkdir -p /opt/ta
tar -xzf /dist/tokenanalytics-linux-x64.tar.gz -C /opt/ta
ls -la /opt/ta/tokenanalytics

echo ""
echo "=================================================================="
echo "STEP 3 — launch the app via the bundled launcher"
echo "=================================================================="
cd /opt/ta/tokenanalytics
nohup ./tokenanalytics.sh > /tmp/ta.log 2>&1 &
APP_PID=$!
echo "launcher PID: $APP_PID"

echo ""
echo "=================================================================="
echo "STEP 4 — wait for the dashboard to answer (up to 90s)"
echo "=================================================================="
for i in $(seq 1 90); do
  if curl -s -o /dev/null http://localhost:3000 2>/dev/null; then echo "frontend up after ${i}s"; break; fi
  sleep 1
done
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/sync/status 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then echo "backend up"; break; fi
  sleep 1
done

echo ""
echo "=================================================================="
echo "STEP 5 — MANDATED VERIFICATION"
echo "=================================================================="
echo '--- CMD: curl -s http://localhost:3000 | grep -o "<title>[^<]*</title>" ---'
FRONT_HTML="$(curl -s http://localhost:3000)"
echo "$FRONT_HTML" | grep -o '<title>[^<]*</title>' || echo "!!! <title> NOT FOUND"
echo ""
if echo "$FRONT_HTML" | grep -q '<title>TokenAnalytics'; then
  echo "PASS: <title>TokenAnalytics present on http://localhost:3000"
else
  echo "FAIL: <title>TokenAnalytics NOT present"
fi
echo ""
echo '--- CMD: curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/sync/status ---'
API_CODE="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/sync/status)"
echo "HTTP $API_CODE  (from http://localhost:8000/sync/status)"
echo ""
echo "--- sample of /sync/status body ---"
curl -s http://localhost:8000/sync/status | head -c 400; echo ""

echo ""
echo "=================================================================="
echo "RESULT"
echo "=================================================================="
if echo "$FRONT_HTML" | grep -q '<title>TokenAnalytics' && [ "$API_CODE" = "200" ]; then
  echo "OVERALL: PASS — self-contained bundle serves dashboard + API on a clean box"; RC=0
else
  echo "OVERALL: FAIL"; RC=1
fi

echo ""
echo "=== APP LOG (tail) ==="
tail -40 /tmp/ta.log || true
kill "$APP_PID" 2>/dev/null || true
exit $RC
