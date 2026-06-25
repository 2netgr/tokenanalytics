#!/usr/bin/env bash
#
# TokenAnalytics — macOS uninstaller (v1)
#
# Removes the managed app + LaunchAgent. LEAVES all user data in
# ~/.tokenanalytics intact (JSON config, history.db, logs, etc.).

set -euo pipefail

DATA_DIR="${HOME}/.tokenanalytics"
APP_DIR="${DATA_DIR}/app"
LOG_DIR="${DATA_DIR}/logs"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_LABEL="com.tokenanalytics.app"
PLIST_DEST="${LAUNCH_AGENTS_DIR}/${PLIST_LABEL}.plist"

if [[ -t 1 ]]; then
    BOLD="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"
    GREEN="$(printf '\033[32m')"; YELLOW="$(printf '\033[33m')"
    RED="$(printf '\033[31m')"; CYAN="$(printf '\033[36m')"
    RESET="$(printf '\033[0m')"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; RESET=""
fi

info() { printf '%s\n' "${CYAN}==>${RESET} $*"; }
ok()   { printf '%s\n' "${GREEN}  ✓${RESET} $*"; }
warn() { printf '%s\n' "${YELLOW}  !${RESET} $*"; }

printf '%s\n\n' "${BOLD}${CYAN}TokenAnalytics — uninstall${RESET}"

# --------------------------------------------------------------------------
# 1. Unload + remove the LaunchAgent
# --------------------------------------------------------------------------
info "Stopping and removing the LaunchAgent"
if launchctl list "${PLIST_LABEL}" >/dev/null 2>&1; then
    launchctl unload "${PLIST_DEST}" >/dev/null 2>&1 || true
    ok "Unloaded ${PLIST_LABEL}"
else
    ok "LaunchAgent not loaded (nothing to unload)"
fi

if [[ -f "${PLIST_DEST}" ]]; then
    rm -f "${PLIST_DEST}"
    ok "Removed ${PLIST_DEST}"
else
    ok "No LaunchAgent plist present"
fi

# --------------------------------------------------------------------------
# 2. Stop any still-running instance launched from the app dir
# --------------------------------------------------------------------------
info "Stopping any running TokenAnalytics process"
# Match node processes running this app's bin/cli.js. Best-effort; ignore errors.
if pkill -f "${APP_DIR}/bin/cli.js" >/dev/null 2>&1; then
    ok "Stopped running process(es)"
else
    ok "No matching running process"
fi

# --------------------------------------------------------------------------
# 3. Remove ONLY the managed app dir — never the data.
# --------------------------------------------------------------------------
info "Removing the installed app (app dir only)"
if [[ -d "${APP_DIR}" ]]; then
    rm -rf "${APP_DIR}"
    ok "Removed ${APP_DIR}"
else
    ok "App dir already absent"
fi

# --------------------------------------------------------------------------
# 4. Report what we kept + how to fully purge
# --------------------------------------------------------------------------
printf '\n%s\n' "${GREEN}${BOLD}TokenAnalytics uninstalled.${RESET}"
printf '\n%s\n' "${BOLD}Your data was kept${RESET} in:"
printf '%s\n' "  ${DATA_DIR}"

if [[ -d "${DATA_DIR}" ]]; then
    # Show what remains (excluding the now-removed app/), without failing if empty.
    remaining="$(cd "${DATA_DIR}" 2>/dev/null && ls -A 2>/dev/null | grep -v '^app$' || true)"
    if [[ -n "${remaining}" ]]; then
        printf '%s\n' "${DIM}  Remaining items:${RESET}"
        while IFS= read -r item; do
            [[ -n "${item}" ]] && printf '%s\n' "    - ${item}"
        done <<< "${remaining}"
    fi
fi

printf '\n%s\n' "This includes your config, ${BOLD}history.db${RESET}, JSON files, and logs (${LOG_DIR})."
printf '\n%s\n' "${YELLOW}To fully purge ALL TokenAnalytics data, run this manually:${RESET}"
printf '%s\n\n' "  ${BOLD}rm -rf \"${DATA_DIR}\"${RESET}"
