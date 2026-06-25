"""``/analytics?include_delegated=true`` folds subagent usage into the totals.

Each session keeps its subagent (delegated) usage in ``tokens["delegated_*"]``,
separate from the headline ``tokens["total"]`` by design. The analytics endpoint
gained an ``include_delegated`` flag (default False = unchanged) that adds those
delegated tokens into the aggregated input/output/cached/total and adds the
delegated cost to the cost contribution.

We inject one local session carrying delegated tokens + a delegated_cost via the
live-scan source (``get_sessions_cached``) and assert that flipping the flag
raises that session's reported total and cost, while the default call leaves the
headline numbers at their non-delegated values.

Needs fastapi + httpx (run under the venv):
    backend/venv/bin/python -m pytest backend/test_analytics_delegated.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="ta-deleg-test-")

try:
    from fastapi.testclient import TestClient
    import main
    _client = TestClient(main.app)
    _HAVE = True
    _ERR = None
except Exception as e:  # noqa: BLE001
    _HAVE = False
    _ERR = e

pytestmark = pytest.mark.skipif(not _HAVE, reason="fastapi/httpx not installed")

_SENTINEL_MODEL = "ta-deleg-test-model"

# Headline tokens vs. the delegated (subagent) bucket kept apart from them.
_BASE = {"input": 1000, "output": 500, "cached": 0, "total": 1500}
_DELEG_IN, _DELEG_OUT, _DELEG_CACHED = 4000, 2000, 0
_DELEG_TOTAL = _DELEG_IN + _DELEG_OUT + _DELEG_CACHED
_DELEG_COST = 0.25
_BASE_COST = 0.05

_SESSION = {
    "agent": "claude",
    "id": "ta-deleg-1",
    "project": "p",
    "model": _SENTINEL_MODEL,
    "timestamp": datetime.now(timezone.utc),
    "tokens": {
        **_BASE,
        "delegated_input": _DELEG_IN,
        "delegated_output": _DELEG_OUT,
        "delegated_cached": _DELEG_CACHED,
    },
    "cost": _BASE_COST,
    "delegated_cost": _DELEG_COST,
}


@pytest.fixture(autouse=True)
def _env_and_live_scan(monkeypatch):
    saved = os.environ.get("TOKENANALYTICS_DATA_DIR")
    os.environ["TOKENANALYTICS_DATA_DIR"] = _TMP
    os.environ.pop("TT_AUTH_TOKEN", None)

    async def _fake_sessions(fresh: bool = False):
        return [dict(_SESSION, tokens=dict(_SESSION["tokens"]))]

    monkeypatch.setattr(main, "get_sessions_cached", _fake_sessions)
    try:
        yield
    finally:
        os.environ.pop("TOKENANALYTICS_DATA_DIR", None)
        if saved is not None:
            os.environ["TOKENANALYTICS_DATA_DIR"] = saved


def _model_row(resp_json):
    return (resp_json.get("by_model") or {}).get(_SENTINEL_MODEL)


def test_default_excludes_delegated():
    row = _model_row(_client.get("/analytics").json())
    assert row is not None, "sentinel session missing from default analytics"
    # Headline numbers only — delegated tokens NOT folded in.
    assert row["input"] == _BASE["input"]
    assert row["output"] == _BASE["output"]
    assert row["total"] == _BASE["total"]
    assert abs(row["cost"] - _BASE_COST) < 1e-9


def test_include_delegated_raises_totals():
    default_resp = _client.get("/analytics").json()
    incl_resp = _client.get("/analytics?include_delegated=true").json()

    d_row = _model_row(default_resp)
    i_row = _model_row(incl_resp)
    assert d_row is not None and i_row is not None

    # Per-model: delegated tokens are now added on top.
    assert i_row["input"] == _BASE["input"] + _DELEG_IN
    assert i_row["output"] == _BASE["output"] + _DELEG_OUT
    assert i_row["total"] == _BASE["total"] + _DELEG_TOTAL
    assert i_row["total"] > d_row["total"]
    assert abs(i_row["cost"] - (_BASE_COST + _DELEG_COST)) < 1e-9
    assert i_row["cost"] > d_row["cost"]

    # Grand total likewise rises by exactly the delegated amount for this session.
    assert incl_resp["total"]["total"] == default_resp["total"]["total"] + _DELEG_TOTAL
    assert incl_resp["total"]["total"] > default_resp["total"]["total"]
    assert incl_resp["total"]["cost"] > default_resp["total"]["cost"]


if __name__ == "__main__":
    if not _HAVE:
        print(f"SKIP  test_analytics_delegated (fastapi/httpx not installed: {_ERR})")
        sys.exit(0)
    print("Run under pytest: backend/venv/bin/python -m pytest backend/test_analytics_delegated.py")
