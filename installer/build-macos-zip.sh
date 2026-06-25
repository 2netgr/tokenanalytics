#!/usr/bin/env bash
#
# Build the TokenAnalytics macOS installer deliverable:
#   goal/TokenAnalytics-macOS.zip
#
# - Marks the .command scripts executable.
# - Zips the contents of installer/macos/ preserving the executable bit.

set -euo pipefail

# Resolve repo-relative paths from this script's own location, so it works
# regardless of the caller's CWD.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd -P)"

SRC_DIR="${SCRIPT_DIR}/macos"
OUT_DIR="${REPO_ROOT}/goal"
ZIP_PATH="${OUT_DIR}/TokenAnalytics-macOS.zip"

[[ -d "${SRC_DIR}" ]] || { echo "error: source dir not found: ${SRC_DIR}" >&2; exit 1; }

echo "==> Marking .command scripts executable"
chmod +x "${SRC_DIR}"/install.command "${SRC_DIR}"/uninstall.command
ls -l "${SRC_DIR}"/*.command

echo "==> Preparing output dir: ${OUT_DIR}"
mkdir -p "${OUT_DIR}"

# Start fresh so re-runs don't append into a stale archive.
rm -f "${ZIP_PATH}"

echo "==> Zipping installer/macos/ -> ${ZIP_PATH}"
# Zip from inside SRC_DIR so archive entries are flat (no installer/macos/ prefix).
# -X strips extra Finder attrs; zip preserves the unix exec bit by default.
(
    cd "${SRC_DIR}"
    zip -X -r "${ZIP_PATH}" \
        install.command \
        uninstall.command \
        README.md \
        com.tokenanalytics.app.plist
)

echo "==> Done. Built: ${ZIP_PATH}"
echo
echo "Contents:"
unzip -l "${ZIP_PATH}"
