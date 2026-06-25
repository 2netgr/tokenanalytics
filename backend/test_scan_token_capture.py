"""Regression test: file-backed transcripts must always have their tokens parsed,
regardless of how many fileless history stubs exist (the claude/codex undercount bug).

Root cause (see _scan_sessions_sync): ~/.claude/history.jsonl injects one fileless
zero-token stub per known sessionId; a `[:100]` recency slice then ranked those
stubs ahead of real transcript files, so the real files fell outside the top 100
and their token-parse loop never ran — a 285M-token session was recorded as 0.

This test floods history.jsonl with 150 stubs newer than a real transcript, so the
real file lands at ~rank 151. Before the fix it is dropped (tokens=0); after the fix
(parse every file-backed transcript) its tokens are captured.

Needs fastapi (run under the venv):  backend/venv/bin/python -m pytest backend/test_scan_token_capture.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

try:
    import main  # noqa: E402
    _HAVE = True
except Exception as e:  # noqa: BLE001
    _HAVE = False
    _ERR = e

pytestmark = pytest.mark.skipif(not _HAVE, reason="fastapi not installed")

# Agent dir globals to neutralise so the scan only sees our claude fixture.
_OTHER_DIRS = [
    "CODEX_DIR", "GEMINI_DIR", "QWEN_DIR", "VIBE_DIR", "CURSOR_DIR", "OLLAMA_DIR",
    "HF_DIR", "OPENCODE_DB", "GROK_DIR", "COPILOT_CLI_DIR",
    "ANTIGRAVITY_EXT_DIR", "VSCODE_EXT_DIR",
]


def _write_transcript(path, turns, inp, outp, cr, model):
    lines = []
    for _ in range(turns):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {
                "model": model,
                "usage": {"input_tokens": inp, "output_tokens": outp,
                          "cache_read_input_tokens": cr},
                "content": [],
            },
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_file_backed_claude_tokens_survive_history_stub_flood(monkeypatch, tmp_path):
    claude = tmp_path / ".claude"
    proj = claude / "projects" / "myproj"
    proj.mkdir(parents=True)
    real = proj / "REALSESS.jsonl"
    # 3 assistant turns: input 3*1000, output 3*500, cached = high-water-mark 2000.
    _write_transcript(real, turns=3, inp=1000, outp=500, cr=2000, model="claude-opus-4-8")

    # Flood history.jsonl with 150 fileless stubs, all NEWER than the real file,
    # so the real transcript is ranked ~151 — past the old [:100] window.
    now_ms = int(time.time() * 1000)
    with open(claude / "history.jsonl", "w", encoding="utf-8") as f:
        for i in range(150):
            f.write(json.dumps({
                "sessionId": f"stub-{i}", "timestamp": now_ms + 10_000_000 + i,
                "project": "/x", "display": "d",
            }) + "\n")

    empty = tmp_path / "nonexistent"  # never created → other scanners skip
    monkeypatch.setattr(main, "CLAUDE_DIR", claude)
    for name in _OTHER_DIRS:
        if hasattr(main, name):
            monkeypatch.setattr(main, name, empty / name)

    sessions = main._scan_sessions_sync()
    rows = [s for s in sessions if s.get("id") == "REALSESS"]
    assert rows, "REALSESS transcript was not returned by the scan at all"
    tok = rows[0]["tokens"]
    # input 3000 + output 1500 + cached 2000 = 6500
    assert tok["total"] == 6500, (
        f"file-backed transcript lost its tokens (got {tok}, model={rows[0].get('model')}) "
        "— it was dropped by the recency cap before its tokens were parsed"
    )
    assert rows[0]["model"] == "claude-opus-4-8"

    # Fileless history.jsonl stubs (transcripts already pruned, no file on disk)
    # must NOT be fabricated into phantom sessions — they only inflate the count.
    stubs = [s for s in sessions if str(s.get("id", "")).startswith("stub-")]
    assert stubs == [], f"fileless history stubs leaked as phantom sessions: {len(stubs)}"
