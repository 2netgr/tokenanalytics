"""Durable, local history store for TokenAnalytics.

TokenAnalytics is otherwise a pure live-scanner: every request re-reads the
coding agents' on-disk transcripts and keeps the result only in a 30s in-RAM
cache. But agents prune their own transcripts (Claude Code deletes
``~/.claude/projects`` after ``cleanupPeriodDays``, default 30), so any analytics
window older than that retention silently loses data.

This module gives the app its own SQLite store under the resolved data dir (see
``tt_paths``) that it upserts on every scan, so a *summary* of each session
outlives the agent's own pruning. It is deliberately tiered:

  - ``sessions``     core rollup — tiny, always kept (one row per session).
  - ``transcripts``  opt-in, compressed full transcript blobs — user-deletable.
  - ``summaries``    generated summaries — persist even after a transcript is gone.

Multi-device (ADR-0004): every row is stamped with the ``device_id`` that produced
it, so two Macs that happen to share an ``(agent, id)`` never overwrite each other.
The rollup PK is therefore ``(device_id, agent, id)``. A small ``devices`` table
holds the registry of known devices (local + any collector that has synced).

Design rules (mirroring ``harness_config``):
  - The DB and its directory are created lazily on first write, never on read.
  - Reads never raise; a missing/locked DB yields empty results.
  - One short-lived connection per call — safe to call from the scan worker
    thread and from request handlers. WAL mode lets reads run during a write.
  - ``query()`` returns rows shaped *exactly* like live session dicts so the
    existing ``/analytics`` aggregation loop can consume stored + live uniformly.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from tt_paths import data_dir

_log = logging.getLogger("tokentelemetry.history")

SCHEMA_VERSION = 3

# Sub-dicts folded into ``ecosystem_json`` and expanded back out on read. These
# are the keys the analytics aggregation + delegation views consume beyond the
# core rollup columns.
_ECOSYSTEM_KEYS = (
    "skills_used", "mcp_usage", "delegation", "subagent_info", "parent_session_id",
)


def _local_identity() -> Tuple[str, str, str]:
    """(device_id, device_name, device_role) for this machine. Imported lazily to
    avoid any import-time coupling; never raises — falls back to a blank id so the
    store still works if identity is somehow unavailable."""
    try:
        import device_identity
        return device_identity.device_id(), device_identity.device_name(), device_identity.device_role()
    except Exception as e:  # noqa: BLE001
        _log.warning("device identity unavailable, using blank device_id: %s", e)
        return "", "", "local"


def _db_path() -> Path:
    # Resolved per call so a process that relocates the data dir (or a test that
    # monkeypatches the data dir) always hits the right file.
    return data_dir() / "history.db"


def _connect() -> sqlite3.Connection:
    """Open the DB, creating the dir + schema lazily. Caller closes."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    _migrate(con)
    return con


# ── schema + migrations ──────────────────────────────────────────────────────

