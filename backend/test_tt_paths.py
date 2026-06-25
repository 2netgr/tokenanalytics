"""Tests for the configurable data directory (discussion #27) + the
TokenTelemetry → TokenAnalytics rename migration (ADR-0004).

By default TokenAnalytics stores config + state in ``~/.tokenanalytics``. Users
who want it elsewhere can set ``TOKENANALYTICS_DATA_DIR`` to point anywhere, or
use the ``TOKENANALYTICS_HOME`` convention (which still appends
``.tokenanalytics``). The pre-rename ``TOKENTELEMETRY_DATA_DIR`` /
``TOKENTELEMETRY_HOME`` variables are honoured as a fallback, and an existing
``.tokentelemetry`` directory is adopted transparently when no
``.tokenanalytics`` exists yet — so upgrading users never lose their store.

These tests pin the resolution precedence and the migration fallback. They drive
the home-relative cases through a temp ``$HOME`` so the result never depends on
the developer's real home directory.

No pytest in the venv — run directly:  python backend/test_tt_paths.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import tt_paths  # noqa: E402


# Every env var the resolver consults — saved/restored around each case so tests
# can't leak into one another (or into the developer's real shell environment).
_VARS = (
    "TOKENANALYTICS_DATA_DIR", "TOKENANALYTICS_HOME",
    "TOKENTELEMETRY_DATA_DIR", "TOKENTELEMETRY_HOME",
    "HOME",
)


def _clear_brand_env() -> None:
    for v in _VARS:
        if v != "HOME":
            os.environ.pop(v, None)


def _restore(saved: dict) -> None:
    for v in _VARS:
        os.environ.pop(v, None)
        if saved.get(v) is not None:
            os.environ[v] = saved[v]


def _case(fn):
    """Run ``fn(home)`` with a clean brand env and a fresh temp ``$HOME``."""
    saved = {v: os.environ.get(v) for v in _VARS}
    try:
        _clear_brand_env()
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            fn(Path(home))
    finally:
        _restore(saved)


# ── new-brand defaults ───────────────────────────────────────────────────────

def test_default_is_home_dot_tokenanalytics():
    def body(home):
        assert tt_paths.data_dir() == home / ".tokenanalytics"
    _case(body)


def test_home_override_appends_new_dirname():
    def body(_home):
        with tempfile.TemporaryDirectory() as h:
            os.environ["TOKENANALYTICS_HOME"] = h
            assert tt_paths.data_dir() == Path(h) / ".tokenanalytics"
    _case(body)


def test_data_dir_override_is_verbatim():
    def body(_home):
        os.environ["TOKENANALYTICS_DATA_DIR"] = "/mnt/d/ta-data"
        assert tt_paths.data_dir() == Path("/mnt/d/ta-data")  # no suffix appended
    _case(body)


def test_data_dir_wins_over_home():
    def body(_home):
        os.environ["TOKENANALYTICS_HOME"] = "/tmp/myhome"
        os.environ["TOKENANALYTICS_DATA_DIR"] = "/mnt/d/ta"
        assert tt_paths.data_dir() == Path("/mnt/d/ta")
    _case(body)


def test_tilde_is_expanded():
    def body(home):
        os.environ["TOKENANALYTICS_DATA_DIR"] = "~/custom-ta"
        assert tt_paths.data_dir() == home / "custom-ta"
    _case(body)


def test_blank_values_fall_through():
    # Empty/whitespace env vars must be treated as unset, not produce "/" or
    # "./.tokenanalytics".
    def body(home):
        os.environ["TOKENANALYTICS_DATA_DIR"] = "   "
        os.environ["TOKENANALYTICS_HOME"] = ""
        assert tt_paths.data_dir() == home / ".tokenanalytics"
    _case(body)


# ── rename migration / backward compatibility ────────────────────────────────

def test_legacy_dir_adopted_when_new_absent():
    """An upgrading user with ~/.tokentelemetry but no ~/.tokenanalytics keeps
    using their existing store in place — zero data movement."""
    def body(home):
        (home / ".tokentelemetry").mkdir()
        assert tt_paths.data_dir() == home / ".tokentelemetry"
    _case(body)


def test_new_dir_wins_when_both_exist():
    def body(home):
        (home / ".tokentelemetry").mkdir()
        (home / ".tokenanalytics").mkdir()
        assert tt_paths.data_dir() == home / ".tokenanalytics"
    _case(body)


def test_legacy_data_dir_env_still_honoured():
    def body(_home):
        os.environ["TOKENTELEMETRY_DATA_DIR"] = "/mnt/d/old-tt"
        assert tt_paths.data_dir() == Path("/mnt/d/old-tt")
    _case(body)


def test_new_env_wins_over_legacy_env():
    def body(_home):
        os.environ["TOKENTELEMETRY_DATA_DIR"] = "/mnt/d/old-tt"
        os.environ["TOKENANALYTICS_DATA_DIR"] = "/mnt/d/new-ta"
        assert tt_paths.data_dir() == Path("/mnt/d/new-ta")
    _case(body)


def test_legacy_home_adopts_existing_legacy_dir():
    """A user who relocated home via the old TOKENTELEMETRY_HOME and has a
    .tokentelemetry there keeps it after the rename."""
    def body(_home):
        with tempfile.TemporaryDirectory() as h:
            os.environ["TOKENTELEMETRY_HOME"] = h
            (Path(h) / ".tokentelemetry").mkdir()
            assert tt_paths.data_dir() == Path(h) / ".tokentelemetry"
    _case(body)


# ── shared-resolver invariant (the point of #27) ─────────────────────────────

def test_all_config_modules_follow_the_override():
    """One env var relocates *every* store, not some."""
    def body(_home):
        os.environ["TOKENANALYTICS_DATA_DIR"] = "/tmp/ta-relocated"
        root = Path("/tmp/ta-relocated")
        import billing_mode
        import power_config
        assert billing_mode._overrides_path() == root / "billing.json"
        assert power_config._config_path() == root / "power.json"
        import summarizers.base as base
        assert (tt_paths.data_dir() / "summarizer") == root / "summarizer"
        assert base.SUMMARIZER_CWD.name == "summarizer"
    _case(body)


def test_migrate_copies_legacy_to_new_keeping_original():
    def body(home):
        legacy = home / ".tokentelemetry"
        legacy.mkdir()
        (legacy / "billing.json").write_text("{}")
        import sqlite3
        sqlite3.connect(str(legacy / "history.db")).close()
        r = tt_paths.migrate_legacy_data_dir()
        assert r == home / ".tokenanalytics", r
        assert (home / ".tokenanalytics" / "billing.json").exists()
        assert (home / ".tokenanalytics" / "history.db").exists()
        assert (legacy / "billing.json").exists()  # original kept as backup
    _case(body)


def test_migrate_noop_when_new_already_exists():
    def body(home):
        (home / ".tokentelemetry").mkdir()
        (home / ".tokenanalytics").mkdir()
        assert tt_paths.migrate_legacy_data_dir() is None
    _case(body)


def test_migrate_noop_when_no_legacy():
    def body(_home):
        assert tt_paths.migrate_legacy_data_dir() is None
    _case(body)


def test_migrate_respects_env_override():
    def body(home):
        (home / ".tokentelemetry").mkdir()
        os.environ["TOKENANALYTICS_DATA_DIR"] = "/tmp/pinned"
        assert tt_paths.migrate_legacy_data_dir() is None
        assert not (home / ".tokenanalytics").exists()
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
