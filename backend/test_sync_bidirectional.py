"""Endpoint + store tests for bidirectional sync (ADR-0004).

Pins the pull side of the star topology: a peer both pushes its rollups to the
hub AND pulls the hub's full set. These cover the pull half end-to-end:

  - ``history_store.query_since`` returns rows after a watermark and excludes a
    given device_id;
  - ``GET /sync/pull`` surfaces previously-synced rows with their device fields
    plus a ``server_time``;
  - a round-trip: upsert a remote row, then ``/sync/pull`` returns it in shape;
  - ``POST /sync/config`` then ``GET /sync/config`` reflects ``enabled`` +
    ``has_token`` without ever leaking the raw token.

Needs fastapi + httpx (the runtime deps). Run under pytest in the venv, or
directly:  python backend/test_sync_bidirectional.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))

# A throwaway data dir for this module's state (device.json + history.db +
# sync.json). Applied PER TEST (autouse fixture / __main__ setup), never at
# import — a high-precedence env var set at import would leak into other test
# modules during collection and corrupt their isolation.
_TMP = tempfile.mkdtemp(prefix="ta-bisync-test-")

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


_SENTINEL_MODEL = "ta-bisync-test-model"


def _remote_rollup(sid: str, model: str = _SENTINEL_MODEL):
    return {
        "agent": "claude", "id": sid, "project": "p", "model": model,
        "tokens": {"input": 1000, "output": 50, "cached": 0, "total": 1050},
        "cost": 0.1, "timestamp": "2026-06-20T00:00:00+00:00",
    }


def _seed_remote(history_store, device_id: str, sid: str, model: str = _SENTINEL_MODEL):
    """Mirror what the worker's pull half does: upsert a remote rollup under a
    foreign device_id with source_origin='remote_sync'."""
    return history_store.upsert_sessions(
        [_remote_rollup(sid, model)], device_id=device_id,
        source_origin="remote_sync", device_name="Other-Mac", device_role="collector",
    )


# ── history_store.query_since ────────────────────────────────────────────────

def test_query_since_excludes_device_and_respects_watermark():
    import history_store
    # Two devices, two rows, with a watermark captured between them.
    _seed_remote(history_store, "dev-A", "qs-a1")
    watermark = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    time.sleep(0.01)
    _seed_remote(history_store, "dev-B", "qs-b1")

    # since=watermark → only the row written after it (dev-B's).
    after = history_store.query_since(since=watermark)
    ids_after = {r["id"] for r in after}
    assert "qs-b1" in ids_after, "row written after the watermark was not returned"
    assert "qs-a1" not in ids_after, "row written before the watermark leaked through"

    # exclude_device_id filters out that device's rows entirely.
    excl = history_store.query_since(since=None, exclude_device_id="dev-A")
    devs = {r["device_id"] for r in excl}
    assert "dev-A" not in devs, "excluded device's rows were returned"
    assert "dev-B" in devs, "non-excluded device's rows missing"

    # Every returned row carries last_seen_at so the client can track progress.
    assert all(r.get("last_seen_at") for r in excl), "query_since row missing last_seen_at"


# ── GET /sync/pull ───────────────────────────────────────────────────────────

def test_sync_pull_returns_rows_with_device_fields_and_server_time():
    import history_store
    _seed_remote(history_store, "remote-pull", "pull-1")

    r = _client.get("/sync/pull")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "server_time" in body and body["server_time"], "missing server_time"
    assert isinstance(body.get("sessions"), list)

    match = [s for s in body["sessions"] if s["id"] == "pull-1"]
    assert match, "synced row not surfaced by /sync/pull"
    s = match[0]
    assert s["device_id"] == "remote-pull"
    assert s["device_name"] == "Other-Mac"
    assert s["device_role"] == "collector"
    assert s["source_origin"] == "remote_sync"
    assert s["tokens"]["total"] == 1050
    assert s.get("last_seen_at"), "pulled row missing last_seen_at"


def test_sync_pull_excludes_named_device():
    import history_store
    _seed_remote(history_store, "keep-me", "ex-keep")
    _seed_remote(history_store, "drop-me", "ex-drop")

    r = _client.get("/sync/pull", params={"exclude_device": "drop-me"})
    assert r.status_code == 200, r.text
    ids = {s["id"] for s in r.json()["sessions"]}
    assert "ex-keep" in ids
    assert "ex-drop" not in ids, "exclude_device did not filter the named device"


# ── round-trip: upsert remote row, pull it back ──────────────────────────────

def test_round_trip_remote_upsert_then_pull():
    import history_store
    n = _seed_remote(history_store, "rt-device", "rt-1", model="rt-model")
    assert n == 1

    r = _client.get("/sync/pull", params={"exclude_device": "some-other-peer"})
    assert r.status_code == 200, r.text
    rows = [s for s in r.json()["sessions"] if s["id"] == "rt-1"]
    assert rows, "round-tripped row not found"
    row = rows[0]
    # Shape assertions: the pull payload is live-session-shaped.
    for key in ("id", "agent", "device_id", "device_name", "device_role",
                "source_origin", "tokens", "timestamp", "last_seen_at"):
        assert key in row, f"pulled row missing key {key!r}"
    assert row["model"] == "rt-model"
    assert set(row["tokens"]) >= {"input", "output", "cached", "total"}


# ── /sync/config round-trip without leaking the token ────────────────────────

def test_sync_config_roundtrip_hides_token():
    # Initially unconfigured.
    g0 = _client.get("/sync/config").json()
    assert g0["enabled"] is False
    assert g0["has_token"] is False

    r = _client.post("/sync/config", json={
        "hub_url": "http://hub.example:8000",
        "auth_token": "super-secret-token",
        "enabled": True,
        "interval": 90,
    })
    assert r.status_code == 200 and r.json()["ok"] is True

    g = _client.get("/sync/config").json()
    assert g["enabled"] is True
    assert g["hub_url"] == "http://hub.example:8000"
    assert g["interval"] == 90
    assert g["has_token"] is True
    # The raw token must NEVER be echoed back over the network.
    assert "auth_token" not in g
    assert "super-secret-token" not in str(g)


def test_sync_status_never_contains_token():
    _client.post("/sync/config", json={
        "hub_url": "http://hub.example:8000",
        "auth_token": "another-secret",
        "enabled": False,
        "interval": 60,
    })
    st = _client.get("/sync/status").json()
    assert "another-secret" not in str(st)
    assert "auth_token" not in st
    # Status shape carries the documented fields.
    for key in ("enabled", "hub_url", "last_push_at", "last_pull_at",
                "pushed_count", "pulled_count", "last_error"):
        assert key in st, f"status missing key {key!r}"


if __name__ == "__main__":
    if not _HAVE:
        print(f"SKIP  test_sync_bidirectional (fastapi/httpx not installed: {_ERR})")
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
