"""Persisted settings for automatic bidirectional sync (ADR-0004).

A peer that opts into sync needs three durable facts: which hub to talk to, the
bearer token the hub requires, and how often to run a cycle. They live in
``sync.json`` under the resolved data dir (see ``tt_paths``) alongside
``device.json`` / ``history.db``:

    { "enabled": bool, "hub_url": str, "auth_token": str, "interval": int }

Conventions mirror ``device_identity`` / ``harness_config``:
  - the file + its dir are created lazily on first write, never on a pure read;
  - ``load()`` never raises — a missing/corrupt file yields the defaults so the
    worker can always start (and simply stays idle until configured);
  - ``save()`` writes atomically (temp file + ``os.replace``) so a crash mid-write
    never leaves a half-written config.

The token is a credential: it is stored here but never echoed back over the
network (the ``/sync/config`` GET returns only ``has_token``), and the worker's
``status()`` never includes it either.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from tt_paths import data_dir

_log = logging.getLogger("tokentelemetry.sync_config")

_DEFAULT_INTERVAL = 60

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "hub_url": "",
    "auth_token": "",
    "interval": _DEFAULT_INTERVAL,
}


def _config_file() -> Path:
    # Resolved per call so a relocated data dir (or a test monkeypatch) always
    # hits the right file.
    return data_dir() / "sync.json"


def load() -> Dict[str, Any]:
    """Return the persisted sync config, filled with defaults for any missing or
    malformed key. Never raises — a missing/corrupt file yields the defaults so a
    misconfigured peer simply stays idle rather than crashing the app."""
    cfg = dict(_DEFAULTS)
    try:
        with open(_config_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg["enabled"] = bool(data.get("enabled", False))
            cfg["hub_url"] = str(data.get("hub_url") or "").strip()
            cfg["auth_token"] = str(data.get("auth_token") or "").strip()
            try:
                n = int(data.get("interval", _DEFAULT_INTERVAL))
                cfg["interval"] = n if n > 0 else _DEFAULT_INTERVAL
            except (TypeError, ValueError):
                cfg["interval"] = _DEFAULT_INTERVAL
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001 — config must never break startup
        _log.warning("sync.json unreadable, using defaults: %s", e)
    return cfg


def save(cfg: Dict[str, Any]) -> None:
    """Atomically persist the sync config. Normalizes to the known keys so a stray
    field never lands on disk. Never raises — a write glitch is logged, not fatal."""
    record = {
        "enabled": bool(cfg.get("enabled", False)),
        "hub_url": str(cfg.get("hub_url") or "").strip(),
        "auth_token": str(cfg.get("auth_token") or "").strip(),
        "interval": _DEFAULT_INTERVAL,
    }
    try:
        n = int(cfg.get("interval", _DEFAULT_INTERVAL))
        record["interval"] = n if n > 0 else _DEFAULT_INTERVAL
    except (TypeError, ValueError):
        record["interval"] = _DEFAULT_INTERVAL

    path = _config_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, path)  # atomic on POSIX
    except Exception as e:  # noqa: BLE001
        _log.warning("could not persist sync.json: %s", e)


def is_configured() -> bool:
    """True when sync is enabled AND a hub URL and token are present — i.e. the
    worker has everything it needs to run a cycle."""
    cfg = load()
    return bool(cfg["enabled"] and cfg["hub_url"] and cfg["auth_token"])
