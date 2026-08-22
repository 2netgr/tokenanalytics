#!/usr/bin/env bash
#
# Build TokenAnalytics.app — a self-contained macOS app bundling a CPython
# runtime (FastAPI backend), a node binary (Next.js standalone frontend), and a
# native WKWebView shell. Produces desktop/dist/TokenAnalytics.app and, with
# `--dmg`, desktop/dist/TokenAnalytics.dmg.
#
# Usage:  desktop/build.sh [--dmg] [--skip-frontend] [--release]
#   --release  Developer ID signing + notarization + stapling (implies --dmg)
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"      # desktop/
ROOT="$(cd "$HERE/.." && pwd)"             # repo root
APP_NAME="TokenAnalytics"
DIST="$HERE/dist"
# Assemble + sign on a CLEAN filesystem, never under the repo. The repo lives on
# an iCloud-synced Desktop, whose file-provider attaches un-removable xattrs
# (com.apple.fileprovider.fpfs#P, FinderInfo) that make codesign reject the
# bundle. We build under /private/tmp, then copy the finished artifacts to dist/.
STAGE="${TMPDIR:-/tmp}/tokenanalytics-build"
APP="$STAGE/$APP_NAME.app"
RUNTIME="$HERE/runtime"
ICON_SRC="$ROOT/landing/assets/img/icon-512.png"
PY_TAG="20260623"
PY_ASSET="cpython-3.12.13+${PY_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
NODE_VER="v22.23.1"

MAKE_DMG=0
SKIP_FRONTEND=0
RELEASE=0
for arg in "$@"; do
  case "$arg" in
    --dmg) MAKE_DMG=1 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --release) RELEASE=1; MAKE_DMG=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

# Release signing. Developer ID Application identity + hardened runtime +
# notarization + stapling, so a downloaded copy opens with no Gatekeeper
# warning. Override with env: SIGN_IDENTITY, NOTARY_PROFILE.
#   one-time setup:  xcrun notarytool store-credentials "$NOTARY_PROFILE" \
#                      --apple-id <apple id> --team-id <team id>   (app-specific password)
SIGN_IDENTITY="${SIGN_IDENTITY:-Developer ID Application}"
NOTARY_PROFILE="${NOTARY_PROFILE:-tokenanalytics-notary}"
if [ "$RELEASE" -eq 1 ]; then
  if ! security find-identity -v -p codesigning | grep -q "$SIGN_IDENTITY"; then
    echo "✗ no '$SIGN_IDENTITY' certificate in the keychain."
    echo "  Create one: Xcode → Settings → Accounts → Manage Certificates → + → Developer ID Application"
    exit 1
  fi
  if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
    echo "✗ notarytool profile '$NOTARY_PROFILE' missing. Run once:"
    echo "  xcrun notarytool store-credentials $NOTARY_PROFILE --apple-id <apple id> --team-id <team id>"
    exit 1
  fi
fi

VERSION="$(node -p "require('$ROOT/package.json').version" 2>/dev/null || echo '1.0.0')"
echo "▸ Building $APP_NAME $VERSION"

# ---------------------------------------------------------------------------
# 1. Runtimes — fetch into desktop/runtime/ if missing (kept out of git).
# ---------------------------------------------------------------------------
mkdir -p "$RUNTIME"
if [ ! -x "$RUNTIME/python/bin/python3" ]; then
  echo "▸ Fetching CPython $PY_TAG…"
  curl -fsSL "https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_ASSET}" -o /tmp/ta_py.tar.gz
  rm -rf "$RUNTIME/python"
  tar -xzf /tmp/ta_py.tar.gz -C "$RUNTIME"      # extracts ./python
  "$RUNTIME/python/bin/python3" -m pip install --quiet --upgrade pip
  "$RUNTIME/python/bin/python3" -m pip install --quiet -r "$ROOT/backend/requirements.txt"
fi
if [ ! -x "$RUNTIME/node-bin" ]; then
  echo "▸ Fetching Node $NODE_VER…"
  curl -fsSL "https://nodejs.org/dist/${NODE_VER}/node-${NODE_VER}-darwin-arm64.tar.gz" -o /tmp/ta_node.tar.gz
  rm -rf /tmp/ta_node_x && mkdir -p /tmp/ta_node_x
  tar -xzf /tmp/ta_node.tar.gz -C /tmp/ta_node_x --strip-components=1
  cp /tmp/ta_node_x/bin/node "$RUNTIME/node-bin"
  chmod +x "$RUNTIME/node-bin"
