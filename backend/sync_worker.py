"""Automatic bidirectional sync loop (ADR-0004).

A *peer* in the star topology both PUSHES its own local rollups to the hub and
PULLS the hub's full set, so every synced device ends up mirroring all devices'
data. One always-on *hub* sits at the centre; peers push+pull to it on an
interval. This module is the peer side of that loop.

It is stdlib-only on the wire (``urllib`` + ``json``) and runs in a daemon
thread, mirroring ``collector.py``'s process model — importing ``main`` to reuse
``_scan_sessions_sync`` is heavyweight but fine in-process. The push half reuses
``collector._to_rollup`` so the privacy boundary (rollups only; the hub's
``extra='forbid'`` rejects any smuggled transcript field) is defined in exactly
one place.

One cycle:
  PUSH  scan local sessions → keep only this device's local rows → map each to a
        clean rollup → POST ``<hub>/sync/sessions``.
  PULL  GET ``<hub>/sync/pull?since=<watermark>&exclude_device=<me>`` → upsert each
        returned row under its own device_id with ``source_origin='remote_sync'``
        → advance the watermark to the response's ``server_time``.

The watermark persists to ``sync_state.json`` (sibling of ``sync.json``) so a
restart resumes incrementally instead of re-pulling everything. The hub being
unreachable or returning a non-2xx is recorded and the loop keeps going; the
auth token is never included in ``status()`` output.

Public API: ``start()`` (idempotent), ``stop()``, ``status()``, ``trigger_once()``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sync_config
from tt_paths import data_dir

_log = logging.getLogger("tokentelemetry.sync_worker")

# ── module state (guarded by _lock) ──────────────────────────────────────────
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None

# In-memory status, surfaced by /sync/status. The token is deliberately absent.
_status: Dict[str, Any] = {
    "enabled": False,
    "hub_url": "",
    "last_push_at": None,
    "last_pull_at": None,
    "pushed_count": 0,
    "pulled_count": 0,
    "last_error": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_file() -> Path:
    return data_dir() / "sync_state.json"


def _read_watermark() -> Optional[str]:
    """The ISO timestamp of the last successful pull (or None on first run)."""
    try:
        with open(_state_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            wm = data.get("last_pull")
            return str(wm) if wm else None
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001 — a bad watermark just means a full pull
        _log.warning("sync_state.json unreadable, pulling from scratch: %s", e)
    return None


def _write_watermark(last_pull: str) -> None:
    """Persist the pull watermark atomically so restarts resume incrementally."""
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"last_pull": last_pull}, fh, indent=2)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        _log.warning("could not persist sync_state.json: %s", e)


# ── HTTP helpers (stdlib only) ───────────────────────────────────────────────

def _safe_json(raw: str):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw or None


def _post_json(url: str, payload: dict, token: str, timeout: float = 20.0):
    """POST ``payload`` as JSON. Returns (status_code, parsed_body_or_None).

    Raises urllib.error.URLError on a transport-level failure (hub unreachable);
    HTTP error responses (4xx/5xx) are returned as (status, body) so the caller
    can record and keep looping. Mirrors ``collector._post_json``."""
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


def _get_json(url: str, token: str, timeout: float = 20.0):
    """GET a JSON body. Returns (status_code, parsed_body_or_None). Same error
    contract as ``_post_json``."""
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _safe_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, _safe_json(raw)


# ── one cycle ────────────────────────────────────────────────────────────────

def _local_rollups() -> List[Dict[str, Any]]:
    """Scan locally and reduce *this device's own local* sessions to clean rollups.

    Reuses ``collector._to_rollup`` (single source of truth for the rollups-only
    privacy boundary). Remote rows already mirrored into history are skipped — a
    peer pushes only what it scanned itself, never what it pulled from the hub."""
    import main  # heavyweight import; deferred like collector.py
    import collector
    import device_identity

    my_id = device_identity.device_id()
    sessions = main._scan_sessions_sync()
    rollups: List[Dict[str, Any]] = []
    for s in sessions:
        # A live scan dict carries no device_id (it's this machine's own scan);
        # any dict that *does* carry a foreign device_id is a rehydrated remote
        # row and must not be pushed back. Local/own rows pass through.
        sid = s.get("device_id")
        if sid and sid != my_id:
            continue
        if s.get("source_origin") == "remote_sync":
            continue
        rollups.append(collector._to_rollup(s))
    return rollups


def _push(hub_url: str, token: str) -> int:
    """PUSH this device's local rollups to the hub. Returns the count pushed.
    Records ``last_error`` on a non-2xx but never raises."""
    import device_identity

    rollups = _local_rollups()
    if not rollups:
        return 0
    url = hub_url.rstrip("/") + "/sync/sessions"
    body = {
        "device_id": device_identity.device_id(),
        "device_name": device_identity.device_name(),
        "device_role": "collector",
        "sessions": rollups,
    }
    status, resp = _post_json(url, body, token)
    if 200 <= status < 300:
        stored = resp.get("stored") if isinstance(resp, dict) else None
        return int(stored) if stored is not None else len(rollups)
    _status["last_error"] = f"push HTTP {status}: {resp}"
    _log.warning("sync push: hub returned HTTP %s: %s", status, resp)
    return 0


def _pull(hub_url: str, token: str) -> int:
    """PULL the hub's rows newer than our watermark and mirror them into history.
    Advances the watermark to the response's ``server_time``. Returns the count
    pulled. Records ``last_error`` on a non-2xx but never raises."""
    import history_store
    import device_identity
    from urllib.parse import urlencode

    my_id = device_identity.device_id()
    since = _read_watermark()
    params = {"exclude_device": my_id, "limit": 5000}
    if since:
        params["since"] = since
    url = hub_url.rstrip("/") + "/sync/pull?" + urlencode(params)

    status, resp = _get_json(url, token)
    if not (200 <= status < 300) or not isinstance(resp, dict):
        _status["last_error"] = f"pull HTTP {status}: {resp}"
        _log.warning("sync pull: hub returned HTTP %s: %s", status, resp)
        return 0

    sessions = resp.get("sessions") or []
    pulled = 0
    for row in sessions:
        rid = row.get("device_id")
        if not rid or rid == my_id:
            continue  # never mirror our own rows back onto ourselves
        history_store.upsert_sessions(
            [row], device_id=rid, source_origin="remote_sync",
            device_name=row.get("device_name"), device_role=row.get("device_role"),
        )
        pulled += 1

    server_time = resp.get("server_time")
    if server_time:
        _write_watermark(str(server_time))
    return pulled


def _run_cycle() -> Dict[str, Any]:
    """Run one PUSH + PULL cycle, updating ``_status``. Never raises — any error
    is captured into ``last_error`` so the surrounding loop keeps going."""
    cfg = sync_config.load()
    _status["enabled"] = bool(cfg["enabled"])
    _status["hub_url"] = cfg["hub_url"]

    if not sync_config.is_configured():
        _status["last_error"] = "not configured"
        return dict(_status)

    hub_url = cfg["hub_url"]
    token = cfg["auth_token"]

    # PUSH ---------------------------------------------------------------------
    try:
        pushed = _push(hub_url, token)
        _status["pushed_count"] = pushed
        _status["last_push_at"] = _now_iso()
    except urllib.error.URLError as e:
        _status["last_error"] = f"push: hub unreachable ({e.reason})"
        _log.warning("sync push: hub unreachable: %s", e.reason)
    except Exception as e:  # noqa: BLE001 — a scan/push glitch must not kill the loop
        _status["last_error"] = f"push failed: {e!r}"
        _log.exception("sync push failed: %s", e)

    # PULL ---------------------------------------------------------------------
    try:
        pulled = _pull(hub_url, token)
        _status["pulled_count"] = pulled
        _status["last_pull_at"] = _now_iso()
    except urllib.error.URLError as e:
        _status["last_error"] = f"pull: hub unreachable ({e.reason})"
        _log.warning("sync pull: hub unreachable: %s", e.reason)
    except Exception as e:  # noqa: BLE001
        _status["last_error"] = f"pull failed: {e!r}"
        _log.exception("sync pull failed: %s", e)

    return dict(_status)


# ── public API ───────────────────────────────────────────────────────────────

def trigger_once() -> Dict[str, Any]:
    """Run a single PUSH+PULL cycle synchronously and return the resulting status.
    Used by the UI "Sync now" button and by tests. Never raises."""
    try:
        return _run_cycle()
    except Exception as e:  # noqa: BLE001
        _status["last_error"] = f"cycle failed: {e!r}"
        _log.exception("sync cycle failed: %s", e)
        return dict(_status)


def _loop(stop_event: threading.Event) -> None:
    """Daemon-thread body: run a cycle, then wait ``interval`` seconds (or until
    stopped). Re-reads the interval each pass so a config change takes effect on
    the next loop without a restart."""
    while not stop_event.is_set():
        trigger_once()
        try:
            interval = int(sync_config.load().get("interval", 60)) or 60
        except (TypeError, ValueError):
            interval = 60
        # Interruptible sleep: returns immediately when stop() is called.
        stop_event.wait(interval)


def start() -> None:
    """Spawn the daemon sync thread if sync is configured and not already running.
    Idempotent — calling it when the worker is already alive is a no-op. A peer
    that isn't configured just stays idle (no thread)."""
    global _thread, _stop_event
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        if not sync_config.is_configured():
            _status["enabled"] = bool(sync_config.load().get("enabled"))
            return
        _stop_event = threading.Event()
        _thread = threading.Thread(
            target=_loop, args=(_stop_event,), name="tt-sync-worker", daemon=True,
        )
        _thread.start()
        _status["enabled"] = True


def stop() -> None:
    """Signal the sync thread to stop and join briefly. Idempotent. Safe to call
    even when no thread is running (e.g. before every restart on config change)."""
    global _thread, _stop_event
    with _lock:
        ev, th = _stop_event, _thread
        _thread = None
        _stop_event = None
    if ev is not None:
        ev.set()
    if th is not None and th.is_alive():
        th.join(timeout=2.0)


def status() -> Dict[str, Any]:
    """Current sync status for /sync/status. A copy, so callers can't mutate the
    module state, and the auth token is never present by construction."""
    out = dict(_status)
    out["enabled"] = bool(sync_config.load().get("enabled"))
    out["running"] = bool(_thread is not None and _thread.is_alive())
    return out
