# TokenAnalytics — macOS Installer

A double-clickable, **unsigned** installer for TokenAnalytics: a local token + cost
monitoring dashboard for Claude, Codex, Gemini and other coding agents.

## Install

1. Unzip `TokenAnalytics-macOS.zip`.
2. **Right-click** `install.command` → **Open** (see *Gatekeeper* below — a plain
   double-click will be blocked the first time because the installer is unsigned).
3. Click **Open** in the confirmation dialog. A Terminal window runs the installer.

The installer will:

- Check prerequisites: **Node.js 18+**, **npm**, **git**, **Python 3.9+** (and tell
  you exactly what's missing, with a download link, if any are).
- Install the app into `~/.tokenanalytics/app` (`git clone` on first run,
  `git pull` on later runs).
- Install a **LaunchAgent** at `~/Library/LaunchAgents/com.tokenanalytics.app.plist`
  so TokenAnalytics starts automatically at login.
- Start it now and open the dashboard at <http://localhost:3000>.

It is **idempotent** — safe to run again to update.

## Uninstall

Right-click `uninstall.command` → **Open**. It:

- Unloads and removes the LaunchAgent.
- Stops the running app.
- Removes `~/.tokenanalytics/app`.
- **Keeps your data** in `~/.tokenanalytics` (config JSON, `history.db`, logs).

To fully purge everything (including your data), run manually:

```bash
rm -rf ~/.tokenanalytics
```

## Gatekeeper (unsigned app)

Because this build is not code-signed or notarized, macOS Gatekeeper blocks a normal
double-click the first time. Use one of these:

- **Right-click → Open**, then click **Open** in the dialog. macOS remembers your
  choice for that file afterward.
- Or: **System Settings → Privacy & Security**, scroll to the security section, and
  click **Open Anyway** after the first blocked attempt.

This is expected for v1; signing/notarization is not included.

## Where your data lives

All data is stored under **`~/.tokenanalytics`**:

- `~/.tokenanalytics/app` — the installed application (managed; removed on uninstall).
- `~/.tokenanalytics/*.json`, `~/.tokenanalytics/history.db` — your config and history.
- `~/.tokenanalytics/logs/` — `tokenanalytics.out.log` and `tokenanalytics.err.log`.

Your data **survives uninstall and reinstall** — only the `app/` subfolder is managed.

## Changing the port

The dashboard defaults to **<http://localhost:3000>**. To run on a different port,
set the relevant environment variable (e.g. `PORT`) before launch. The LaunchAgent
plist controls how it starts at login — edit
`~/Library/LaunchAgents/com.tokenanalytics.app.plist`, add a `PORT` entry under
`EnvironmentVariables`, then reload it:

```bash
launchctl unload ~/Library/LaunchAgents/com.tokenanalytics.app.plist
launchctl load   ~/Library/LaunchAgents/com.tokenanalytics.app.plist
```

If you change the port, open `http://localhost:<your-port>` instead of `:3000`.

## Troubleshooting

- **"Node.js 18+ is required"** — install/update Node from
  <https://nodejs.org/en/download> (or `brew install node`) and re-run.
- **"Python 3.9+ is not installed"** — install from
  <https://www.python.org/downloads/macos/> (or `brew install python`) and re-run.
- **"git clone failed"** — check your network/proxy; confirm
  `https://github.com/2netgr/tokenanalytics.git` is reachable, then re-run.
- **Dashboard didn't open** — check the logs:
  `~/.tokenanalytics/logs/tokenanalytics.err.log`. Confirm nothing else is using
  port 3000.
- **Is it running?** — `launchctl list | grep com.tokenanalytics.app`.
- **Restart it** — unload then load the LaunchAgent (see *Changing the port* above).

## Notes

- The upstream GitHub repository is still named **`tokentelemetry`**
  (`2netgr/tokenanalytics`); the product is branded **TokenAnalytics**.
- This installer uses bash + a LaunchAgent plist only. No signing/notarization (v1).