fi

# ---------------------------------------------------------------------------
# 2. Frontend — production `output: standalone` build.
# ---------------------------------------------------------------------------
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  echo "▸ Building frontend (next build, standalone)…"
  ( cd "$ROOT/frontend"
    [ -d node_modules ] || npm install
    rm -rf .next
    npm run build >/dev/null
    cp -r public .next/standalone/ 2>/dev/null || true
    mkdir -p .next/standalone/.next
    cp -r .next/static .next/standalone/.next/ )
fi
[ -f "$ROOT/frontend/.next/standalone/server.js" ] || { echo "frontend standalone missing — run without --skip-frontend"; exit 1; }

# ---------------------------------------------------------------------------
# 3. Assemble the .app skeleton.
# ---------------------------------------------------------------------------
echo "▸ Assembling bundle…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" \
         "$APP/Contents/Resources/runtime" \
         "$APP/Contents/Resources/app"

# Info.plist (version substituted in).
sed "s/__VERSION__/$VERSION/g" "$HERE/Info.plist.template" > "$APP/Contents/Info.plist"

# Native shell.
echo "▸ Compiling Swift shell…"
# -no_adhoc_codesign: emit an UNSIGNED executable. The Swift linker otherwise
# stamps its own "linker-signed" ad-hoc signature, which codesign then refuses
# to replace when sealing the bundle ("detritus not allowed"). Leaving it
# unsigned lets the bundle-wide ad-hoc sign below cover it cleanly.
swiftc -O -o "$APP/Contents/MacOS/$APP_NAME" "$HERE/shell/main.swift" \
  -framework Cocoa -framework WebKit \
  -Xlinker -no_adhoc_codesign

# Icon.
echo "▸ Generating icon…"
ICONSET="$(mktemp -d)/AppIcon.iconset"; mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$ICON_SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s*2)); sips -z "$d" "$d" "$ICON_SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

# Runtimes.
echo "▸ Copying runtimes…"
cp -R "$RUNTIME/python" "$APP/Contents/Resources/runtime/python"
cp "$RUNTIME/node-bin" "$APP/Contents/Resources/runtime/node"
chmod +x "$APP/Contents/Resources/runtime/node"

# Backend source (no venv / tests / caches).
rsync -a \
  --exclude 'venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'test_*.py' --exclude '*.pyc' \
  "$ROOT/backend/" "$APP/Contents/Resources/app/backend/"

# Frontend standalone bundle.
cp -R "$ROOT/frontend/.next/standalone/." "$APP/Contents/Resources/app/frontend/"

# Precompile bytecode with HASH-based caches (mtime-independent, so they survive
# the copy). Paired with PYTHONDONTWRITEBYTECODE at runtime: the interpreter
# reads these .pyc but never writes a new one into the signed bundle (that write
# hangs under launchd). Keeps cold-start fast without touching the bundle.
echo "▸ Precompiling Python bytecode (hash-based)…"
"$APP/Contents/Resources/runtime/python/bin/python3" -m compileall -q -f \
  --invalidation-mode unchecked-hash \
  "$APP/Contents/Resources/runtime/python/lib/python3.12" \
  "$APP/Contents/Resources/app/backend" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# 4. Code signing. Default is ad-hoc (free). With --release: Developer ID +
#    hardened runtime + secure timestamp. Every nested Mach-O (python dylibs /
#    extension modules, the node binary, the python interpreter) is signed
#    individually, inside-out, then the bundle is sealed — --deep is not
#    reliable for nested runtimes and is deprecated.
# ---------------------------------------------------------------------------
if [ "$RELEASE" -eq 1 ]; then
  echo "▸ Signing (Developer ID, hardened runtime)…"
  IDENT="$SIGN_IDENTITY"
  SIGN_FLAGS=(--force --timestamp --options runtime)
else
  echo "▸ Signing (ad-hoc)…"
  IDENT="-"
  SIGN_FLAGS=(--force)
fi
# Best-effort: clear stray Finder info / resource forks. (com.apple.provenance
# is sticky on recent macOS and can't be removed, but it doesn't block signing
# an unsigned binary — which is why the Swift shell is built unsigned above.)
xattr -cr "$APP" 2>/dev/null || true

