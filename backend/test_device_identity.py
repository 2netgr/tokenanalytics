"""Tests for the per-install device identity (ADR-0004).

The device_id is the anchor that keeps two Macs from overwriting each other in
the durable history, so the one property that matters most is *stability*: the
same id must come back across calls and across process restarts.

No pytest in the venv — run directly:  python backend/test_device_identity.py
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

_VARS = ("TOKENANALYTICS_DATA_DIR", "TA_DEVICE_NAME", "TA_DEVICE_ROLE")


def _fresh_module():
    """Re-import device_identity to simulate a brand-new process (no module
    state carried over) — the id must still resolve from disk."""
    sys.modules.pop("device_identity", None)
    return importlib.import_module("device_identity")


def _case(fn):
    saved = {v: os.environ.get(v) for v in _VARS}
    try:
        for v in _VARS:
            os.environ.pop(v, None)
        with tempfile.TemporaryDirectory() as d:
            os.environ["TOKENANALYTICS_DATA_DIR"] = d
            fn(Path(d))
    finally:
        for v in _VARS:
            os.environ.pop(v, None)
            if saved.get(v) is not None:
                os.environ[v] = saved[v]


def test_id_is_generated_and_persisted():
    def body(d):
        di = _fresh_module()
        gen = di.device_id()
        assert gen and len(gen) == 32, f"expected 32-char hex id, got {gen!r}"
        assert (d / "device.json").exists(), "device.json was not written"
        stored = json.loads((d / "device.json").read_text())
        assert stored["device_id"] == gen
    _case(body)


def test_id_is_stable_within_process():
    def body(_d):
        di = _fresh_module()
        assert di.device_id() == di.device_id()
    _case(body)


def test_id_is_stable_across_processes():
    def body(_d):
        first = _fresh_module().device_id()
        # A fresh import with no module state must read the same id back.
        second = _fresh_module().device_id()
        assert first == second, f"id changed across processes: {first} != {second}"
    _case(body)


def test_default_name_is_hostname():
    def body(_d):
        di = _fresh_module()
        assert di.device_name(), "device_name should default to the hostname"
        assert not di.device_name().endswith(".local")
    _case(body)


def test_default_role_is_local():
    def body(_d):
        di = _fresh_module()
        assert di.device_role() == "local"
    _case(body)


def test_role_env_override():
    def body(_d):
        di = _fresh_module()
        os.environ["TA_DEVICE_ROLE"] = "collector"
        assert di.device_role() == "collector"
        os.environ["TA_DEVICE_ROLE"] = "bogus"  # invalid → falls back to stored
        assert di.device_role() == "local"
    _case(body)


def test_name_env_override():
    def body(_d):
        di = _fresh_module()
        os.environ["TA_DEVICE_NAME"] = "Nikos-MBP"
        assert di.device_name() == "Nikos-MBP"
    _case(body)


def test_set_role_persists_and_validates():
    def body(_d):
        di = _fresh_module()
        di.set_role("hub")
        assert _fresh_module().device_role() == "hub"  # survived a "restart"
        try:
            di.set_role("nonsense")
            assert False, "expected ValueError on invalid role"
        except ValueError:
            pass
    _case(body)


def test_local_device_shape():
    def body(_d):
        di = _fresh_module()
        dev = di.local_device()
        for k in ("device_id", "device_name", "device_role", "source_origin", "last_seen_at"):
            assert k in dev, f"local_device missing {k}"
        assert dev["source_origin"] == "local_scan"
        assert dev["is_local"] is True
    _case(body)


def test_create_is_race_safe():
    """If a file already exists, _create_atomically must yield the existing
    winner, never clobber it with a second id."""
    def body(_d):
        di = _fresh_module()
        winner = di.device_id()
        # Simulate a racing process that already wrote a different record.
        loser = {"device_id": "f" * 32, "device_name": "x", "device_role": "local"}
        got = di._create_atomically(loser)
        assert got["device_id"] == winner, "race guard clobbered the existing id"
    _case(body)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