_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    device_id       TEXT NOT NULL DEFAULT '',
    device_name     TEXT,
    device_role     TEXT,
    source_origin   TEXT DEFAULT 'local_scan',
    agent           TEXT NOT NULL,
    id              TEXT NOT NULL,
    project         TEXT,
    model           TEXT,
    provider        TEXT,
    endpoint        TEXT,
    billing_mode    TEXT,
    first_ts        TEXT,
    last_ts         TEXT,
    input           INTEGER DEFAULT 0,
    output          INTEGER DEFAULT 0,
    cached          INTEGER DEFAULT 0,
    total           INTEGER DEFAULT 0,
    cost            REAL    DEFAULT 0.0,
    tok_per_sec     REAL,
    ecosystem_json  TEXT,
    first_seen_at   TEXT,
    last_seen_at    TEXT,
    source_present  INTEGER DEFAULT 1,
    transcript_archived INTEGER DEFAULT 0,
    summary_present INTEGER DEFAULT 0,
    PRIMARY KEY (device_id, agent, id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_ts ON sessions(last_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_agent   ON sessions(agent);
CREATE INDEX IF NOT EXISTS idx_sessions_model   ON sessions(model);
CREATE INDEX IF NOT EXISTS idx_sessions_device  ON sessions(device_id);

CREATE TABLE IF NOT EXISTS transcripts (
    device_id   TEXT NOT NULL DEFAULT '',
    agent       TEXT NOT NULL,
    id          TEXT NOT NULL,
    blob        BLOB,
    bytes       INTEGER DEFAULT 0,
    archived_at TEXT,
    PRIMARY KEY (device_id, agent, id)
);

CREATE TABLE IF NOT EXISTS summaries (
    device_id  TEXT NOT NULL DEFAULT '',
    agent      TEXT NOT NULL,
    id         TEXT NOT NULL,
    summary    TEXT,
    created_at TEXT,
    PRIMARY KEY (device_id, agent, id)
);

CREATE TABLE IF NOT EXISTS devices (
    device_id     TEXT PRIMARY KEY,
    device_name   TEXT,
    device_role   TEXT,
    source_origin TEXT,
    last_seen_at  TEXT
);
"""


def _migrate(con: sqlite3.Connection) -> None:
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver >= SCHEMA_VERSION:
        return
    if ver < 1:
        # Fresh DB → create the current schema directly.
        con.executescript(_V2_SCHEMA)
    elif ver < 2:
        # ver == 1: an existing single-device store. Widen every rollup PK to
        # include device_id and backfill the local device onto every prior row.
        _migrate_1_to_2(con)
    if ver < 3:
        # v3 is a data cleanup, not a schema change: drop fileless phantom rollups.
        _purge_phantom_sessions(con)
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.commit()


def _purge_phantom_sessions(con: sqlite3.Connection) -> None:
    """Schema v3 cleanup — delete fileless 'phantom' rollups: rows the live scanner
    used to fabricate from ``~/.claude/history.jsonl`` for sessions whose transcripts
    were already pruned. They carry no tokens, no model, no archived transcript and no
    summary, so they only inflated the session count. Any row that captured real
    information (tokens, a model, an archived transcript, or a summary) is kept — that
    is the genuine all-time history we never delete. Never raises."""
    try:
        con.execute(
            "DELETE FROM sessions WHERE total=0 AND (model IS NULL OR model='') "
            "AND summary_present=0 AND transcript_archived=0"
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("phantom purge skipped: %s", e)


def _migrate_1_to_2(con: sqlite3.Connection) -> None:
    """Rebuild sessions/transcripts/summaries with a ``(device_id, agent, id)`` PK
    and stamp every existing row as belonging to the local device. SQLite cannot
    alter a primary key in place, so each table is rebuilt: rename → create new →
    copy → drop. Idempotent and lossless; ``history.db`` stays deletable to undo."""
    local_id, local_name, local_role = _local_identity()

    # sessions ----------------------------------------------------------------
    con.execute("ALTER TABLE sessions RENAME TO _sessions_v1")
    con.executescript(_V2_SCHEMA)  # creates sessions (+ transcripts/summaries/devices) IF NOT EXISTS
    con.execute(
        """
        INSERT INTO sessions (
            device_id, device_name, device_role, source_origin,
            agent, id, project, model, provider, endpoint, billing_mode,
            first_ts, last_ts, input, output, cached, total, cost,
            tok_per_sec, ecosystem_json, first_seen_at, last_seen_at,
            source_present, transcript_archived, summary_present
        )
        SELECT
            ?, ?, ?, 'local_scan',
            agent, id, project, model, provider, endpoint, billing_mode,
            first_ts, last_ts, input, output, cached, total, cost,
            tok_per_sec, ecosystem_json, first_seen_at, last_seen_at,
            source_present, transcript_archived, summary_present
        FROM _sessions_v1
        """,
        (local_id, local_name, local_role),
    )
    con.execute("DROP TABLE _sessions_v1")

    # transcripts -------------------------------------------------------------
    con.execute("ALTER TABLE transcripts RENAME TO _transcripts_v1")
    con.executescript(
        """CREATE TABLE transcripts (
            device_id   TEXT NOT NULL DEFAULT '',
            agent       TEXT NOT NULL,
            id          TEXT NOT NULL,
            blob        BLOB,
            bytes       INTEGER DEFAULT 0,
            archived_at TEXT,
            PRIMARY KEY (device_id, agent, id)
        );"""
    )
    con.execute(
        "INSERT INTO transcripts (device_id, agent, id, blob, bytes, archived_at) "
        "SELECT ?, agent, id, blob, bytes, archived_at FROM _transcripts_v1",
        (local_id,),
    )
    con.execute("DROP TABLE _transcripts_v1")

    # summaries ---------------------------------------------------------------
    con.execute("ALTER TABLE summaries RENAME TO _summaries_v1")
    con.executescript(
        """CREATE TABLE summaries (
            device_id  TEXT NOT NULL DEFAULT '',
            agent      TEXT NOT NULL,
            id         TEXT NOT NULL,
            summary    TEXT,
            created_at TEXT,
            PRIMARY KEY (device_id, agent, id)
        );"""
    )
    con.execute(
        "INSERT INTO summaries (device_id, agent, id, summary, created_at) "
        "SELECT ?, agent, id, summary, created_at FROM _summaries_v1",
        (local_id,),
    )
    con.execute("DROP TABLE _summaries_v1")

    # Register the local device now that its rows exist.
    if local_id:
        con.execute(
            "INSERT OR REPLACE INTO devices (device_id, device_name, device_role, source_origin, last_seen_at) "
            "VALUES (?,?,?,?,?)",
            (local_id, local_name, local_role, "local_scan", datetime.now(timezone.utc).isoformat()),
        )


# ── serialization helpers ────────────────────────────────────────────────────

def _to_utc_iso(ts: Any) -> str:
    """Normalize a session timestamp to UTC ISO-8601 for lexicographic range
    filtering. Accepts a datetime or an ISO string; falls back to now()."""
    if isinstance(ts, datetime):
        dt = ts
    elif isinstance(ts, str) and ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _ecosystem_blob(row: Dict[str, Any]) -> Optional[str]:
    eco = {k: row[k] for k in _ECOSYSTEM_KEYS if row.get(k) is not None}
    return json.dumps(eco, default=str) if eco else None


# ── write path ───────────────────────────────────────────────────────────────

def upsert_sessions(
    rows: Sequence[Dict[str, Any]],
    device_id: Optional[str] = None,
    source_origin: str = "local_scan",
    device_name: Optional[str] = None,
    device_role: Optional[str] = None,
) -> int:
    """Idempotently persist the core rollup for each session dict.

    Keyed by ``(device_id, agent, id)``: a session that grows between scans
    overwrites its row (never duplicates), and two devices that share an
    ``(agent, id)`` keep separate rows. ``first_ts`` / ``first_seen_at`` are
    preserved across upserts; ``last_*`` and the token/cost columns track the
    freshest data. ``source_present`` is (re)set to 1 because we just saw it.

    ``device_id`` defaults to the local device (a local scan). The sync ingest
    path passes the remote device's id + ``source_origin='remote_sync'`` and its
    name/role. Returns the number of rows written. Never raises — a store failure
    must not break the scan that called it."""
    valid = [r for r in rows if r.get("id") and r.get("agent")]
    if not valid:
        return 0
    if device_id is None:
        device_id, dname, drole = _local_identity()
        device_name = device_name or dname
        device_role = device_role or drole
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    try:
        con = _connect()
        try:
            for r in valid:
                tok = r.get("tokens") or {}
                ts = _to_utc_iso(r.get("timestamp"))
                con.execute(
                    """
                    INSERT INTO sessions (
                        device_id, device_name, device_role, source_origin,
                        agent, id, project, model, provider, endpoint, billing_mode,
                        first_ts, last_ts, input, output, cached, total, cost,
                        tok_per_sec, ecosystem_json, first_seen_at, last_seen_at,
                        source_present
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(device_id, agent, id) DO UPDATE SET
                        device_name=excluded.device_name,
                        device_role=excluded.device_role,
                        source_origin=excluded.source_origin,
                        project=excluded.project,
                        model=excluded.model,
                        provider=excluded.provider,
                        endpoint=excluded.endpoint,
                        billing_mode=excluded.billing_mode,
                        first_ts=MIN(sessions.first_ts, excluded.first_ts),
                        last_ts=MAX(sessions.last_ts, excluded.last_ts),
                        input=excluded.input,
                        output=excluded.output,
                        cached=excluded.cached,
                        total=excluded.total,
                        cost=excluded.cost,
                        tok_per_sec=excluded.tok_per_sec,
                        ecosystem_json=excluded.ecosystem_json,
                        last_seen_at=excluded.last_seen_at,
                        source_present=1
                    """,
                    (
                        device_id, device_name, device_role, source_origin,
                        r.get("agent"), r.get("id"), r.get("project"), r.get("model"),
                        r.get("provider"), r.get("endpoint"), r.get("billing_mode"),
                        ts, ts,
                        int(tok.get("input", 0) or 0), int(tok.get("output", 0) or 0),
                        int(tok.get("cached", 0) or 0), int(tok.get("total", 0) or 0),
                        float(r.get("cost", 0.0) or 0.0),
                        r.get("tok_per_sec"),
                        _ecosystem_blob(r), now, now,
                    ),
                )
                written += 1
            con.commit()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 — store must never break the scan
        _log.exception("history upsert failed: %s", e)
    return written


def mark_absent(seen_keys: Set[Tuple[str, str]], device_id: Optional[str] = None) -> None:
    """Flag this device's rows whose ``(agent, id)`` was NOT in the latest scan as
    no longer on disk (``source_present=0``). Scoped to the local device only — the
    hub must never flag a *collector's* rows absent just because it didn't scan
    them itself (the collector reports its own absences). Never deletes — the
    rollup is what survives agent pruning."""
    if device_id is None:
        device_id, _, _ = _local_identity()
    try:
        con = _connect()
        try:
            present = con.execute(
                "SELECT agent, id FROM sessions WHERE source_present=1 AND device_id=?",
                (device_id,),
            ).fetchall()
            gone = [(a, i) for (a, i) in ((r["agent"], r["id"]) for r in present)
                    if (a, i) not in seen_keys]
            if gone:
                con.executemany(
                    "UPDATE sessions SET source_present=0 WHERE device_id=? AND agent=? AND id=?",
                    [(device_id, a, i) for (a, i) in gone],
                )
                con.commit()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history mark_absent failed: %s", e)


# ── device registry ──────────────────────────────────────────────────────────

def register_device(
    device_id: str,
    device_name: Optional[str] = None,
    device_role: Optional[str] = None,
    source_origin: str = "remote_sync",
    last_seen_at: Optional[str] = None,
) -> None:
    """Record/refresh a known device (called from /devices/register and on every
    successful sync). Idempotent. Never raises."""
    if not device_id:
        return
    last_seen_at = last_seen_at or datetime.now(timezone.utc).isoformat()
    try:
        con = _connect()
        try:
            con.execute(
                """INSERT INTO devices (device_id, device_name, device_role, source_origin, last_seen_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET
                       device_name=COALESCE(excluded.device_name, devices.device_name),
                       device_role=COALESCE(excluded.device_role, devices.device_role),
                       source_origin=excluded.source_origin,
                       last_seen_at=excluded.last_seen_at""",
                (device_id, device_name, device_role, source_origin, last_seen_at),
            )
            con.commit()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history register_device failed: %s", e)


def list_devices() -> List[Dict[str, Any]]:
    """All known devices: the explicit registry merged with any device that has
    rows in ``sessions`` (so a device shows up the moment it syncs, even before an
    explicit register). Each entry carries its freshest ``last_seen_at`` and a
    ``session_count``. Never raises."""
    by_id: Dict[str, Dict[str, Any]] = {}
    try:
        con = _connect()
        try:
            for r in con.execute("SELECT * FROM devices"):
                by_id[r["device_id"]] = {
                    "device_id": r["device_id"],
                    "device_name": r["device_name"],
                    "device_role": r["device_role"],
                    "source_origin": r["source_origin"],
                    "last_seen_at": r["last_seen_at"],
                    "session_count": 0,
                }
            for r in con.execute(
                """SELECT device_id,
                          MAX(device_name) AS device_name,
                          MAX(device_role) AS device_role,
                          MAX(source_origin) AS source_origin,
                          MAX(last_seen_at) AS last_seen_at,
                          COUNT(*) AS n
                   FROM sessions GROUP BY device_id"""
            ):
                cur = by_id.setdefault(r["device_id"], {
                    "device_id": r["device_id"],
                    "device_name": r["device_name"],
                    "device_role": r["device_role"],
                    "source_origin": r["source_origin"],
                    "last_seen_at": r["last_seen_at"],
                    "session_count": 0,
                })
                cur["session_count"] = r["n"] or 0
                # Prefer the most recent activity timestamp seen anywhere.
                if r["last_seen_at"] and (not cur.get("last_seen_at") or r["last_seen_at"] > cur["last_seen_at"]):
                    cur["last_seen_at"] = r["last_seen_at"]
                cur["device_name"] = cur.get("device_name") or r["device_name"]
                cur["device_role"] = cur.get("device_role") or r["device_role"]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history list_devices failed: %s", e)
    return list(by_id.values())


# ── read path ────────────────────────────────────────────────────────────────

def _rehydrate(r: sqlite3.Row) -> Dict[str, Any]:
    """Turn a stored row back into a live-session-shaped dict."""
    try:
        ts = datetime.fromisoformat(r["last_ts"]) if r["last_ts"] else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)
    out: Dict[str, Any] = {
        "id": r["id"],
        "agent": r["agent"],
        "project": r["project"],
        "model": r["model"],
        "provider": r["provider"],
        "endpoint": r["endpoint"],
        "billing_mode": r["billing_mode"],
        "timestamp": ts,
        "tokens": {
            "input": r["input"], "output": r["output"],
            "cached": r["cached"], "total": r["total"],
        },
        "cost": r["cost"],
        "tok_per_sec": r["tok_per_sec"],
        "source_present": bool(r["source_present"]),
        "transcript_archived": bool(r["transcript_archived"]),
        "summary_present": bool(r["summary_present"]),
        "device_id": r["device_id"],
        "device_name": r["device_name"],
        "device_role": r["device_role"],
        "source_origin": r["source_origin"],
        "last_seen_at": r["last_seen_at"],
        "from_history": True,
    }
    if r["ecosystem_json"]:
        try:
            eco = json.loads(r["ecosystem_json"])
            for k in _ECOSYSTEM_KEYS:
                if k in eco:
                    out[k] = eco[k]
        except (ValueError, TypeError):
            pass
    return out


def query(
    from_: Optional[str] = None,
    to: Optional[str] = None,
    agents: Optional[Iterable[str]] = None,
    models: Optional[Iterable[str]] = None,
    projects: Optional[Iterable[str]] = None,
    device_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Return stored sessions as live-session-shaped dicts.

    ``from_`` / ``to`` are UTC ISO bounds compared against ``last_ts`` in SQL
    (indexed). ``agents`` / ``models`` / ``projects`` / ``device_ids`` are each an
    optional allow-list; empty/omitted means "no filter" (i.e. All devices). The
    ``device`` UI filter maps: local → [local_id], all → None, <id> → [id]. Never
    raises."""
    where: List[str] = []
    params: List[Any] = []
    if from_:
        where.append("last_ts >= ?"); params.append(from_)
    if to:
        where.append("last_ts <= ?"); params.append(to)
    for col, vals in (
        ("agent", agents), ("model", models), ("project", projects), ("device_id", device_ids),
    ):
        vals = [v for v in (vals or []) if v]
        if vals:
            where.append(f"{col} IN ({','.join('?' * len(vals))})")
            params.extend(vals)
    sql = "SELECT * FROM sessions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    try:
        con = _connect()
        try:
            return [_rehydrate(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history query failed: %s", e)
        return []


def query_since(
    since: Optional[str] = None,
    exclude_device_id: Optional[str] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    """Incremental pull feed for bidirectional sync (ADR-0004).

    Returns stored sessions as live-session-shaped dicts whose ``last_seen_at`` is
    strictly greater than ``since`` (or all of them when ``since`` is None),
    excluding any row belonging to ``exclude_device_id`` (a peer never needs its
    own rows pulled back), ordered by ``last_seen_at`` ascending and capped at
    ``limit``. Ordering by the write-time watermark lets the caller advance its
    cursor monotonically and resume incrementally across restarts. Never raises."""
    where: List[str] = []
    params: List[Any] = []
    if since:
        where.append("last_seen_at > ?"); params.append(since)
    if exclude_device_id:
        where.append("device_id != ?"); params.append(exclude_device_id)
    sql = "SELECT * FROM sessions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY last_seen_at ASC LIMIT ?"
    params.append(int(limit) if limit and limit > 0 else 5000)
    try:
        con = _connect()
        try:
            return [_rehydrate(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history query_since failed: %s", e)
        return []


def coverage(device_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Earliest stored date + per-agent present/pruned counts, for the UI's
    data-availability notice. Optionally scoped to a device allow-list."""
    out: Dict[str, Any] = {"earliest": None, "by_agent": {}, "total_sessions": 0}
    dev = [d for d in (device_ids or []) if d]
    dev_clause = f" WHERE device_id IN ({','.join('?' * len(dev))})" if dev else ""
    try:
        con = _connect()
        try:
            row = con.execute(
                "SELECT MIN(first_ts) AS e, COUNT(*) AS n FROM sessions" + dev_clause, dev
            ).fetchone()
            out["earliest"] = row["e"]
            out["total_sessions"] = row["n"] or 0
            for r in con.execute(
                """SELECT agent,
                          SUM(source_present) AS present,
                          SUM(CASE WHEN source_present=0 THEN 1 ELSE 0 END) AS pruned,
                          SUM(summary_present) AS summarized
                   FROM sessions""" + dev_clause + " GROUP BY agent", dev
            ).fetchall():
                out["by_agent"][r["agent"]] = {
                    "present": r["present"] or 0,
                    "pruned": r["pruned"] or 0,
                    "summarized": r["summarized"] or 0,
                }
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history coverage failed: %s", e)
    return out


def storage_stats() -> Dict[str, Any]:
    """Row counts + bytes per tier per agent, for the Settings storage readout."""
    out: Dict[str, Any] = {"by_agent": {}, "transcript_bytes": 0, "total_sessions": 0}

    def _agent_row(a: str) -> Dict[str, Any]:
        return out["by_agent"].setdefault(
            a, {"sessions": 0, "transcripts": 0, "transcript_bytes": 0, "summaries": 0}
        )
    try:
        con = _connect()
        try:
            for r in con.execute("SELECT agent, COUNT(*) AS n FROM sessions GROUP BY agent"):
                _agent_row(r["agent"])["sessions"] = r["n"]
                out["total_sessions"] += r["n"]
            for r in con.execute(
                "SELECT agent, COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b FROM transcripts GROUP BY agent"
            ):
                row = _agent_row(r["agent"])
                row["transcripts"] = r["n"]; row["transcript_bytes"] = r["b"]
                out["transcript_bytes"] += r["b"]
            for r in con.execute("SELECT agent, COUNT(*) AS n FROM summaries GROUP BY agent"):
                _agent_row(r["agent"])["summaries"] = r["n"]
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history storage_stats failed: %s", e)
    return out


# ── tier 2: transcript archive ───────────────────────────────────────────────
# Transcripts/summaries are local-only by construction (we only ever archive the
# local machine's claude/codex transcripts; remote rollups carry none). The
# helpers therefore default to the local device_id, keeping their callers in
# main.py unchanged while still scoping rows correctly under the widened PK.

def put_transcript(agent: str, id: str, text: str, device_id: Optional[str] = None) -> int:
    """Archive (or replace) a compressed transcript blob. Returns stored bytes."""
    if device_id is None:
        device_id, _, _ = _local_identity()
    blob = zlib.compress(text.encode("utf-8", errors="replace"))
    now = datetime.now(timezone.utc).isoformat()
    try:
        con = _connect()
        try:
            con.execute(
                """INSERT INTO transcripts (device_id, agent, id, blob, bytes, archived_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(device_id, agent, id) DO UPDATE SET
                       blob=excluded.blob, bytes=excluded.bytes, archived_at=excluded.archived_at""",
                (device_id, agent, id, blob, len(blob), now),
            )
            con.execute(
                "UPDATE sessions SET transcript_archived=1 WHERE device_id=? AND agent=? AND id=?",
                (device_id, agent, id),
            )
            con.commit()
        finally:
            con.close()
        return len(blob)
    except Exception as e:  # noqa: BLE001
        _log.exception("history put_transcript failed: %s", e)
        return 0


def get_transcript(agent: str, id: str, device_id: Optional[str] = None) -> Optional[str]:
    if device_id is None:
        device_id, _, _ = _local_identity()
    try:
        con = _connect()
        try:
            r = con.execute(
                "SELECT blob FROM transcripts WHERE device_id=? AND agent=? AND id=?",
                (device_id, agent, id),
            ).fetchone()
        finally:
            con.close()
        if r and r["blob"] is not None:
            return zlib.decompress(r["blob"]).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        _log.exception("history get_transcript failed: %s", e)
    return None


def delete_transcripts(agent: Optional[str] = None, older_than_days: Optional[int] = None) -> int:
    """Purge tier-2 transcript blobs (freeing space) while leaving the core
    rollup and any summaries intact. Optionally scoped by agent and/or age.
    Returns the number of blobs deleted."""
    where: List[str] = []
    params: List[Any] = []
    if agent:
        where.append("agent=?"); params.append(agent)
    if older_than_days is not None:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        where.append("archived_at < ?"); params.append(cutoff)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        con = _connect()
        try:
            doomed = con.execute(
                "SELECT device_id, agent, id FROM transcripts" + clause, params
            ).fetchall()
            con.execute("DELETE FROM transcripts" + clause, params)
            for r in doomed:
                con.execute(
                    "UPDATE sessions SET transcript_archived=0 WHERE device_id=? AND agent=? AND id=?",
                    (r["device_id"], r["agent"], r["id"]),
                )
            con.commit()
            return len(doomed)
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history delete_transcripts failed: %s", e)
        return 0


# ── tier 3: summaries ────────────────────────────────────────────────────────

def put_summary(agent: str, id: str, summary: str, device_id: Optional[str] = None) -> None:
    """Persist a generated summary; survives even after the transcript is gone."""
    if device_id is None:
        device_id, _, _ = _local_identity()
    now = datetime.now(timezone.utc).isoformat()
    try:
        con = _connect()
        try:
            con.execute(
                """INSERT INTO summaries (device_id, agent, id, summary, created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(device_id, agent, id) DO UPDATE SET summary=excluded.summary, created_at=excluded.created_at""",
                (device_id, agent, id, summary, now),
            )
            con.execute(
                "UPDATE sessions SET summary_present=1 WHERE device_id=? AND agent=? AND id=?",
                (device_id, agent, id),
            )
            con.commit()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        _log.exception("history put_summary failed: %s", e)


def get_summary(agent: str, id: str, device_id: Optional[str] = None) -> Optional[str]:
    if device_id is None:
        device_id, _, _ = _local_identity()
    try:
        con = _connect()
        try:
            r = con.execute(
                "SELECT summary FROM summaries WHERE device_id=? AND agent=? AND id=?",
                (device_id, agent, id),
            ).fetchone()
        finally:
            con.close()
        return r["summary"] if r else None
    except Exception as e:  # noqa: BLE001
        _log.exception("history get_summary failed: %s", e)
        return None
