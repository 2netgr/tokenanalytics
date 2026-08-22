# Changelog

All notable changes to TokenAnalytics will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.6.2] - 2026-08-22

### Changed
- **The macOS app is now signed with a Developer ID and notarized by Apple.** A downloaded `TokenAnalytics.dmg` opens with a plain double-click — no more "unidentified developer" / right-click → Open step. `desktop/build.sh --release` signs every bundled binary (CPython, Node, native addons) under the hardened runtime, notarizes the disk image and staples the ticket.

## [1.6.1] - 2026-06-25

### Changed
- **State now lives under `~/.tokenanalytics`.** On first launch, if a legacy `~/.tokentelemetry` exists but `~/.tokenanalytics` does not (and no path is pinned via env), the app copies the data into the new home so everything lives under the new brand. The legacy dir is left intact as a **backup** — nothing is moved or deleted — and the copy is WAL-checkpointed for a consistent SQLite snapshot. (Previously the legacy dir was merely adopted in place.) Implemented as `tt_paths.migrate_legacy_data_dir()`, run from `bin/cli.js` before the backend touches the data dir.

---

## [1.6.0] - 2026-06-25

### Added
- **One-click "Update now".** The update banner now has an **Update now** button that pulls the latest code (`POST /update/apply`, loopback-only) and restarts the app automatically (via the LaunchAgent's KeepAlive), then reloads once the new version is live — no terminal needed. The manual `git pull && ./start.sh` copy button was removed. **"What's changed"** still opens the in-app popup of highlights.

### Fixed
- The update check pointed at the old upstream repo. `_REPO_OWNER`/`_REPO_NAME` are now `2netgr`/`tokenanalytics`, so the banner's "behind" detection and changelog highlights track this repo.

---

## [1.5.0] - 2026-06-25

### Added
- **Automatic bidirectional multi-device sync.** A device now keeps itself fully in sync with a hub: it both **pushes** its own rollups and **pulls** every other device's, so all devices mirror the same "all devices" view (star topology — one always-on hub, peers push+pull). New endpoints `GET /sync/pull` (incremental, watermarked), `GET`/`POST /sync/config`, `POST /sync/now`, `GET /sync/status`; an in-app background sync worker (`sync_worker.py`) that auto-runs when configured (settings persisted in `sync.json`); and a **"Multi-device sync" settings panel** to pair a device to a hub (URL + token) with live status — no CLI needed. Verified end-to-end between two running instances (push and pull both confirmed; a peer mirrored the hub's data and the hub received the peer's).

### Fixed
- Sync/collector rollups now strip the `tokens` dict to the four integer counts before sending. Live-scan token dicts also carry `cost` (float), `estimated`, `cache_creation`, etc., which the hub's strict `tokens: Dict[str, int]` schema rejected with HTTP 422 — this also fixes the existing one-way collector. Regression test added.

---

## [1.4.0] - 2026-06-25

### Added
- **Cursor token estimation.** Cursor's local transcripts carry no usage fields (so Cursor sessions showed 0 tokens), but they do record the model (e.g. `composer-2.5-fast`). The scanner now estimates tokens from transcript text (~chars/4, flagged `estimated`) and prices them with Cursor Composer rates — Composer 2.5: $0.50 input / $2.50 output / $0.20 cache-read per million ([source](https://cursor.com/docs/models-and-pricing)). On a real store this turned Cursor from $0 into ~214K tokens / $0.47. Estimates are approximate (input excludes the context Cursor does not log).
- **"Include subagents" toggle.** `GET /analytics?include_delegated=true` (and a switch on the Analytics page) folds each session's delegated/subagent tokens into the headline totals; default keeps them in the separate delegation bucket (e.g. Claude 79.9M → 87.3M with subagents included).

---

## [1.3.0] - 2026-06-25

### Changed
- **No more phantom sessions.** The live scanner used to fabricate a zero-token "session" for every id in `~/.claude/history.jsonl` whose transcript Claude had already pruned — inflating the session count to ~27,000 when only ~64 real transcripts existed on disk. The scan now reports only sessions that still have a transcript. All-time history is preserved instead by the durable store (`history.db`), which keeps every captured rollup forever (ADR-0002): with the app running continuously (the LaunchAgent), each session is captured within Claude's retention window and kept permanently — no longer "just the last 30 days".
- **History store v3 migration:** a one-time cleanup deletes existing fileless phantom rollups (zero tokens, no model, no transcript, no summary); rows that captured real usage are kept. On a real store this dropped the Claude session count from ~27,021 to ~64 with token totals unchanged (79.8M).

### Notes
- Subagent/delegated tokens are captured in each session's separate "delegation" bucket (visible in the delegation view), by design distinct from the headline per-session total.

---

## [1.2.1] - 2026-06-25

### Fixed
- **Token undercounting for Claude (and latent Codex).** `_scan_sessions_sync` parsed tokens for only the 100 most-recent sessions; because `~/.claude/history.jsonl` injects a fileless zero-token stub per known session id, real transcripts were ranked past the top 100 and their usage was never read — recording multi-million-token sessions as 0. Now **every file-backed transcript is parsed**. On a real store this lifted the Claude total from ~3.4M to ~79.6M tokens and restored per-model attribution (claude-sonnet-4-6, claude-opus-4-8, claude-fable-5, claude-opus-4-7). The identical 100-session cap on the Codex path was removed too. Covered by `backend/test_scan_token_capture.py`.

### Known limitations
- Cursor and Copilot local transcripts contain no token-usage fields, so those sessions still show 0 tokens — the scanner deliberately does not fabricate estimates.
- `~/.claude/history.jsonl` still emits one zero-token row per pruned session, inflating the session *count*; this is cosmetic and does not affect token totals.

---

## [1.2.0] - 2026-06-25

### Removed
- **Product telemetry removed entirely.** No usage events are collected or transmitted: `telemetry.enabled()` is hard-disabled, `emit()` is a guaranteed no-op, the first-run telemetry notice no longer appears, and the Cloudflare Worker sink (`proxy/`) was deleted. The app makes no analytics calls to any external server. (The separate, opt-out update check and pricing sync are unaffected.)

---

## [1.1.0] - 2026-06-24

### Added
- **Multi-device analytics (local hub sync).** One Mac runs the dashboard as a hub; secondary Macs run a new `collector` mode (`tokenanalytics collector --hub-url <URL> --auth-token <T>`) that scans locally and syncs **summary rollups only** — never prompt/output/transcript text. New endpoints: `GET /devices`, `POST /devices/register`, `POST /sync/sessions`.
- **Device selector** on the Analytics and Dashboard pages: view the local device, all devices, or a single device. Defaults to the local device, so the standalone experience is unchanged.
- Per-install **device identity** (stable `device_id`, hostname-based `device_name`, `device_role` of local/hub/collector), persisted to `device.json`.
- Unsigned **macOS installer** deliverable (`goal/TokenAnalytics-macOS.zip`): double-clickable `install.command`/`uninstall.command`, a `com.tokenanalytics.app.plist` LaunchAgent, and a README. Uninstall preserves user data.
- ADR-0004 documenting the multi-device trust boundary ("no third-party network") and the history-store primary-key change; a `CONTEXT.md` glossary.

### Changed
- **Rebranded TokenTelemetry → TokenAnalytics** across the dashboard UI, CLI output/help, website, docs, and the Hermes plugin metadata.
- The CLI command is now `tokenanalytics` (the old `tokentelemetry` command is kept as a backward-compatible alias).
- The default data directory is now `~/.tokenanalytics` (env: `TOKENANALYTICS_DATA_DIR` / `TOKENANALYTICS_HOME`). An existing `~/.tokentelemetry` directory and the legacy `TOKENTELEMETRY_*` env vars are still honoured automatically — upgrading users keep their data in place with zero migration.
- The durable-history `sessions`/`transcripts`/`summaries` primary key widened to `(device_id, agent, id)` so two devices that share an agent session id never overwrite each other. Existing `history.db` files migrate in place and losslessly on first run; every prior row is stamped as the local device.

### Notes
- All source/install URLs were repointed to `github.com/2netgr/tokenanalytics` and the `tokenanalytics.app` domain. Internal identifiers were intentionally **not** renamed to avoid breakage: the `TT_*` runtime env vars, the `tt_paths` module, and the `--tt-*` CSS tokens are unchanged.

---

## [1.0.0] - 2026-04-27

### Added
- Initial public release of TokenAnalytics
- Local observability dashboard for AI coding agents
- Support for 9 agents: Claude Code, Gemini CLI, Codex, Cursor, GitHub Copilot, Qwen, OpenCode, Vibe, Antigravity
- Real-time token usage tracking and cost estimates
- Session trace waterfall with reasoning + tool call breakdown
- Per-project insights: heatmaps, model leaderboards, agent distribution
- Analytics: cumulative token usage per agent/model over time
- Plans view for captured plan-mode outputs
- FastAPI backend + Next.js frontend
- One-command install via `install.sh` (macOS/Linux) and `start.bat` (Windows)
- 100% local — no signup, no cloud, no telemetry
- MIT open source license
- Website at https://tokenanalytics.app
