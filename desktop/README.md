# TokenAnalytics — macOS desktop app

A self-contained `TokenAnalytics.app` that bundles its own Python (FastAPI
backend) and Node (Next.js frontend) and shows the dashboard in a native window.
No prerequisites on the user's Mac, no Terminal, no manual ports.

## How it works

```
TokenAnalytics.app/Contents/
├── MacOS/TokenAnalytics            # native Swift WKWebView shell (desktop/shell/main.swift)
└── Resources/
    ├── runtime/python/             # relocatable CPython 3.12 + backend deps
    ├── runtime/node                # node binary (runs the Next standalone server)
    ├── app/backend/                # FastAPI source
    └── app/frontend/               # Next.js `output: standalone` build
```

On launch the shell:

1. picks two **free loopback ports** (so a busy 3000/8000 is never a problem);
2. starts the bundled backend (`python3 main.py --port <api>`) and frontend
   (`node server.js`, `PORT=<front>`);
3. shows a splash, waits until the dashboard responds, then loads
   `http://localhost:<front>/?apiport=<api>` in the window — the `?apiport`
   tells the client the live backend port at runtime (Next bakes
   `NEXT_PUBLIC_API_PORT` at build time, which can't know an auto-picked port);
4. on quit — including Force Quit / crash — reaps both child processes (a clean
   quit tears them down directly; a non-clean exit is cleaned up on next launch
   via a recorded pid file + `pkill`).

The app runs **fully offline**: `TT_NO_UPDATE_CHECK=1` is set, so the backend
makes no outbound calls.

## Build

```bash
desktop/build.sh            # → desktop/dist/TokenAnalytics.app
desktop/build.sh --dmg      # → also desktop/dist/TokenAnalytics.dmg (drag-to-Applications)
desktop/build.sh --skip-frontend   # reuse an existing frontend/.next/standalone build
```

Requirements on the **build** machine (not the user's): macOS on Apple Silicon,
Xcode command-line tools (`swiftc`, `codesign`, `hdiutil`), Node, and network
access (the first build downloads the pinned CPython + Node into
`desktop/runtime/`, which is gitignored).

### Notes

- **Default signing is ad-hoc** (dev builds). Releases use `build.sh --release`:
  Developer ID Application identity, hardened runtime, secure timestamp on every
  nested Mach-O (`desktop/entitlements/*.plist`), then `notarytool submit --wait`
  and `stapler staple` on the .dmg. Needs the certificate in the keychain and a
  notarytool keychain profile (`tokenanalytics-notary`, App Store Connect API key).
- The build assembles and signs under `/private/tmp`, **never** in the repo: the
  repo lives on an iCloud-synced Desktop whose file-provider xattrs make
  `codesign` reject the bundle. Final artifacts are copied back to
  `desktop/dist/`.
- Target is **arm64 only** for now. A universal build would bundle both-arch
  Python + Node and a `lipo`'d shell.