is_macho() { file -b "$1" 2>/dev/null | grep -q "Mach-O"; }
RES="$APP/Contents/Resources"
PYROOT="$RES/runtime/python"

# 4a. Python runtime: every dylib / .so / executable, libraries first.
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  codesign "${SIGN_FLAGS[@]}" -s "$IDENT" --entitlements "$HERE/entitlements/python.plist" "$f"
done < <(find "$PYROOT" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  codesign "${SIGN_FLAGS[@]}" -s "$IDENT" --entitlements "$HERE/entitlements/python.plist" "$f"
done < <(find "$PYROOT/bin" -type f -perm -u+x -print0)

# 4b. Node binary (V8 JIT entitlements).
codesign "${SIGN_FLAGS[@]}" -s "$IDENT" --entitlements "$HERE/entitlements/node.plist" "$RES/runtime/node"

# 4c. Native addons + their dylibs in the app bundle (e.g. sharp's libvips).
while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  codesign "${SIGN_FLAGS[@]}" -s "$IDENT" "$f"
done < <(find "$RES/app" -type f \( -name '*.node' -o -name '*.dylib' -o -name '*.so' \) -print0)

# 4d. Seal the bundle (signs the Swift shell + writes _CodeSignature).
codesign "${SIGN_FLAGS[@]}" -s "$IDENT" --entitlements "$HERE/entitlements/shell.plist" "$APP"

if codesign --verify --deep --strict --verbose=2 "$APP" 2>/dev/null; then
  echo "  ✓ signature verifies"
else
  echo "  ⚠ signature verify reported issues (review before distributing)"
  [ "$RELEASE" -eq 1 ] && exit 1
fi

SIZE="$(du -sh "$APP" | cut -f1)"
echo "✓ Built (staged) $APP  ($SIZE)"

# ---------------------------------------------------------------------------
# 5. Optional .dmg (drag-to-Applications) — built from the clean staged .app.
# ---------------------------------------------------------------------------
mkdir -p "$DIST"
if [ "$MAKE_DMG" -eq 1 ]; then
  echo "▸ Building .dmg…"
  DMGSTAGE="$STAGE/dmgroot"; rm -rf "$DMGSTAGE"; mkdir -p "$DMGSTAGE"
  cp -R "$APP" "$DMGSTAGE/"
  ln -s /Applications "$DMGSTAGE/Applications"
  TMP_DMG="$STAGE/$APP_NAME.dmg"; rm -f "$TMP_DMG"
  hdiutil create -volname "$APP_NAME" -srcfolder "$DMGSTAGE" -ov -format UDZO "$TMP_DMG" >/dev/null
  if [ "$RELEASE" -eq 1 ]; then
    # Sign the disk image itself, then notarize + staple it. The .app inside
    # was already signed; stapling the .dmg also staples the app it carries
    # (Apple records both), so Gatekeeper is happy online AND offline.
    codesign --force --timestamp -s "$SIGN_IDENTITY" "$TMP_DMG"
    echo "▸ Notarizing (this waits on Apple, typically 1–10 min)…"
    xcrun notarytool submit "$TMP_DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$TMP_DMG"
    xcrun stapler validate "$TMP_DMG"
    echo "▸ Gatekeeper assessment of the notarized app:"
    MNT="$(mktemp -d)"; hdiutil attach "$TMP_DMG" -mountpoint "$MNT" -nobrowse -quiet
    spctl -a -vv -t exec "$MNT/$APP_NAME.app" 2>&1 | sed 's/^/  /' || true
    hdiutil detach "$MNT" -quiet
  fi
  cp -f "$TMP_DMG" "$DIST/$APP_NAME.dmg"
  echo "✓ Built $DIST/$APP_NAME.dmg  ($(du -sh "$DIST/$APP_NAME.dmg" | cut -f1))"
fi

# Copy the signed .app into dist/ for local inspection/launch. (dist/ is on the
# iCloud Desktop so this copy re-acquires file-provider xattrs and won't pass
# `codesign --verify --strict`, but it still launches; the canonical, cleanly
# sealed copy is the one inside the .dmg.)
echo "▸ Copying .app to dist/…"
rm -rf "$DIST/$APP_NAME.app"
ditto "$APP" "$DIST/$APP_NAME.app"
echo "✓ $DIST/$APP_NAME.app"
