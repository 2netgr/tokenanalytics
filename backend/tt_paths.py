"""Single source of truth for where TokenAnalytics stores its config + state.

By default everything lives in ``~/.tokenanalytics/``. Environment variables let
a user relocate it — handy for keeping the system drive clear, isolating dev-tool
state on a secondary drive, or pinning the path in tests:

  - ``TOKENANALYTICS_DATA_DIR``  Absolute override of the data directory itself.
        Used verbatim: set it to ``D:\\dev\\ta-data`` (or ``/mnt/data/ta``) and
        that exact folder becomes the store — no ``.tokenanalytics`` suffix is
        appended. Highest precedence. This is the knob most users want.
  - ``TOKENANALYTICS_HOME``      Override of the *home* directory; the usual
        ``.tokenanalytics`` subfolder is still appended underneath it.

Backward compatibility (the product was renamed from TokenTelemetry): the legacy
``TOKENTELEMETRY_DATA_DIR`` / ``TOKENTELEMETRY_HOME`` variables are still honoured
as a fallback, and — crucially — an existing ``~/.tokentelemetry`` directory is
adopted transparently when no ``~/.tokenanalytics`` exists yet. So an upgrading
user keeps reading and writing their existing history.db / billing.json in place;
nothing is moved, copied, or lost. Fresh installs get ``~/.tokenanalytics``.

Resolution is lazy — the environment is read on every call — so a process that
exports the variable before launching the backend gets the right path, and tests
can monkeypatch it per-case. The directory is never created here: callers create
it lazily on first write (see ``harness_config._ensure_dir`` and friends), so a
read never materialises an empty folder in the wrong place. The "does it exist"
checks below are pure stat() reads — they never create anything.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# The conventional folder name appended under the user's home (or under
# TOKENANALYTICS_HOME). Not appended when TOKENANALYTICS_DATA_DIR is used.
DEFAULT_DIRNAME = ".tokenanalytics"

# The pre-rename folder name. Adopted transparently when it exists and the new
# default does not, so upgrading users never lose their data dir.
LEGACY_DIRNAME = ".tokentelemetry"


def _env(*names: str) -> str:
    """First non-blank value among the given env var names, in order."""
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v
    return ""


def data_dir() -> Path:
    """Resolve the TokenAnalytics data directory.

    Precedence (first match wins):
      1. ``TOKENANALYTICS_DATA_DIR`` / legacy ``TOKENTELEMETRY_DATA_DIR`` — verbatim.
      2. ``TOKENANALYTICS_HOME`` / legacy ``TOKENTELEMETRY_HOME`` — ``<that>/.tokenanalytics``.
      3. ``~/.tokenanalytics`` — unless it is absent and a legacy
         ``~/.tokentelemetry`` exists, in which case the legacy dir is adopted.
    """
    explicit = _env("TOKENANALYTICS_DATA_DIR", "TOKENTELEMETRY_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()

    home = _env("TOKENANALYTICS_HOME", "TOKENTELEMETRY_HOME")
    base = Path(home).expanduser() if home else Path.home()

    new_dir = base / DEFAULT_DIRNAME
    legacy_dir = base / LEGACY_DIRNAME
    # Adopt the legacy dir in place only when the new one hasn't been created yet
    # and the old one really exists — keeps an upgrading user's data working with
    # zero filesystem mutation. A fresh base just gets the new default. Applied
    # under a custom *_HOME too, so a user who relocated their home keeps their
    # existing ``.tokentelemetry`` store after the rename.
    if not new_dir.exists() and legacy_dir.exists():
        return legacy_dir
    return new_dir


def migrate_legacy_data_dir() -> Optional[Path]:
    """One-time, side-effecting migration (TokenTelemetry → TokenAnalytics).

    If there is no ``~/.tokenanalytics`` yet but a legacy ``~/.tokentelemetry``
    exists — and the user hasn't pinned a path via the env vars — copy the legacy
    data into the new home so all state lives under the new brand. The legacy dir
    is left untouched as a backup; nothing is moved or deleted. Idempotent (only
    acts when the new dir is absent). Returns the new dir on a real migration, else
    None. Never raises — a failed migration must not block startup (the resolver
    still adopts the legacy dir, so the app keeps working off the old home).

    Call this ONCE at startup, before any data access. Unlike ``data_dir()`` this
    *does* touch the filesystem, so it is a deliberate, separate step.
    """
    # An explicit override means the user chose a path — never auto-migrate.
    if _env("TOKENANALYTICS_DATA_DIR", "TOKENTELEMETRY_DATA_DIR",
            "TOKENANALYTICS_HOME", "TOKENTELEMETRY_HOME"):
        return None
    base = Path.home()
    new_dir = base / DEFAULT_DIRNAME
    legacy = base / LEGACY_DIRNAME
    if new_dir.exists() or not legacy.exists():
        return None
    import shutil
    try:
        # Checkpoint any SQLite WAL in the legacy dir so the copy is consistent.
        import sqlite3
        for db in legacy.glob("*.db"):
            try:
                con = sqlite3.connect(str(db))
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.close()
            except Exception:
                pass
        shutil.copytree(legacy, new_dir)
        return new_dir
    except Exception:
        # Clean up a half-written copy so the resolver falls back to the legacy
        # dir cleanly on the next run.
        try:
            if new_dir.exists():
                shutil.rmtree(new_dir, ignore_errors=True)
        except Exception:
            pass
        return None
