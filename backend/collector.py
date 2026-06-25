#!/usr/bin/env python3
"""Collector mode: scan local coding-agent logs, sync rollups to a hub (ADR-0004).

A *collector* is a secondary Mac that scans its own coding-agent logs locally and
periodically pushes **summary rollups only** to a primary "hub" Mac that serves the
dashboard. This is the same trust boundary ADR-0004 draws: session data may travel
*between the user's own devices* on the user's own LAN/Tailscale, never to any
external service, and only as raw token/cost *facts* — never transcripts, prompts,
or output text. See ``docs/adr/0004-multi-device-sync-trust-boundary.md``.

Privacy policy — ROLLUPS ONLY:
  Each scanned session is reduced to a flat rollup carrying only the keys the hub's
  ``SessionRollupIn`` model accepts (it uses ``extra='forbid'``):

      agent, id, project, model, provider, endpoint, billing_mode,
      timestamp (ISO string), tokens {input, output, cached, total},
      cost, tok_per_sec

  Everything else the local scan produces (display text, plans, mcp_tools,
  artifacts, child/parent ids, …) is dropped here, at the source, so nothing but
  the summary facts ever crosses the network. Sending a session with any extra key
  would be rejected by the hub with HTTP 422 — by design.

Process model:
  This runs as a standalone, stdlib-only process (no new pip deps; ``urllib`` +
  ``json``). Importing ``main`` to reuse ``_scan_sessions_sync`` is heavyweight but
  fine here. The default service (the dashboard) is untouched — collector mode is
  opt-in and only ever *reads* local logs and *posts* rollups outward.

Config (environment):
  TA_HUB_URL      base URL of the hub, e.g. http://hub.tailnet.ts.net:8000 (required)
  TA_AUTH_TOKEN   bearer token the hub requires for non-loopback callers (optional
                  for a loopback/insecure hub, required otherwise)
  TA_INTERVAL     seconds between scans (default 60)

On start it sets this device's role to "collector", registers once with the hub,
then loops forever: scan → map to rollups → POST /sync/sessions. The hub being
unreachable or returning a non-200 is logged and the loop continues; Ctrl+C exits
cleanly.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import device_identity

# The exact set of keys the hub's SessionRollupIn model accepts. Any other key
# trips its extra='forbid' guard (HTTP 422), so we build rollups from this list
# alone and never pass the scan dict through verbatim.
_ROLLUP_KEYS = (
    "agent",
    "id",
    "project",
    "model",
    "provider",
    "endpoint",
    "billing_mode",
    "timestamp",
    "tokens",
    "cost",
    "tok_per_sec",
)

_DEFAULT_INTERVAL = 60


def _log(msg: str) -> None:
    """Timestamped status line on stdout (line-buffered so logs stream live)."""
    print(f"[collector {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _iso(ts):
    """Normalize a scan timestamp to an ISO-8601 string (or None).

    The local scan yields ``timestamp`` as a ``datetime``; the hub expects a
    string. Anything already a string is passed through; anything unexpected is
    dropped to None so a malformed value never blocks a whole batch.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    if isinstance(ts, str):
        return ts
    return None


def _to_rollup(session: dict) -> dict:
    """Reduce one live scan dict to a clean rollup with ONLY the allowed keys.

    Drops every key the hub does not expect (display, plans, mcp_tools, artifacts,
    *_source, child/parent ids, …) so the hub's extra='forbid' never trips.
    """
    rollup = {}
    for key in _ROLLUP_KEYS:
        if key not in session:
            continue
        value = session[key]
        if key == "timestamp":
            value = _iso(value)
        elif key == "tokens":
            # The hub accepts only integer token counts. Live-scan token dicts also
            # carry cost (float), 'estimated' (bool), cache_creation, _cached_sum,
            # delegated_*, … — strip to the four integer counts so the rollup
            # validates against the hub's tokens: Dict[str, int] schema. The cost
            # rides as its own top-level rollup key.
            t = value if isinstance(value, dict) else {}
            value = {k: int(t.get(k, 0) or 0) for k in ("input", "output", "cached", "total")}
        rollup[key] = value
    return rollup


