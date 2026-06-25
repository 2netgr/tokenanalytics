"""Endpoint tests for multi-device sync (ADR-0004).

Drives the real FastAPI app through a TestClient:
  - a synced rollup shows up in /devices and in /analytics?device=all|<id> but
    NOT in /analytics?device=local;
  - /sync/sessions rejects unauthenticated *remote* requests when a token is set;
  - /sync/sessions rejects payloads that smuggle transcript/prompt text;
  - /devices always reports the local device.

Needs fastapi + httpx (the runtime deps). Run directly:
    python backend/test_sync_endpoints.py
or under pytest in the venv. Skips cleanly if fastapi isn't installed.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

# A throwaway data dir for this module's state (device.json + history.db). It is
# applied PER TEST (autouse fixture below / __main__ setup), never at module
# import — setting a high-precedence env var at import would leak into other test
# modules during pytest collection and corrupt their isolation.
_TMP = tempfile.mkdtemp(prefix="ta-sync-test-")

try:
    from fastapi.testclient import TestClient
    import main
    _client = TestClient(main.app)
    _HAVE = True
    _ERR = None
except Exception as e:  # noqa: BLE001
    _HAVE = False
    _ERR = e


def _apply_env():
    os.environ["TOKENANALYTICS_DATA_DIR"] = _TMP
    os.environ.pop("TT_AUTH_TOKEN", None)


# Scope the env to THIS module's tests so it can't leak into the rest of the suite.
try:
    import pytest

    @pytest.fixture(autouse=True)
    def _scoped_env():
        saved = {k: os.environ.get(k) for k in ("TOKENANALYTICS_DATA_DIR", "TT_AUTH_TOKEN")}
        _apply_env()
        try:
            yield
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
except ImportError:
    pass

# A sentinel model + token total so assertions are immune to the dev machine's
# real local sessions (which also flow through /analytics?device=local|all).
_SENTINEL_MODEL = "ta-sync-test-model"
_ROLLUP = {
    "agent": "claude", "id": "ta-sync-1", "project": "p", "model": _SENTINEL_MODEL,
    "tokens": {"input": 12000, "output": 345, "cached": 0, "total": 12345},
    "cost": 0.42, "timestamp": "2026-06-20T00:00:00+00:00",
}


def _models(analytics_json):
    return set((analytics_json.get("by_model") or {}).keys())


def test_sync_then_visible_across_device_filters():
    os.environ.pop("TT_AUTH_TOKEN", None)
    r = _client.post("/sync/sessions", json={
        "device_id": "remote-B", "device_name": "Other-Mac", "sessions": [_ROLLUP],
    })
    assert r.status_code == 200, r.text
    assert r.json()["stored"] == 1

    devs = _client.get("/devices").json()
    assert any(d["device_id"] == "remote-B" for d in devs["devices"]), "synced device missing from /devices"

    all_models = _models(_client.get("/analytics?device=all").json())
    rem_models = _models(_client.get("/analytics?device=remote-B").json())
    loc_models = _models(_client.get("/analytics?device=local").json())
    assert _SENTINEL_MODEL in rem_models, "remote device view missing its own session"
    assert _SENTINEL_MODEL in all_models, "all-devices view missing the synced session"
    assert _SENTINEL_MODEL not in loc_models, "local view leaked a remote session"

    rem = _client.get("/analytics?device=remote-B").json()
    assert rem["by_model"][_SENTINEL_MODEL]["total"] == 12345


def test_sync_rejects_unauthenticated_remote():
    os.environ["TT_AUTH_TOKEN"] = "s3cret"
    try:
        # TestClient is a non-loopback client → the gate applies.
        r = _client.post("/sync/sessions", json={"device_id": "remote-X", "sessions": [_ROLLUP]})
        assert r.status_code == 401, f"expected 401, got {r.status_code}"
        ok = _client.post(
            "/sync/sessions", json={"device_id": "remote-X", "sessions": [_ROLLUP]},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert ok.status_code == 200, ok.text
    finally:
        os.environ.pop("TT_AUTH_TOKEN", None)


def test_sync_rejects_transcript_smuggling():
    os.environ.pop("TT_AUTH_TOKEN", None)
    bad = dict(_ROLLUP, id="ta-sync-bad", text="secret prompt/output text")
    r = _client.post("/sync/sessions", json={"device_id": "remote-C", "sessions": [bad]})
    assert r.status_code == 422, f"expected 422 for smuggled transcript, got {r.status_code}: {r.text}"


def test_devices_reports_local():
    d = _client.get("/devices").json()
    assert d["local"]["device_id"], "/devices did not report a local device_id"
    assert d["local"]["is_local"] is True


def test_register_device():
    os.environ.pop("TT_AUTH_TOKEN", None)
    r = _client.post("/devices/register", json={
        "device_id": "remote-reg", "device_name": "Reg-Mac", "device_role": "collector",
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    devs = _client.get("/devices").json()["devices"]
    assert any(x["device_id"] == "remote-reg" for x in devs)


if __name__ == "__main__":
    if not _HAVE:
        print(f"SKIP  test_sync_endpoints (fastapi/httpx not installed: {_ERR})")
        sys.exit(0)
    _apply_env()  # direct run: single module, safe to set globally
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
