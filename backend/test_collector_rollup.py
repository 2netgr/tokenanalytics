"""Regression: a sync/collector rollup built from a real live-scan session must
strip the tokens dict to the four integer counts, so it validates against the
hub's strict `tokens: Dict[str, int]` schema. Live token dicts also carry cost
(float), 'estimated' (bool), cache_creation, _cached_sum, delegated_* — pushing
those raised HTTP 422 on /sync/sessions until _to_rollup sanitised them.

Run under the venv:  backend/venv/bin/python -m pytest backend/test_collector_rollup.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

try:
    import collector  # noqa: E402
    import main  # noqa: E402  (for the hub's SessionRollupIn schema)
    _HAVE = True
except Exception as e:  # noqa: BLE001
    _HAVE = False
    _ERR = e

pytestmark = pytest.mark.skipif(not _HAVE, reason="fastapi not installed")


def test_to_rollup_tokens_are_int_only_and_validate():
    session = {
        "agent": "cursor", "id": "x", "model": "composer-2.5", "provider": None,
        "endpoint": None, "billing_mode": None,
        "timestamp": "2026-06-20T00:00:00+00:00",
        "cost": 0.17442600000000003,            # top-level cost (float) — fine
        "tok_per_sec": 12.5,
        "tokens": {                              # rich live-scan token dict
            "input": 10, "output": 5, "cached": 0, "total": 15,
            "cost": 0.1744, "estimated": True, "cache_creation": 3,
            "_cached_sum": 99, "delegated_input": 7,
        },
        "display": "junk", "plans": [], "mcp_tools": ["x"],
    }
    r = collector._to_rollup(session)
    assert set(r["tokens"].keys()) == {"input", "output", "cached", "total"}
    assert all(isinstance(v, int) for v in r["tokens"].values()), r["tokens"]
    # Must validate against the hub's strict push schema — i.e. no HTTP 422.
    main.SessionRollupIn(**r)
