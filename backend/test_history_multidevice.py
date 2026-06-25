"""Multi-device history tests (ADR-0004).

These pin the correctness-critical guarantees of the rename + multi-device work:

  - an existing v1 (single-device) history.db migrates in place with zero row loss,
    every prior row stamped with the local device_id + source_origin='local_scan';
  - a local and a remote session that share an (agent, id) keep separate rows and
    never overwrite each other;
  - remote sessions are stored under the remote device_id / 'remote_sync';
  - mark_absent is scoped to the local device — a hub never flags a collector's
    rows absent;
  - transcripts are attributed per-device (no cross-device mis-attribution);
  - the device registry lists both explicitly-registered and session-bearing devices.

No pytest in the venv — run directly:  python backend/test_history_multidevice.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

_VARS = ("TOKENANALYTICS_DATA_DIR", "TA_DEVICE_NAME", "TA_DEVICE_ROLE")


def _case(fn):
    saved = {v: os.environ.get(v) for v in _VARS}
    try:
        for v in _VARS:
            os.environ.pop(v, None)
        with tempfile.TemporaryDirectory() as d:
            os.environ["TOKENANALYTICS_DATA_DIR"] = d
            # Re-import the stores fresh so module state can't leak across cases.
            for m in ("history_store", "device_identity", "tt_paths"):
                sys.modules.pop(m, None)
            import tt_paths  # noqa: F401
            import device_identity
            import history_store
            fn(Path(d), history_store, device_identity)
    finally:
        for v in _VARS:
            os.environ.pop(v, None)
            if saved.get(v) is not None:
                os.environ[v] = saved[v]


_V1_SCHEMA = """
CREATE TABLE sessions (
    agent TEXT NOT NULL, id TEXT NOT NULL, project TEXT, model TEXT, provider TEXT,
    endpoint TEXT, billing_mode TEXT, first_ts TEXT, last_ts TEXT,
    input INTEGER DEFAULT 0, output INTEGER DEFAULT 0, cached INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0, cost REAL DEFAULT 0.0, tok_per_sec REAL,
    ecosystem_json TEXT, first_seen_at TEXT, last_seen_at TEXT,
    source_present INTEGER DEFAULT 1, transcript_archived INTEGER DEFAULT 0,
    summary_present INTEGER DEFAULT 0, PRIMARY KEY (agent, id)
);
CREATE TABLE transcripts (agent TEXT NOT NULL, id TEXT NOT NULL, blob BLOB,
    bytes INTEGER DEFAULT 0, archived_at TEXT, PRIMARY KEY (agent, id));
CREATE TABLE summaries (agent TEXT NOT NULL, id TEXT NOT NULL, summary TEXT,
    created_at TEXT, PRIMARY KEY (agent, id));
