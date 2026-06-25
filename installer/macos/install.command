#!/usr/bin/env bash
#
# TokenAnalytics — macOS installer (unsigned, v1)
#
# - Checks prerequisites (Node >= 18, npm, git, Python 3.9+)
# - Installs / updates the app into ~/.tokenanalytics/app via git
# - Installs a LaunchAgent so it starts at login
# - Starts it now and opens the dashboard
#
# Idempotent. NEVER touches user data in ~/.tokenanalytics (only app/).

set -euo pipefail

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
REPO_URL="https://github.com/2netgr/tokenanalytics.git"
DATA_DIR="${HOME}/.tokenanalytics"
APP_DIR="${DATA_DIR}/app"
LOG_DIR="${DATA_DIR}/logs"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_LABEL="com.tokenanalytics.app"
PLIST_DEST="${LAUNCH_AGENTS_DIR}/${PLIST_LABEL}.plist"
DASHBOARD_URL="http://localhost:3000"

# Directory this script lives in (so we can find the bundled plist template).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
PLIST_TEMPLATE="${SCRIPT_DIR}/${PLIST_LABEL}.plist"

# --------------------------------------------------------------------------
# Pretty output helpers
# --------------------------------------------------------------------------
if [[ -t 1 ]]; then
    BOLD="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"
    GREEN="$(printf '\033[32m')"; YELLOW="$(printf '\033[33m')"
    RED="$(printf '\033[31m')"; CYAN="$(printf '\033[36m')"
    RESET="$(printf '\033[0m')"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; CYAN=""; RESET=""
fi

info()  { printf '%s\n' "${CYAN}==>${RESET} $*"; }
ok()    { printf '%s\n' "${GREEN}  ✓${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}  !${RESET} $*"; }
fail()  { printf '%s\n' "${RED}  ✗ $*${RESET}" >&2; exit 1; }

banner() {
    printf '%s\n' "${BOLD}${CYAN}"
    cat <<'BANNER'
  ╔════════════════════════════════════════════════╗
  ║                                                ║
  ║          T O K E N   A N A L Y T I C S         ║
  ║                                                ║
  ║      Local token + cost monitoring for         ║
  ║      Claude, Codex, Gemini & other agents      ║
  ║                                                ║
  ╚════════════════════════════════════════════════╝
BANNER
    printf '%s\n' "${RESET}"
    printf '%s\n\n' "${DIM}  macOS installer (unsigned) — v1${RESET}"
}

# --------------------------------------------------------------------------
# Prerequisite checks
# --------------------------------------------------------------------------

# Compare two dotted versions: returns 0 if $1 >= $2.
version_ge() {
    # Uses sort -V; portable enough for x.y.z comparisons.
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

check_prereqs() {
    info "Checking prerequisites"

    # --- Node.js >= 18 ---
    if ! command -v node >/dev/null 2>&1; then
        fail "Node.js is not installed. Install Node 18+ from https://nodejs.org/en/download (or 'brew install node'), then re-run this installer."
    fi
    local node_raw node_ver
    node_raw="$(node --version 2>/dev/null || true)"   # e.g. v20.11.1
    node_ver="${node_raw#v}"
    if [[ -z "${node_ver}" ]] || ! version_ge "${node_ver}" "18.0.0"; then
        fail "Node.js 18+ is required (found '${node_raw:-none}'). Update from https://nodejs.org/en/download then re-run."
    fi
    ok "Node.js ${node_ver}"

    # --- npm ---
    if ! command -v npm >/dev/null 2>&1; then
        fail "npm is not installed (normally ships with Node.js). Install Node 18+ from https://nodejs.org/en/download then re-run."
    fi
    ok "npm $(npm --version 2>/dev/null || echo '?')"

    # --- git ---
    if ! command -v git >/dev/null 2>&1; then
        fail "git is not installed. Install the Xcode Command Line Tools with 'xcode-select --install' or get git from https://git-scm.com/download/mac then re-run."
    fi
    ok "git $(git --version 2>/dev/null | awk '{print $3}')"

    # --- Python 3.9+ ---
    local py_bin=""
    if command -v python3 >/dev/null 2>&1; then
        py_bin="python3"
    elif command -v python >/dev/null 2>&1; then
        py_bin="python"
    fi
    if [[ -z "${py_bin}" ]]; then
        fail "Python 3.9+ is not installed. Install from https://www.python.org/downloads/macos/ (or 'brew install python'), then re-run."
    fi
    local py_ver
    py_ver="$("${py_bin}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "")"
    if [[ -z "${py_ver}" ]] || ! version_ge "${py_ver}" "3.9.0"; then
        fail "Python 3.9+ is required (found '${py_ver:-none}'). Install from https://www.python.org/downloads/macos/ then re-run."
    fi
    ok "Python ${py_ver}"

    # Remember the absolute node path for the LaunchAgent.
    NODE_BIN="$(command -v node)"
}

# --------------------------------------------------------------------------
# App install / update (git). Only ever manages ${APP_DIR}.
# --------------------------------------------------------------------------
install_or_update_app() {
    info "Installing TokenAnalytics app into ${APP_DIR}"

    # Create the data dir and logs dir but NEVER clobber existing user data.
    mkdir -p "${DATA_DIR}" "${LOG_DIR}"

    if [[ -d "${APP_DIR}/.git" ]]; then
        info "Existing install found — updating (git pull)"
        if git -C "${APP_DIR}" pull --ff-only >/dev/null 2>&1; then
            ok "Updated to latest"
        else
            warn "git pull --ff-only failed (local changes or diverged history); keeping the current checkout."
        fi
    elif [[ -e "${APP_DIR}" ]]; then
        # Path exists but is not a git checkout. Don't destroy anything — bail loudly.
        fail "${APP_DIR} exists but is not a git checkout. Move or remove it manually, then re-run. (Your data in ${DATA_DIR} is untouched.)"
    else
        info "Cloning ${REPO_URL}"
        if git clone --depth 1 "${REPO_URL}" "${APP_DIR}" >/dev/null 2>&1; then
            ok "Cloned"
        else
            fail "git clone failed. Check your network/proxy and that ${REPO_URL} is reachable, then re-run."
        fi
    fi
}