def _post_json(url: str, payload: dict, token: str, timeout: float = 20.0):
    """POST ``payload`` as JSON. Returns (status_code, parsed_body_or_None).

    Raises urllib.error.URLError on a transport-level failure (hub unreachable);
    HTTP error responses (4xx/5xx) are returned as (status, body) rather than
    raised, so the caller can log and keep looping.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _safe_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, _safe_json(raw)


def _safe_json(raw: str):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw or None


def _register(hub_url: str, token: str) -> None:
    """Announce this collector to the hub once at startup. Best-effort: a failure
    here is logged but does not stop the sync loop (the hub also upserts the
    device on every /sync/sessions call)."""
    url = hub_url.rstrip("/") + "/devices/register"
    body = {
        "device_id": device_identity.device_id(),
        "device_name": device_identity.device_name(),
        "device_role": "collector",
    }
    try:
        status, resp = _post_json(url, body, token)
    except urllib.error.URLError as e:
        _log(f"register: hub unreachable ({e.reason}); will still try to sync")
        return
    if status == 200:
        _log(f"registered with hub as {body['device_name']} ({body['device_id'][:8]}…)")
    else:
        _log(f"register: hub returned HTTP {status}: {resp}")


def _scan_rollups() -> list:
    """Run the local scan and map every session to a privacy-safe rollup."""
    import main  # heavyweight import; deferred so startup banner prints first

    sessions = main._scan_sessions_sync()
    return [_to_rollup(s) for s in sessions]


def _sync_once(hub_url: str, token: str) -> None:
    """One scan → map → POST cycle. Never raises: any error is logged so the
    surrounding loop keeps going."""
    try:
        rollups = _scan_rollups()
    except Exception as e:  # noqa: BLE001 — a scan glitch must not kill the loop
        _log(f"scan failed: {e!r}")
        return

    if not rollups:
        _log("no local sessions to sync")
        return

    url = hub_url.rstrip("/") + "/sync/sessions"
    body = {
        "device_id": device_identity.device_id(),
        "device_name": device_identity.device_name(),
        "device_role": "collector",
        "sessions": rollups,
    }
    try:
        status, resp = _post_json(url, body, token)
    except urllib.error.URLError as e:
        _log(f"hub unreachable ({e.reason}); {len(rollups)} sessions queued for next pass")
        return

    if status == 200:
        stored = resp.get("stored") if isinstance(resp, dict) else None
        if stored is None:
            stored = len(rollups)
        _log(f"synced {stored} sessions to {hub_url}")
    elif status == 401:
        _log("hub rejected auth (HTTP 401) — check TA_AUTH_TOKEN matches the hub's token")
    elif status == 422:
        _log(f"hub rejected payload (HTTP 422): {resp}")
    else:
        _log(f"hub returned HTTP {status}: {resp}")


def _read_interval() -> int:
    raw = (os.environ.get("TA_INTERVAL") or "").strip()
    if not raw:
        return _DEFAULT_INTERVAL
    try:
        n = int(raw)
    except ValueError:
        _log(f"invalid TA_INTERVAL {raw!r}; using {_DEFAULT_INTERVAL}s")
        return _DEFAULT_INTERVAL
    return n if n > 0 else _DEFAULT_INTERVAL


def main_loop() -> int:
    hub_url = (os.environ.get("TA_HUB_URL") or "").strip()
    token = (os.environ.get("TA_AUTH_TOKEN") or "").strip()
    interval = _read_interval()

    if not hub_url:
        _log("TA_HUB_URL is required (the hub's base URL, e.g. http://host:8000)")
        return 2

    # Mark this install as a collector for the whole run. Honors the persisted
    # role thereafter; TA_DEVICE_ROLE/TA_DEVICE_NAME env overrides still apply.
    try:
        device_identity.set_role("collector")
    except Exception as e:  # noqa: BLE001 — a write glitch must not abort startup
        _log(f"could not persist collector role ({e!r}); continuing")

    _log(f"collector starting — hub={hub_url} interval={interval}s")
    _log(f"device={device_identity.device_name()} ({device_identity.device_id()[:8]}…)")
    if not token:
        _log("no TA_AUTH_TOKEN set — only a loopback or --insecure-no-auth hub will accept this")

    _register(hub_url, token)

    try:
        while True:
            _sync_once(hub_url, token)
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("stopping (Ctrl+C)")
        return 0


if __name__ == "__main__":
    sys.exit(main_loop())