"""


def _build_v1_db(path: Path, rows):
    """Write a faithful v1 history.db (user_version=1) with the given rows."""
    con = sqlite3.connect(str(path))
    con.executescript(_V1_SCHEMA)
    con.executemany(
        "INSERT INTO sessions (agent, id, project, model, first_ts, last_ts, "
        "input, output, cached, total, cost) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.execute("PRAGMA user_version=1")
    con.commit()
    con.close()


def test_v1_db_migrates_without_row_loss(_d=None):
    def body(d, hs, di):
        rows = [
            ("claude", f"sess-{i}", "proj", "claude-opus-4-8",
             "2026-06-0%d T00:00:00+00:00" % (i + 1), "2026-06-0%d T01:00:00+00:00" % (i + 1),
             100 * i, 50 * i, 0, 150 * i, 0.1 * i)
            for i in range(1, 6)
        ]
        _build_v1_db(d / "history.db", rows)
        local = di.device_id()
        # Triggers _connect → _migrate (v1 → v2).
        out = hs.query()
        assert len(out) == len(rows), f"row loss: {len(out)} != {len(rows)}"
        for r in out:
            assert r["device_id"] == local, f"row not backfilled to local: {r['device_id']!r}"
            assert r["source_origin"] == "local_scan"
        # user_version actually advanced.
        con = sqlite3.connect(str(d / "history.db"))
        assert con.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        con.close()
    _case(body)


def test_cross_device_no_overwrite(_d=None):
    def body(_d, hs, di):
        local = di.device_id()
        shared = {"agent": "claude", "id": "collision-1", "project": "p",
                  "tokens": {"input": 10, "output": 5, "cached": 0, "total": 15}, "cost": 0.01}
        hs.upsert_sessions([shared])  # local
        remote = dict(shared)
        remote["tokens"] = {"input": 999, "output": 1, "cached": 0, "total": 1000}
        hs.upsert_sessions([remote], device_id="remote-B", source_origin="remote_sync",
                           device_name="Other-Mac", device_role="collector")
        allrows = hs.query()
        assert len(allrows) == 2, f"collision collapsed two devices into {len(allrows)} row(s)"
        local_only = hs.query(device_ids=[local])
        assert len(local_only) == 1 and local_only[0]["tokens"]["total"] == 15
        remote_only = hs.query(device_ids=["remote-B"])
        assert len(remote_only) == 1 and remote_only[0]["tokens"]["total"] == 1000
        assert remote_only[0]["source_origin"] == "remote_sync"
        assert remote_only[0]["device_name"] == "Other-Mac"
    _case(body)


def test_mark_absent_is_device_scoped(_d=None):
    def body(_d, hs, di):
        local = di.device_id()
        hs.upsert_sessions([
            {"agent": "claude", "id": "a", "tokens": {}, "cost": 0},
            {"agent": "claude", "id": "b", "tokens": {}, "cost": 0},
        ])
        hs.upsert_sessions([{"agent": "claude", "id": "a", "tokens": {}, "cost": 0}],
                           device_id="remote-B", source_origin="remote_sync")
        # Local scan now only sees 'a'. 'b' (local) should flip absent; the remote
        # 'a' must stay present — the hub didn't scan it.
        hs.mark_absent({("claude", "a")})
        present = {(r["device_id"], r["id"]): r["source_present"] for r in hs.query()}
        assert present[(local, "a")] is True
        assert present[(local, "b")] is False, "local 'b' should be marked absent"
        assert present[("remote-B", "a")] is True, "remote row must not be touched"
    _case(body)


def test_transcript_is_device_scoped(_d=None):
    def body(_d, hs, di):
        local = di.device_id()
        hs.upsert_sessions([{"agent": "claude", "id": "X", "tokens": {}, "cost": 0}])
        hs.upsert_sessions([{"agent": "claude", "id": "X", "tokens": {}, "cost": 0}],
                           device_id="remote-B", source_origin="remote_sync")
        hs.put_transcript("claude", "X", "LOCAL transcript body")  # local device
        assert hs.get_transcript("claude", "X", device_id=local) == "LOCAL transcript body"
        # The remote device's session must NOT inherit the local transcript.
        assert hs.get_transcript("claude", "X", device_id="remote-B") is None
    _case(body)


def test_register_and_list_devices(_d=None):
    def body(_d, hs, di):
        local = di.device_id()
        hs.upsert_sessions([{"agent": "claude", "id": "s1", "tokens": {}, "cost": 0}])  # local
        hs.register_device("remote-B", device_name="Other-Mac", device_role="collector",
                           source_origin="remote_sync")
        devs = {d["device_id"]: d for d in hs.list_devices()}
        assert local in devs, "local device (has sessions) should be listed"
        assert devs[local]["session_count"] == 1
        assert "remote-B" in devs, "registered collector should be listed"
        assert devs["remote-B"]["device_role"] == "collector"
    _case(body)


def test_v3_purges_phantom_rows_keeps_real_history(_d=None):
    def body(d, hs, di):
        # Build a v2 store by hand: 1 real session (tokens+model), 2 pure phantoms
        # (0 tokens, no model), and 1 zero-token row that has a summary (must be kept).
        dbp = d / "history.db"
        con = sqlite3.connect(str(dbp))
        con.executescript(hs._V2_SCHEMA)
        con.execute(
            "INSERT INTO sessions (device_id, agent, id, model, total, source_present, summary_present, transcript_archived) "
            "VALUES ('dev','claude','real-1','claude-opus-4-8',1000,1,0,0)")
        con.execute("INSERT INTO sessions (device_id, agent, id, model, total) VALUES ('dev','claude','phantom-1',NULL,0)")
        con.execute("INSERT INTO sessions (device_id, agent, id, model, total) VALUES ('dev','claude','phantom-2',NULL,0)")
        con.execute(
            "INSERT INTO sessions (device_id, agent, id, model, total, summary_present) "
            "VALUES ('dev','claude','kept-summary',NULL,0,1)")
        con.execute("PRAGMA user_version=2")
        con.commit(); con.close()

        # Any read triggers _connect → _migrate (v2 → v3 phantom purge).
        ids = {r["id"] for r in hs.query()}
        assert "real-1" in ids, "real token-bearing session was wrongly purged"
        assert "kept-summary" in ids, "zero-token row with a summary must be kept"
        assert "phantom-1" not in ids and "phantom-2" not in ids, "phantom rows were not purged"
        con = sqlite3.connect(str(dbp))
        assert con.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        con.close()
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
