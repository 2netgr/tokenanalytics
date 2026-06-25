# CONTEXT — Glossary

The canonical language of TokenAnalytics (formerly TokenTelemetry). This file is a
glossary only — no implementation details. When code or docs use one of these terms,
they mean exactly what is written here.

## Identity & topology

- **Device** — one Mac install of TokenAnalytics, identified by a stable `device_id`
  generated once per install. A device also has a human-readable `device_name`
  (defaults to the macOS hostname).
- **device_role** — the part a device plays: `local` (a standalone install, no sync),
  `hub` (runs the dashboard/API and aggregates other devices), or `collector` (scans
  locally and uploads to a hub, no UI of its own).
- **Hub** — the one device whose role is `hub`. It serves the dashboard and stores both
  its own local sessions and the rollups synced from collectors.
- **Collector** — a device whose role is `collector`. It scans its own agent logs and
  sends **session rollups** to the hub on an interval. It never serves a dashboard.

## Data that moves

- **Session rollup** — the core, always-kept record of a single coding-agent session:
  raw token/cost facts (input/output/cached/total, cost, model, timestamps, and the
  small ecosystem summary). This is the *only* shape that crosses the network during
  sync. It carries **no prompt text, no model output, and no transcript**.
  > Note: this is the same "core rollup" tier introduced for durable history
  > (see [ADR-0002](docs/adr/0002-durable-history-rollup.md)). "Session rollup" names
  > that tier when it travels between devices, to keep it distinct from the two tiers
  > that never travel: **transcripts** (tier 2) and **summaries** (tier 3).
- **source_origin** — how a stored session arrived on the device reading it:
  `local_scan` (this device scanned it from disk) or `remote_sync` (a collector sent it).

## Trust boundary

- **Trust boundary** — the user's own devices, reachable over the user's own network
  (LAN or Tailscale). The local-first principle is **"no third-party network,"** not
  "no network": session rollups may travel between the user's own Macs, authenticated,
  but never to any external/cloud service. See [ADR-0004](docs/adr/0004-multi-device-sync-trust-boundary.md).
