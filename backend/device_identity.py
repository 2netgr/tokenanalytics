"""Stable per-install identity for multi-device analytics (ADR-0004).

Every TokenAnalytics install gets a ``device_id`` generated once and persisted to
``device.json`` in the resolved data dir, plus a human-readable ``device_name``
(the macOS hostname by default) and a ``device_role``:

  - ``local``     a standalone install that only scans its own machine.
  - ``hub``       the device that serves the dashboard and aggregates others.
  - ``collector`` a secondary device that scans locally and syncs rollups to a hub.

The ``device_id`` is the anchor that keeps two Macs from overwriting each other in
the durable history (the rollup PK widens to ``(device_id, agent, id)``). It must
therefore be *stable across runs* — so it is written once and read thereafter.

Conventions mirror ``history_store`` / ``harness_config``:
  - the file + its dir are created lazily on first write, never on a pure read;
  - reads never raise — a missing/corrupt file yields freshly-generated defaults;
  - first-write is atomic and race-safe (exclusive create, then re-read the
    winner) so two processes starting at once converge on one id.

``TA_DEVICE_NAME`` / ``TA_DEVICE_ROLE`` env vars override the stored name/role for
the current process (handy for collectors and for tests).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from tt_paths import data_dir

_log = logging.getLogger("tokentelemetry.identity")

VALID_ROLES = ("local", "hub", "collector")
_DEFAULT_ROLE = "local"


def _device_file() -> Path:
    return data_dir() / "device.json"


def _hostname() -> str:
    try:
        h = socket.gethostname() or ""
    except Exception:  # noqa: BLE001
        h = ""
    # Trim the chatty ``.local`` mDNS suffix macOS appends; keep it readable.
    if h.endswith(".local"):
        h = h[: -len(".local")]
    return h.strip() or "this-mac"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_raw() -> Dict[str, Any]:
    try:
        with open(_device_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001 — never let identity break a scan
        _log.warning("device.json unreadable, regenerating: %s", e)
        return {}


def _create_atomically(record: Dict[str, Any]) -> Dict[str, Any]:
    """Write ``record`` only if no file exists yet; if we lost the race, return
    whatever the winner wrote. Guarantees a single stable id per install."""
    path = _device_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 'x' fails if the file already exists — that's the race guard.
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        return record
    except FileExistsError:
        existing = _read_raw()
        return existing or record
    except Exception as e:  # noqa: BLE001
        _log.warning("could not persist device.json (%s); using ephemeral id", e)
        return record


def _persist(record: Dict[str, Any]) -> None:
    path = _device_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, path)  # atomic on POSIX
    except Exception as e:  # noqa: BLE001
        _log.warning("could not update device.json: %s", e)


def _ensure() -> Dict[str, Any]:
    """Return the persisted device record, creating it on first call."""
    rec = _read_raw()
    if rec.get("device_id"):
        return rec
    fresh = {
        "device_id": uuid.uuid4().hex,
        "device_name": _hostname(),
        "device_role": _DEFAULT_ROLE,
        "created_at": _now_iso(),
    }
    return _create_atomically(fresh)


def device_id() -> str:
    return str(_ensure().get("device_id") or "")


def device_name() -> str:
    override = (os.environ.get("TA_DEVICE_NAME") or "").strip()
    if override:
        return override
    return str(_ensure().get("device_name") or _hostname())


def device_role() -> str:
    override = (os.environ.get("TA_DEVICE_ROLE") or "").strip().lower()
    if override in VALID_ROLES:
        return override
    role = str(_ensure().get("device_role") or _DEFAULT_ROLE)
    return role if role in VALID_ROLES else _DEFAULT_ROLE


def set_role(role: str) -> None:
    role = (role or "").strip().lower()
    if role not in VALID_ROLES:
        raise ValueError(f"invalid device_role: {role!r} (expected one of {VALID_ROLES})")
    rec = _ensure()
    rec["device_role"] = role
    _persist(rec)


def set_name(name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise ValueError("device_name cannot be blank")
    rec = _ensure()
    rec["device_name"] = name
    _persist(rec)


def local_device() -> Dict[str, Any]:
    """The local machine's identity as a registry-shaped dict. ``last_seen_at`` is
    'now' because we are, by definition, looking at the local device live."""
    _ensure()
    return {
        "device_id": device_id(),
        "device_name": device_name(),
        "device_role": device_role(),
        "source_origin": "local_scan",
        "last_seen_at": _now_iso(),
        "is_local": True,
    }
