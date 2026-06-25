"""Cursor token ESTIMATION (Composer pricing).

Cursor agent-transcripts carry NO usage/token fields, so the scanner used to
record cursor sessions at 0 tokens / $0.00. The scan now estimates tokens from
message text length (~4 chars/token), normalizes the Composer model alias to a
priceable id, and marks the result as an estimate.

This builds a minimal cursor transcript (a few user + assistant text lines, one
of which carries model ``composer-2.5-fast``), points CURSOR_DIR at the fixture
with every OTHER agent dir neutralised, runs the real scan path, and asserts the
session comes back with estimated input/output > 0, model ``composer-2.5``,
cost > 0, and ``tokens["estimated"]`` True.

Needs fastapi (run under the venv):
    backend/venv/bin/python -m pytest backend/test_cursor_estimation.py
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

try:
    import main  # noqa: E402
    _HAVE = True
except Exception as e:  # noqa: BLE001
    _HAVE = False
    _ERR = e

pytestmark = pytest.mark.skipif(not _HAVE, reason="fastapi not installed")

# Agent dir globals to neutralise so the scan only sees our cursor fixture.
_OTHER_DIRS = [
    "CLAUDE_DIR", "CODEX_DIR", "GEMINI_DIR", "QWEN_DIR", "VIBE_DIR", "OLLAMA_DIR",
    "HF_DIR", "OPENCODE_DB", "GROK_DIR", "COPILOT_CLI_DIR",
    "ANTIGRAVITY_EXT_DIR", "VSCODE_EXT_DIR", "CURSOR_STORAGE", "VSCODE_STORAGE",
]

_USER_TEXTS = ["please refactor the parser", "now add a test for the edge case"]
_ASSISTANT_TEXTS = [
    "Sure — I'll start by reading the parser module and mapping its callers.",
    "Done. I added a focused test covering the empty-input edge case.",
]


def _write_cursor_transcript(cursor_dir, slug, sid):
    tdir = cursor_dir / "projects" / slug / "agent-transcripts" / sid
    tdir.mkdir(parents=True)
    lines = []
    # user lines (content as a list of text-items, like real cursor transcripts)
    for t in _USER_TEXTS:
        lines.append(json.dumps({
            "role": "user",
            "message": {"content": [{"type": "text", "text": t}]},
        }))
    # assistant lines; one carries the Composer fast alias, one a bare string body
    lines.append(json.dumps({
        "role": "assistant",
        "message": {"model": "composer-2.5-fast",
                    "content": [{"type": "text", "text": _ASSISTANT_TEXTS[0]}]},
    }))
    lines.append(json.dumps({
        "role": "assistant",
        "message": {"content": _ASSISTANT_TEXTS[1]},
    }))
    (tdir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cursor_session_tokens_are_estimated_and_priced(monkeypatch, tmp_path):
    cursor = tmp_path / ".cursor"
    sid = "CURSORSESS1"
    _write_cursor_transcript(cursor, slug="-Users-me-proj", sid=sid)

    empty = tmp_path / "nonexistent"  # never created → other scanners skip
    monkeypatch.setattr(main, "CURSOR_DIR", cursor)
    for name in _OTHER_DIRS:
        if hasattr(main, name):
            monkeypatch.setattr(main, name, empty / name)

    sessions = main._scan_sessions_sync()
    rows = [s for s in sessions if s.get("id") == sid and s.get("agent") == "cursor"]
    assert rows, "cursor transcript was not returned by the scan"
    sess = rows[0]
    tok = sess["tokens"]

    exp_in = math.ceil(sum(len(t) for t in _USER_TEXTS) / 4)
    exp_out = math.ceil(sum(len(t) for t in _ASSISTANT_TEXTS) / 4)

    assert tok["input"] == exp_in > 0, f"input estimate wrong: {tok}"
    assert tok["output"] == exp_out > 0, f"output estimate wrong: {tok}"
    assert tok["cached"] == 0
    assert tok["total"] == exp_in + exp_out
    assert tok.get("estimated") is True, "estimate not flagged"
    assert sess["model"] == "composer-2.5", f"model not normalized: {sess['model']!r}"
    assert sess["cost"] > 0, "Composer pricing should make cost non-zero"


def test_cursor_model_defaults_when_absent(monkeypatch, tmp_path):
    # A transcript with no message.model at all → default composer-2.5.
    cursor = tmp_path / ".cursor"
    sid = "CURSORSESS2"
    tdir = cursor / "projects" / "-Users-me-proj2" / "agent-transcripts" / sid
    tdir.mkdir(parents=True)
    lines = [
        json.dumps({"role": "user", "message": {"content": "hello there"}}),
        json.dumps({"role": "assistant", "message": {"content": "hi, how can I help?"}}),
    ]
    (tdir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    empty = tmp_path / "nonexistent"
    monkeypatch.setattr(main, "CURSOR_DIR", cursor)
    for name in _OTHER_DIRS:
        if hasattr(main, name):
            monkeypatch.setattr(main, name, empty / name)

    sessions = main._scan_sessions_sync()
    rows = [s for s in sessions if s.get("id") == sid and s.get("agent") == "cursor"]
    assert rows, "cursor transcript was not returned by the scan"
    assert rows[0]["model"] == "composer-2.5"
    assert rows[0]["tokens"].get("estimated") is True
    assert rows[0]["cost"] > 0