# --------------------------------------------------------------------------
# One-time data migration (TokenTelemetry → TokenAnalytics). MUST run before we
# create ~/.tokenanalytics/app: that mkdir makes ~/.tokenanalytics exist, after
# which the app resolves its data dir there — abandoning a legacy ~/.tokentelemetry.
# So if this machine has legacy data and no ~/.tokenanalytics yet, copy the data
# into the new home first. The legacy dir is kept as a backup — nothing is moved
# or deleted.
# --------------------------------------------------------------------------
migrate_legacy_data() {
    local legacy="${HOME}/.tokentelemetry"
    [[ -d "${legacy}" && ! -e "${DATA_DIR}" ]] || return 0
    info "Found existing data in ~/.tokentelemetry — migrating to ~/.tokenanalytics"
    if command -v sqlite3 >/dev/null 2>&1; then
        for db in "${legacy}"/*.db; do
            [[ -e "${db}" ]] && sqlite3 "${db}" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
        done
    fi
    if cp -R "${legacy}" "${DATA_DIR}"; then
        ok "Migrated your data (the original ~/.tokentelemetry is kept as a backup)"
    else
        warn "Could not migrate ~/.tokentelemetry automatically; starting fresh in ~/.tokenanalytics."
    fi
}

# --------------------------------------------------------------------------
# LaunchAgent install / refresh
# --------------------------------------------------------------------------
install_launch_agent() {
    info "Installing LaunchAgent ${PLIST_LABEL}"

    [[ -f "${PLIST_TEMPLATE}" ]] || fail "Bundled plist template not found at ${PLIST_TEMPLATE}"

    mkdir -p "${LAUNCH_AGENTS_DIR}"

    # If already loaded, unload first so we can refresh the definition cleanly.
    if launchctl list "${PLIST_LABEL}" >/dev/null 2>&1; then
        launchctl unload "${PLIST_DEST}" >/dev/null 2>&1 || true
    fi

    # Render the template -> destination, substituting absolute paths.
    # Use a sed delimiter (|) that won't appear in our paths.
    sed \
        -e "s|__HOME__|${HOME}|g" \
        -e "s|__APP_DIR__|${APP_DIR}|g" \
        -e "s|__NODE_BIN__|${NODE_BIN}|g" \
        "${PLIST_TEMPLATE}" > "${PLIST_DEST}"

    ok "Wrote ${PLIST_DEST}"

    if launchctl load "${PLIST_DEST}" >/dev/null 2>&1; then
        ok "Loaded LaunchAgent (will start at login)"
    else
        warn "launchctl load reported an issue; the app may already be loaded. Continuing."
    fi
}

# --------------------------------------------------------------------------
# Start now + open browser
# --------------------------------------------------------------------------
start_now() {
    info "Starting TokenAnalytics now"

    # If the LaunchAgent already started it, great. Otherwise kick it off
    # directly in the background so the user doesn't have to log out/in.
    if ! curl -fsS --max-time 1 "${DASHBOARD_URL}" >/dev/null 2>&1; then
        (
            cd "${APP_DIR}"
            nohup "${NODE_BIN}" bin/cli.js \
                >>"${LOG_DIR}/tokenanalytics.out.log" \
                2>>"${LOG_DIR}/tokenanalytics.err.log" &
        ) || warn "Could not background-launch directly; the LaunchAgent should still start it."
    fi

    # Wait briefly for the server to come up (best-effort, ~10s max).
    local i
    for i in $(seq 1 20); do
        if curl -fsS --max-time 1 "${DASHBOARD_URL}" >/dev/null 2>&1; then
            ok "Dashboard is responding"
            break
        fi
        sleep 0.5
    done

    info "Opening ${DASHBOARD_URL}"
    open "${DASHBOARD_URL}" >/dev/null 2>&1 || warn "Could not auto-open the browser; visit ${DASHBOARD_URL} manually."
}

# --------------------------------------------------------------------------
# Final summary
# --------------------------------------------------------------------------
print_summary() {
    printf '\n%s\n' "${GREEN}${BOLD}TokenAnalytics is installed.${RESET}"
    printf '\n'
    printf '%s\n' "  Dashboard : ${BOLD}${DASHBOARD_URL}${RESET}"
    printf '%s\n' "  App dir   : ${APP_DIR}"
    printf '%s\n' "  Your data : ${DATA_DIR}  ${DIM}(JSON, history.db, logs — never touched by install/uninstall)${RESET}"
    printf '%s\n' "  Logs      : ${LOG_DIR}/tokenanalytics.{out,err}.log"
    printf '\n'
    printf '%s\n' "  Starts automatically at login (LaunchAgent ${PLIST_LABEL})."
    printf '\n'
    printf '%s\n' "  To uninstall, run ${BOLD}uninstall.command${RESET} from this folder."
    printf '%s\n' "  ${DIM}(It removes ${APP_DIR} and the LaunchAgent but keeps your data.)${RESET}"
    printf '\n'
}

# --------------------------------------------------------------------------
main() {
    banner
    check_prereqs
    migrate_legacy_data
    install_or_update_app
    install_launch_agent
    start_now
    print_summary
}

main "$@"
